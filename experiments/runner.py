"""Run one controlled experiment: baseline -> stress -> recovery.

The runner only *observes*: it feeds the normal MonitorEngine and records what the
detector says.  It never injects, forces or suppresses a diagnosis.

Timeline record (one per sample) contains the phase, key metrics and a compact copy of
every active diagnosis, so evaluation and reports can be recomputed offline.
"""
import json
import os
import platform
import signal
import time
from datetime import datetime

import psutil

from analyzer.metrics import dumps_json
from analyzer.validation import ConfigError
from engine import MonitorEngine
from .workloads import Workload, plan_experiment

KEY_METRICS = ["cpu_percent", "iowait_percent", "load1", "runq_wait_per_core", "procs_running",
               "ctx_switches_s", "psi_cpu_some_pct", "psi_cpu", "mem_percent", "mem_available_mb",
               "swap_in_s", "swap_out_s", "psi_mem_some_pct", "psi_mem_full_pct", "psi_mem",
               "disk_util", "disk_await_ms", "disk_queue", "disk_read_mbs", "disk_write_mbs",
               "psi_io_some_pct", "psi_io_full_pct", "psi_io"]


class Cancelled(Exception):
    pass


def compact_diag(a):
    resp = a.get("responsible") or {}
    return {"resource": a["resource"], "device": a.get("device"), "state": a["state"],
            "severity": a["severity"], "confidence": a["confidence"], "method": a["method"],
            "duration_s": a.get("duration_s"), "headline": a["diagnosis"],
            "likely_cause": a.get("likely_cause"), "recommendation": a.get("recommendation"),
            "responsible_verdict": resp.get("verdict"), "responsible_text": a.get("responsible_text"),
            "responsible_pids": [c["pid"] for c in resp.get("contributors", [])],
            "evidence": [{"name": e["name"], "cls": e["cls"], "status": e["status"], "detail": e["detail"]}
                         for e in a.get("evidence", [])],
            "contradicting": a.get("contradicting", []), "missing": a.get("missing", [])}


def environment():
    return {"python": platform.python_version(), "kernel": platform.release(),
            "cores": os.cpu_count(), "mem_total_mb": round(psutil.virtual_memory().total / 2**20),
            "psi_cpu": os.path.exists("/proc/pressure/cpu"), "psi_memory": os.path.exists("/proc/pressure/memory"),
            "psi_io": os.path.exists("/proc/pressure/io"), "schedstat": os.path.exists("/proc/schedstat"),
            "uid": os.getuid() if hasattr(os, "getuid") else None}


