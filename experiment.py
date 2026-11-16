"""Controlled performance experiments.

    python experiment.py check                         # dependencies and permissions
    python experiment.py run cpu                       # deliberate CPU contention
    python experiment.py run memory --memory-pct 70    # bounded memory pressure
    python experiment.py run disk --disk-path /var/tmp # bounded disk I/O
    python experiment.py run cpu-benign                # negative control: busy CPU, nothing waiting
    python experiment.py run control                   # negative control: idle system
    python experiment.py suite --repeats 3             # all experiments, aggregate report
    python experiment.py report                        # aggregate all saved runs into suite.md
    python experiment.py cancel                        # ask a running experiment to stop cleanly

Experiments only start when you run them (an explicit --yes or interactive confirmation is
required).  Ctrl+C stops the workload and cleans up.  No root is needed.
"""
import argparse
import json
import os
import sys

from analyzer.metrics import dumps_json
from analyzer.validation import ConfigError, load_config_file
from experiments.evaluation import aggregate
from experiments.report import render_suite
from experiments.runner import ExperimentRunner
from experiments.workloads import cgroup_mem_headroom_mb, stress_ng_path

KINDS = ["cpu", "memory", "disk", "cpu-benign", "control"]


def common(sp):
    sp.add_argument("--duration", type=float, default=30, help="stress seconds (5-600, default 30)")
    sp.add_argument("--baseline", type=float, default=20, help="baseline seconds (5-600, default 20)")
    sp.add_argument("--recovery", type=float, default=30, help="max recovery seconds (5-900, default 30)")
    sp.add_argument("--interval", type=float, default=1.0, help="sampling seconds (0.5-300, default 1)")
    sp.add_argument("--workers", type=int, help="workload workers (bounded)")
    sp.add_argument("--memory-pct", type=float, help="percent of AVAILABLE RAM to use (5-90, default 80)")
    sp.add_argument("--disk-mb", type=int, help="MB written per disk worker (16-4096, default 256)")
    sp.add_argument("--disk-path", help="scratch directory on a real disk (default: system temp dir)")
    sp.add_argument("--backend", default="auto", choices=["auto", "stress-ng", "builtin"])
    sp.add_argument("--out", default=os.path.join("data", "experiments"))
    sp.add_argument("--config", help="JSON file with detector settings (window, required, thresholds)")
    sp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")


def confirm(plan, yes):
    print(f"\nExperiment: {plan['kind']}  backend={plan['backend']}  workers={plan.get('workers')}  "
          f"duration={plan['duration']}s")
    for n in plan["notes"] + plan["warnings"]:
        print(f"  - {n}")
    if yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing to start without confirmation (non-interactive). Re-run with --yes.", file=sys.stderr)
        return False
    return input("Start this experiment? [y/N] ").strip().lower() in ("y", "yes")


def make_runner(a, kind, out):
    cfg = load_config_file(a.config) if a.config else {}
    return ExperimentRunner(kind, out, a.duration, a.baseline, a.recovery, a.interval,
                            cfg.get("window", 5), cfg.get("required", 3), a.workers, a.memory_pct,
                            a.disk_mb, a.disk_path, a.backend, cfg.get("thresholds"),
                            progress=None)


def cmd_check(_):
    import psutil
    print("Experiment prerequisites")
    ng = stress_ng_path()
    print(f"  stress-ng : {ng or 'not installed (built-in Python workloads will be used; optional: sudo apt install stress-ng)'}")
    for r in ("cpu", "memory", "io"):
        ok = os.access(f"/proc/pressure/{r}", os.R_OK)
        print(f"  PSI {r:<7}: {'available' if ok else 'UNAVAILABLE (kernel < 4.20 or PSI disabled; confidence will be capped)'}")
    print(f"  schedstat : {'available' if os.path.exists('/proc/schedstat') else 'unavailable (run-queue falls back to procs_running)'}")
    print(f"  root      : {'yes' if os.geteuid() == 0 else 'no (fine: experiments never need root)'}")
    head = cgroup_mem_headroom_mb()
    print(f"  memory    : {psutil.virtual_memory().available / 2**20:.0f} MB available"
          + (f", cgroup headroom {head:.0f} MB" if head is not None else ""))
    return 0


