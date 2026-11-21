"""Resumable Campaign Automation Runner for Linux Bottleneck Detective V2.

Automates the execution of multi-repeat experiment campaigns across 5 categories
(control, cpu-benign, cpu, memory, disk) x 3 repeats = 15 experiments.

Key Features:
- Preserves all original project files and prior experiment results.
- Writes to a separate, dedicated campaign directory (default: data/campaign_runs).
- Checkpoints progress atomically in manifest.json after every experiment.
- Fully resumable: skips already-completed experiments on rerun/crash/quota expiration.
- Dry-run mode (--dry-run) to inspect the 15-run execution plan without running stress.
- Harmless test mode (--test) to validate runner lifecycle and manifest persistence safely.
- Explicit run mode (--run) to initiate the actual campaign.
- Single-instance protection lock preventing unsafe concurrent executions.
- Enforces strict safety bounds: non-root, conservative memory/disk/CPU, nice 10, PR_SET_PDEATHSIG.
- Automatic aggregation of all completed runs into suite.json and suite.md.
"""
import argparse
import copy
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

from analyzer.metrics import dumps_json
from analyzer.validation import ConfigError, load_config_file
from experiments.evaluation import aggregate
from experiments.report import render_suite
from experiments.runner import ExperimentRunner
from experiments.workloads import cgroup_mem_headroom_mb, stress_ng_path

log = logging.getLogger("campaign_runner")

CATEGORIES = ["control", "cpu-benign", "cpu", "memory", "disk"]
DEFAULT_CAMPAIGN_DIR = os.path.join("data", "campaign_runs")


class CampaignLock:
    """Single-instance protection mechanism for campaign execution."""

    def __init__(self, lock_path):
        self.lock_path = Path(lock_path)
        self.acquired = False

    def acquire(self):
        if self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                old_pid = data.get("pid")
                if old_pid and psutil.pid_exists(old_pid):
                    try:
                        p = psutil.Process(old_pid)
                        if p.status() != psutil.STATUS_ZOMBIE:
                            raise RuntimeError(
                                f"Another campaign instance is currently running (PID {old_pid}, started {data.get('started_at')}). "
                                "Refusing concurrent execution to protect campaign state."
                            )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (json.JSONDecodeError, OSError):
                pass
            # Stale lockfile
            try:
                self.lock_path.unlink()
            except OSError:
                pass

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump({
                "pid": os.getpid(),
                "started_at": datetime.now().isoformat()
            }, f)
        self.acquired = True

    def release(self):
        if self.acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                pass
            self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class CampaignManifest:
    """Persistent, atomic state tracker for experiment campaigns."""

    def __init__(self, campaign_dir, manifest_path=None):
        self.campaign_dir = Path(campaign_dir)
        self.manifest_path = Path(manifest_path) if manifest_path else self.campaign_dir / "manifest.json"
        self.data = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "campaign_dir": str(self.campaign_dir),
            "repeats": 3,
            "categories": CATEGORIES,
            "tasks": [],
            "summary": {"total": 0, "completed": 0, "failed": 0, "cancelled": 0, "pending": 0, "skipped": 0}
        }
        self.load()

    def load(self):
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
                    self._update_summary()
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Could not read existing manifest %s: %s; starting fresh", self.manifest_path, e)

    def save(self):
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = datetime.now().isoformat()
        self._update_summary()
        tmp_path = self.manifest_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(dumps_json(self.data, indent=2))
        tmp_path.replace(self.manifest_path)

    def _update_summary(self):
        counts = {"total": len(self.data["tasks"]), "completed": 0, "failed": 0, "cancelled": 0, "pending": 0, "skipped": 0}
        for t in self.data["tasks"]:
            st = t.get("status", "PENDING")
            if st == "COMPLETED":
                counts["completed"] += 1
            elif st == "FAILED":
                counts["failed"] += 1
            elif st == "CANCELLED":
                counts["cancelled"] += 1
            elif st == "SKIPPED":
                counts["skipped"] += 1
            else:
                counts["pending"] += 1
        self.data["summary"] = counts

    def init_tasks(self, categories, repeats, default_params):
        """Populate the 15 campaign tasks if manifest is empty or fresh."""
        if self.data["tasks"]:
            return  # preserve existing tasks
        tasks = []
        task_id = 1
        for rep in range(1, repeats + 1):
            for cat in categories:
                params = copy.deepcopy(default_params.get(cat, {}))
                tasks.append({
                    "id": f"task_{task_id:02d}_{cat}_rep{rep}",
                    "index": task_id,
                    "category": cat,
                    "repeat": rep,
                    "status": "PENDING",
                    "parameters": params,
                    "run_id": None,
                    "run_dir": None,
                    "verdict": None,
                    "started_at": None,
                    "completed_at": None,
                    "error": None
                })
                task_id += 1
        self.data["repeats"] = repeats
        self.data["categories"] = categories
        self.data["tasks"] = tasks
        self.save()

    def get_task(self, task_id):
        for t in self.data["tasks"]:
            if t["id"] == task_id:
                return t
        return None

    def mark_started(self, task_id, run_id, run_dir):
        t = self.get_task(task_id)
        if t:
            t["status"] = "IN_PROGRESS"
            t["run_id"] = run_id
            t["run_dir"] = str(run_dir)
            t["started_at"] = datetime.now().isoformat()
            self.save()

    def mark_completed(self, task_id, verdict, report_path=None):
        t = self.get_task(task_id)
        if t:
            t["status"] = "COMPLETED"
            t["verdict"] = verdict
            t["completed_at"] = datetime.now().isoformat()
            if report_path:
                t["report_path"] = str(report_path)
            self.save()

    def mark_cancelled(self, task_id, reason="Cancelled"):
        t = self.get_task(task_id)
        if t:
            t["status"] = "CANCELLED"
            t["error"] = str(reason)
            t["verdict"] = None
            t["completed_at"] = datetime.now().isoformat()
            self.save()

    def mark_failed(self, task_id, error_msg):
        t = self.get_task(task_id)
        if t:
            t["status"] = "FAILED"
            t["error"] = str(error_msg)
            t["completed_at"] = datetime.now().isoformat()
            self.save()

    def mark_skipped(self, task_id, reason="Skipped by user configuration"):
        t = self.get_task(task_id)
        if t:
            t["status"] = "SKIPPED"
            t["error"] = str(reason)
            self.save()