class ExperimentRunner:
    def __init__(self, kind, out_root="data/experiments", duration=30, baseline_s=20, recovery_s=30,
                 interval=1.0, window=5, required=3, workers=None, memory_pct=None, disk_mb=None,
                 disk_path=None, backend="auto", thresholds=None, cancel_file=None, progress=None,
                 engine_factory=None):
        self.plan = plan_experiment(kind, duration, workers, memory_pct, disk_mb, disk_path, backend,
                                    baseline_s, recovery_s)
        self.kind, self.duration, self.baseline_s, self.recovery_s = kind, duration, baseline_s, recovery_s
        self.interval, self.window, self.required, self.thresholds = interval, window, required, thresholds
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{kind}"
        self.out_dir = os.path.join(out_root, self.run_id)
        self.cancel_file = cancel_file or os.path.join(out_root, "CANCEL")
        self.progress = progress or (lambda *_: None)
        self.engine_factory = engine_factory
        self.workload = None
        self.timeline = []
        self._cancel = False

    # -------------------------------------------------------------- helpers
    def _write_state(self, status, phase=None, t=None):
        """Small status file the dashboard polls (atomic; never blocks the experiment)."""
        try:
            tmp = os.path.join(self.out_dir, "state.json.tmp")
            with open(tmp, "w") as f:
                f.write(dumps_json({"run_id": self.run_id, "kind": self.kind, "status": status, "phase": phase,
                                    "t": t, "total_s": self.baseline_s + self.duration, "updated": time.time(),
                                    "out_dir": self.out_dir}))
            os.replace(tmp, os.path.join(self.out_dir, "state.json"))
        except OSError:
            pass

    def _check_cancel(self):
        if self._cancel or os.path.exists(self.cancel_file):
            raise Cancelled()

    def _make_engine(self):
        if self.engine_factory:
            return self.engine_factory()
        n_base = max(1, int(self.baseline_s / self.interval))
        # Short experiment baseline: warm up on ~70% of the baseline phase.  Static thresholds stay
        # primary; the baseline is frozen once stress starts so stress is never learned as normal.
        return MonitorEngine(self.interval, self.window, self.required, self.thresholds,
                             baseline={"min_samples": max(5, min(60, int(n_base * 0.7))), "window": 300,
                                       "adopt_after": 0})

    def _tick(self, engine, phase, t0, last):
        now = time.monotonic()
        dt = max(now - last, 1e-3)
        s, alerts, _ = engine.step(dt)
        rec = {"t": round(time.time() - t0, 2), "phase": phase,
               "metrics": {k: s.get(k) for k in KEY_METRICS},
               "status": dict(engine.detector.status),
               "diagnoses": [compact_diag(a) for a in alerts if a["resource"] != "Process"],
               "workload_running": bool(self.workload and self.workload.running())}
        if self.workload:
            self.workload.poll_pids()
        self.timeline.append(rec)
        self._write_state("running", phase, rec["t"])
        self.progress(phase, rec)
        return now

    def _run_phase(self, engine, phase, seconds, t0, last, until=None):
        end = time.monotonic() + seconds
        nxt = time.monotonic()
        while time.monotonic() < end:
            self._check_cancel()
            nxt += self.interval
            time.sleep(max(0.0, nxt - time.monotonic()))
            last = self._tick(engine, phase, t0, last)
            if until and until(self.timeline[-1]):
                break
        return last

    # ------------------------------------------------------------------ run
    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        self._clear_cancel()                          # stale cancel request from an earlier run
        old = {sig: signal.signal(sig, self._on_signal) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            return self._run()
        finally:
            for sig, h in old.items():
                signal.signal(sig, h)

    def _clear_cancel(self):
        self._cancel = False
        if os.path.exists(self.cancel_file):
            os.remove(self.cancel_file)

    def _run(self):
        status, marks = "completed", {"baseline_start": 0.0}
        t0 = time.time()
        engine = self._make_engine()
        self.workload = Workload(self.plan) if self.plan["backend"] != "none" else None
        try:
            last = self._run_phase(engine, "baseline", self.baseline_s, t0, time.monotonic())
            if engine.baseline is not None:
                engine.baseline.frozen = True          # never learn stress as normal
            marks["stress_start"] = round(time.time() - t0, 2)
            if self.workload:
                self.workload.start()
            self._run_phase(engine, "stress", self.duration, t0, last)
        except Cancelled:
            status = "cancelled"
        finally:
            marks["stress_end"] = round(time.time() - t0, 2)
            if self.workload:
                self.workload.stop()                   # always: normal end, cancel, error or signal
        marks.setdefault("stress_start", marks["stress_end"])
        if status == "cancelled":
            self._clear_cancel()                       # workload is gone; still observe recovery.
        clear = {"n": 0}                               # A second cancel/Ctrl+C aborts this phase.

        def cleared(rec):                              # recovered: nothing active for `window` samples
            active = any(d["state"] in ("POTENTIAL", "CONFIRMED") for d in rec["diagnoses"])
            clear["n"] = 0 if active else clear["n"] + 1
            return clear["n"] >= self.window
        try:
            self._run_phase(engine, "recovery", self.recovery_s, t0, time.monotonic(), until=cleared)
        except Cancelled:
            status = "cancelled"
        leftovers = sorted(self.workload.leftover_pids()) if self.workload else []
        marks["end"] = round(time.time() - t0, 2)
        report = {"run_id": self.run_id, "kind": self.kind, "status": status, "plan": self.plan,
                  "params": {"duration": self.duration, "baseline_s": self.baseline_s,
                             "recovery_s": self.recovery_s, "interval": self.interval,
                             "window": self.window, "required": self.required},
                  "marks": marks, "environment": environment(),
                  "workload_pids": sorted(self.workload.seen_pids) if self.workload else [],
                  "leftover_pids": leftovers, "timeline": self.timeline}
        from .evaluation import evaluate
        report["evaluation"] = evaluate(report)
        self.save(report)
        self._write_state(status, "done", marks["end"])
        return report

    def _on_signal(self, signum, frame):
        self._cancel = True                            # picked up at the next tick -> clean shutdown

    def save(self, report):
        with open(os.path.join(self.out_dir, "report.json"), "w") as f:
            f.write(dumps_json(report, indent=1))
        from .report import write_markdown, write_chart
        write_markdown(report, os.path.join(self.out_dir, "report.md"))
        write_chart(report, os.path.join(self.out_dir, "timeline.png"))