def cmd_run(a):
    try:
        r = make_runner(a, a.kind, a.out)
        if not confirm(r.plan, a.yes):
            return 1

        def progress(phase, rec):
            act = [f"{d['resource']}:{d['state']}" for d in rec["diagnoses"]]
            print(f"  t={rec['t']:6.1f}s {phase:<8} cpu={rec['metrics']['cpu_percent'] or 0:5.1f}% "
                  f"mem={rec['metrics']['mem_percent'] or 0:5.1f}% {' '.join(act)}", flush=True)
        r.progress = progress
        os.makedirs(a.out, exist_ok=True)
        rep = r.run()
    except ConfigError as e:
        print(f"Cannot run experiment: {e}", file=sys.stderr)
        return 2
    print(f"\nVerdict: {rep['evaluation']['verdict']}   report: {r.out_dir}/report.md")
    return 0 if rep["evaluation"]["verdict"] in ("PASS",) else 3


def cmd_suite(a):
    try:
        kinds = a.kinds or KINDS
        reports = []
        for i in range(a.repeats):
            for k in kinds:
                r = make_runner(a, k, a.out)
                if not reports and i == 0 and k == kinds[0]:
                    print("Suite will run:", ", ".join(kinds), "x", a.repeats)
                    if not (a.yes or (sys.stdin.isatty() and input("Start? [y/N] ").lower().startswith("y"))):
                        print("Not started (use --yes).")
                        return 1
                print(f"\n=== {k} (repeat {i + 1}/{a.repeats}) ===", flush=True)
                reports.append(r.run())
                print(f"    verdict: {reports[-1]['evaluation']['verdict']}", flush=True)
    except ConfigError as e:
        print(f"Cannot run suite: {e}", file=sys.stderr)
        return 2
    agg = aggregate(reports)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "suite.json"), "w") as f:
        f.write(dumps_json(agg, indent=1))
    with open(os.path.join(a.out, "suite.md"), "w") as f:
        f.write(render_suite(agg, reports))
    print(f"\nSuite report: {os.path.join(a.out, 'suite.md')}")
    return 0


def cmd_report(a):
    """Aggregate every report.json found under --out into suite.md / suite.json."""
    reports = []
    for d in sorted(os.listdir(a.out)) if os.path.isdir(a.out) else []:
        p = os.path.join(a.out, d, "report.json")
        if os.path.exists(p):
            with open(p) as f:
                reports.append(json.load(f))
    if not reports:
        print(f"No experiment reports found in {a.out}", file=sys.stderr)
        return 1
    agg = aggregate(reports)
    with open(os.path.join(a.out, "suite.json"), "w") as f:
        f.write(dumps_json(agg, indent=1))
    with open(os.path.join(a.out, "suite.md"), "w") as f:
        f.write(render_suite(agg, reports))
    print(f"Aggregated {len(reports)} run(s) -> {os.path.join(a.out, 'suite.md')}")
    return 0


def cmd_cancel(a):
    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "CANCEL"), "w").close()
    print("Cancel requested; the running experiment stops its workload at the next sample.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    rp = sub.add_parser("run")
    rp.add_argument("kind", choices=KINDS)
    common(rp)
    sp = sub.add_parser("suite")
    sp.add_argument("--repeats", type=int, default=1)
    sp.add_argument("--kinds", nargs="+", choices=KINDS)
    common(sp)
    ap2 = sub.add_parser("report", help="aggregate all saved runs into suite.md")
    ap2.add_argument("--out", default=os.path.join("data", "experiments"))
    cp = sub.add_parser("cancel")
    cp.add_argument("--out", default=os.path.join("data", "experiments"))
    a = ap.parse_args(argv)
    if a.cmd == "suite" and not 1 <= a.repeats <= 20:
        print("--repeats must be between 1 and 20", file=sys.stderr)
        return 2
    return {"check": cmd_check, "run": cmd_run, "suite": cmd_suite, "cancel": cmd_cancel, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