class CampaignRunner:
    """Manages the full lifecycle of an automated experiment campaign."""

    def __init__(self, campaign_dir=DEFAULT_CAMPAIGN_DIR, repeats=3, categories=None,
                 config_file=None, backend="builtin", dry_run=False, safe_mode=True):
        self.campaign_dir = Path(campaign_dir).resolve()
        self.repeats = repeats
        self.categories = categories or CATEGORIES
        self.config_file = config_file
        self.backend = backend
        self.dry_run = dry_run
        self.safe_mode = safe_mode
        self.interrupted = False
        self.manifest = CampaignManifest(self.campaign_dir)
        self._setup_signals()

    def _setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        print(f"\n[INTERRUPT] Signal {signum} received. Safely stopping current workload and saving manifest...")
        self.interrupted = True

    def default_workload_parameters(self):
        """Construct safe, non-aggressive workload parameters tailored for this host."""
        cores = os.cpu_count() or 1
        # In safe mode, we deliberately avoid 72 workers or 60% RAM
        # Safe CPU worker limits
        cpu_workers = min(cores, 8) if self.safe_mode else cores
        cpu_contention_workers = min(cores + 2, 12) if self.safe_mode else min(2 * cores, 64)

        return {
            "control": {
                "duration": 15.0 if self.safe_mode else 30.0,
                "baseline": 10.0 if self.safe_mode else 20.0,
                "recovery": 15.0 if self.safe_mode else 30.0,
                "interval": 1.0,
                "backend": self.backend,
                "workers": None
            },
            "cpu-benign": {
                "duration": 15.0 if self.safe_mode else 30.0,
                "baseline": 10.0 if self.safe_mode else 20.0,
                "recovery": 15.0 if self.safe_mode else 30.0,
                "interval": 1.0,
                "backend": self.backend,
                "workers": cpu_workers
            },
            "cpu": {
                "duration": 15.0 if self.safe_mode else 30.0,
                "baseline": 10.0 if self.safe_mode else 20.0,
                "recovery": 15.0 if self.safe_mode else 30.0,
                "interval": 1.0,
                "backend": self.backend,
                "workers": cpu_contention_workers
            },
            "memory": {
                "duration": 15.0 if self.safe_mode else 30.0,
                "baseline": 10.0 if self.safe_mode else 20.0,
                "recovery": 15.0 if self.safe_mode else 30.0,
                "interval": 1.0,
                "backend": self.backend,
                "memory_pct": 10.0 if self.safe_mode else 30.0,  # strictly conservative
                "disk_path": "/var/tmp"
            },
            "disk": {
                "duration": 15.0 if self.safe_mode else 30.0,
                "baseline": 10.0 if self.safe_mode else 20.0,
                "recovery": 15.0 if self.safe_mode else 30.0,
                "interval": 1.0,
                "backend": self.backend,
                "workers": 2,
                "disk_mb": 64 if self.safe_mode else 128,  # bounded small size
                "disk_path": "/var/tmp"
            }
        }

    def initialize_campaign(self):
        """Prepare campaign directory and manifest tasks."""
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        default_params = self.default_workload_parameters()
        self.manifest.init_tasks(self.categories, self.repeats, default_params)

    def print_plan(self):
        """Print the complete status and schedule of the campaign."""
        tasks = self.manifest.data["tasks"]
        summary = self.manifest.data["summary"]
        print("=" * 80)
        print(f" CAMPAIGN PLAN: {len(tasks)} Experiments ({self.repeats} repeats x {len(self.categories)} categories)")
        print(f" Output Directory : {self.campaign_dir}")
        print(f" Manifest Status  : Total={summary['total']} | Completed={summary['completed']} | "
              f"Cancelled={summary.get('cancelled', 0)} | Pending={summary['pending']} | Failed={summary['failed']}")
        print(f" Backend          : {self.backend} (no root/sudo)")
        print(f" Safe Mode        : {self.safe_mode}")
        print("=" * 80)
        print(f"{'#':<4} {'Task ID':<26} {'Category':<12} {'Rep':<4} {'Status':<12} {'Verdict':<8} {'Run ID'}")
        print("-" * 80)
        for t in tasks:
            print(f"{t['index']:<4} {t['id']:<26} {t['category']:<12} {t['repeat']:<4} "
                  f"{t['status']:<12} {str(t.get('verdict') or '-'):<8} {str(t.get('run_id') or '-')}")
        print("=" * 80)

    def is_task_effectively_completed(self, task):
        """Check if task is recorded as COMPLETED and its report artifact genuinely exists and is valid."""
        if task.get("status") != "COMPLETED":
            return False
        run_dir = task.get("run_dir")
        if not run_dir or not os.path.isdir(run_dir):
            return False
        report_json_path = os.path.join(run_dir, "report.json")
        if not os.path.isfile(report_json_path):
            return False
        try:
            with open(report_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Must be a completed run, not cancelled or partial
            if data.get("status") != "completed":
                return False
            if not data.get("timeline") or not data.get("evaluation"):
                return False
            if "verdict" not in data.get("evaluation", {}):
                return False
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def run_single_task(self, task):
        """Execute one experiment task with atomic checkpointing."""
        task_id = task["id"]
        cat = task["category"]
        params = task["parameters"]

        if self.is_task_effectively_completed(task):
            print(f"[{task['index']}/{len(self.manifest.data['tasks'])}] SKIP already completed: {task_id} (Verdict: {task['verdict']})")
            return task.get("verdict")

        print(f"\n>>> [{task['index']}/{len(self.manifest.data['tasks'])}] EXECUTING {task_id} ({cat}, rep {task['repeat']})")
        print(f"    Params: {params}")

        if self.dry_run:
            print(f"    [DRY RUN] Simulating {cat} execution without starting workloads...")
            time.sleep(0.02)
            self.manifest.mark_started(task_id, f"dryrun-{cat}-{task['repeat']}", str(self.campaign_dir / f"dryrun-{cat}-{task['repeat']}"))
            self.manifest.mark_completed(task_id, "PASS")
            return "PASS"

        cfg = load_config_file(self.config_file) if self.config_file else {}
        runner = ExperimentRunner(
            kind=cat,
            out_root=str(self.campaign_dir),
            duration=params.get("duration", 20.0),
            baseline_s=params.get("baseline", 10.0),
            recovery_s=params.get("recovery", 15.0),
            interval=params.get("interval", 1.0),
            window=cfg.get("window", 5),
            required=cfg.get("required", 3),
            workers=params.get("workers"),
            memory_pct=params.get("memory_pct"),
            disk_mb=params.get("disk_mb"),
            disk_path=params.get("disk_path"),
            backend=params.get("backend", self.backend),
            thresholds=cfg.get("thresholds")
        )

        self.manifest.mark_started(task_id, runner.run_id, runner.out_dir)

        def progress(phase, rec):
            acts = [f"{d['resource']}:{d['state']}" for d in rec["diagnoses"]]
            print(f"    t={rec['t']:5.1f}s phase={phase:<9} cpu={rec['metrics'].get('cpu_percent') or 0:4.1f}% "
                  f"mem={rec['metrics'].get('mem_percent') or 0:4.1f}% {' '.join(acts)}", flush=True)

        runner.progress = progress

        try:
            report = runner.run()
            # Check if experiment was cancelled
            if report.get("status") == "cancelled" or getattr(runner, "_cancel", False):
                self.interrupted = True
                self.manifest.mark_cancelled(task_id, reason="Experiment cancelled during execution")
                print(f"    [CANCELLED] {task_id} was stopped cleanly. Not marked completed.")
                return "CANCELLED"

            verdict = report.get("evaluation", {}).get("verdict", "FAIL")
            report_file = os.path.join(runner.out_dir, "report.json")
            self.manifest.mark_completed(task_id, verdict, report_file)
            print(f"    Result: {verdict} | Report: {runner.out_dir}/report.md")
            return verdict
        except (KeyboardInterrupt, SystemExit):
            self.interrupted = True
            self.manifest.mark_cancelled(task_id, reason="Interrupted by user signal")
            print(f"    [INTERRUPTED] {task_id} interrupted by signal. Not marked completed.")
            return "CANCELLED"
        except Exception as e:
            self.manifest.mark_failed(task_id, str(e))
            print(f"    ERROR running {task_id}: {e}", file=sys.stderr)
            return "ERROR"

    def execute_campaign(self):
        """Execute all pending tasks sequentially, respecting resumption and interruptions."""
        lock = CampaignLock(self.campaign_dir / "campaign.lock")
        with lock:
            self.initialize_campaign()
            self.print_plan()

            tasks = self.manifest.data["tasks"]
            for task in tasks:
                if self.interrupted:
                    print("\n[PAUSE] Campaign execution halted due to interrupt signal. Progress saved.")
                    break
                result = self.run_single_task(task)
                if result == "CANCELLED" or self.interrupted:
                    print("\n[PAUSE] Task was cancelled or interrupted. Stopping campaign execution without proceeding to next experiment.")
                    break

            self.generate_aggregate_report()

    def generate_aggregate_report(self):
        """Aggregate all completed reports in campaign_dir into suite.json and suite.md."""
        reports = []
        if not self.campaign_dir.exists():
            return

        for d in sorted(os.listdir(self.campaign_dir)):
            rep_path = self.campaign_dir / d / "report.json"
            if rep_path.exists():
                try:
                    with open(rep_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("status") == "completed":
                            reports.append(data)
                except Exception as e:
                    log.warning("Could not read report %s: %s", rep_path, e)

        if not reports:
            print("No valid completed experiment reports found to aggregate.")
            return

        agg = aggregate(reports)
        suite_json = self.campaign_dir / "suite.json"
        suite_md = self.campaign_dir / "suite.md"

        with open(suite_json, "w", encoding="utf-8") as f:
            f.write(dumps_json(agg, indent=2))
        with open(suite_md, "w", encoding="utf-8") as f:
            f.write(render_suite(agg, reports))

        print(f"\n[CAMPAIGN SUMMARY] Aggregated {len(reports)} completed runs.")
        print(f"  JSON : {suite_json}")
        print(f"  MD   : {suite_md}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--campaign-dir", default=DEFAULT_CAMPAIGN_DIR, help="Dedicated output directory for campaign")
    p.add_argument("--repeats", type=int, default=3, help="Number of repetitions per category (default: 3)")
    p.add_argument("--categories", nargs="+", choices=CATEGORIES, default=CATEGORIES, help="Categories to include")
    p.add_argument("--backend", choices=["builtin", "stress-ng", "auto"], default="builtin", help="Stress backend")
    p.add_argument("--config", help="Optional detector configuration JSON")
    p.add_argument("--reset", action="store_true", help="Reset existing manifest in campaign directory")

    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument("--run", action="store_true", help="Explicitly start actual campaign execution")
    mode_group.add_argument("--dry-run", action="store_true", help="Display plan and test manifest transitions without load")
    mode_group.add_argument("--test", action="store_true", help="Execute a single harmless test (5s control) to verify runner")

    args = p.parse_args(argv)

    if args.reset:
        mpath = Path(args.campaign_dir) / "manifest.json"
        if mpath.exists():
            mpath.unlink()
            print(f"Deleted existing manifest at {mpath}")

    runner = CampaignRunner(
        campaign_dir=args.campaign_dir,
        repeats=args.repeats,
        categories=args.categories,
        config_file=args.config,
        backend=args.backend,
        dry_run=args.dry_run,
        safe_mode=True
    )

    if args.test:
        print("[TEST MODE] Running harmless test: 1 short control experiment (5s baseline, 5s duration, 5s recovery)...")
        runner.initialize_campaign()
        test_task = {
            "id": "harmless_test_control",
            "index": 0,
            "category": "control",
            "repeat": 1,
            "status": "PENDING",
            "parameters": {"duration": 5.0, "baseline": 5.0, "recovery": 5.0, "interval": 1.0, "backend": runner.backend, "workers": None}
        }
        res = runner.run_single_task(test_task)
        print(f"[TEST MODE] Harmless test completed with result: {res}")
        return 0

    if args.dry_run:
        print("[DRY RUN MODE] Simulating all 15 campaign tasks and manifest checkpointing...")
        runner.execute_campaign()
        return 0

    if args.run:
        print("[RUN MODE] Starting full campaign execution...")
        runner.execute_campaign()
        return 0

    # Normal execution (no flags): display plan and exit without running experiments
    runner.initialize_campaign()
    runner.print_plan()
    print("\nNo execution flag specified. Campaign plan displayed.")
    print("Use --dry-run to simulate execution without workloads.")
    print("Use --test to run a single harmless control test.")
    print("Use --run to explicitly start the campaign.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
