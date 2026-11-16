"""Collector + detector loop. Run this first; the dashboard reads the files it writes.

    python main.py [--interval 2] [--duration 0] [--data-dir data]
                   [--window 5 --required 3] [--config config.json]
                   [--no-baseline] [--reset-baseline] [--baseline-min-samples 60] [--z-threshold 4]
"""
import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime

from analyzer.metrics import csv_value, dumps_json
from analyzer.validation import ConfigError, load_config_file
from engine import MonitorEngine

# Legacy columns first (unchanged order) so old tooling keeps working; V2 columns appended.
FIELDS = ["timestamp", "cpu_percent", "iowait_percent", "load1", "load5", "load15", "cores",
          "ctx_switches_s", "mem_percent", "mem_available_mb", "swap_percent", "swap_in_s",
          "swap_out_s", "disk_read_mbs", "disk_write_mbs", "disk_util", "disk_await_ms",
          "psi_cpu", "psi_mem", "psi_io", "top_cpu_name", "top_cpu_pid", "top_cpu_pct",
          "top_mem_name", "top_mem_pid", "top_mem_pct",
          # V2
          "ts", "procs_running", "runq_wait_per_core", "runnable_per_core",
          "psi_cpu_some_pct", "psi_mem_some_pct", "psi_io_some_pct",
          "psi_cpu_full", "psi_mem_full", "psi_io_full",
          "psi_mem_full_pct", "psi_io_full_pct", "psi_cpu_avg300", "psi_mem_avg300", "psi_io_avg300",
          "mem_cached_mb", "mem_reclaimable_mb", "mem_naive_used_percent", "major_faults_s",
          "disk_util_mean", "disk_queue", "busiest_disk", "cpu_state", "mem_state", "disk_state"]
ALERT_FIELDS = ["timestamp", "resource", "severity", "diagnosis", "recommendation",
                "state", "confidence", "method", "device", "duration_s", "likely_cause",
                "responsible", "evidence"]
SCHEMA_VERSION = 2


def append_row(path, fields, row):
    """Append a CSV row.  If an older file has a different header it is rotated aside
    (never mixed), so columns can never silently misalign."""
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    if not new:
        with open(path, newline="") as f:
            head = next(csv.reader(f), None)
        if head != fields:
            if head and set(head) <= set(fields):       # older, narrower schema: keep data safe
                os.replace(path, f"{path}.{datetime.now():%Y%m%d%H%M%S}.old")
                new = True
            elif head != fields:
                os.replace(path, f"{path}.{datetime.now():%Y%m%d%H%M%S}.old")
                new = True
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: csv_value(v) for k, v in row.items()})


def write_latest(path, payload):
    """Atomic write of STRICT JSON (NaN/inf -> null) so any parser can read it."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(dumps_json(payload))
    os.replace(tmp, path)          # dashboard never reads a half-written file


def alert_row(stamp, a):
    row = {"timestamp": stamp, **a}
    row["responsible"] = a.get("responsible_text", "")
    row["evidence"] = " | ".join(f"{e['name']}: {e['status']}" for e in a.get("evidence", []))
    return row


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=None, help="sampling interval s (0.5-300, default 2)")
    ap.add_argument("--duration", type=float, default=0, help="seconds; 0 = run forever")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--window", type=int, default=None, help="persistence window (samples, default 5)")
    ap.add_argument("--required", type=int, default=None, help="samples that must hit (default 3)")
    ap.add_argument("--config", help="JSON file: interval, window, required, thresholds{}, baseline{}")
    ap.add_argument("--no-baseline", action="store_true", help="static thresholds only")
    ap.add_argument("--reset-baseline", action="store_true", help="discard the saved baseline")
    ap.add_argument("--baseline-min-samples", type=int, default=None)
    ap.add_argument("--z-threshold", type=float, default=None)
    return ap


def resolve_config(args):
    """CLI > config file > defaults.  Raises ConfigError with a readable message."""
    cfg = load_config_file(args.config) if args.config else {}
    base = dict(cfg.get("baseline", {}))
    if args.no_baseline:
        base["enabled"] = False
    if args.baseline_min_samples is not None:
        base["min_samples"] = args.baseline_min_samples
    if args.z_threshold is not None:
        base["z"] = args.z_threshold
    return dict(interval=args.interval if args.interval is not None else cfg.get("interval", 2.0),
                window=args.window if args.window is not None else cfg.get("window", 5),
                required=args.required if args.required is not None else cfg.get("required", 3),
                thresholds=cfg.get("thresholds", {}), baseline=base)


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = resolve_config(args)
        if args.duration < 0:
            raise ConfigError(f"--duration must be >= 0, got {args.duration}")
        os.makedirs(args.data_dir, exist_ok=True)
        bpath = os.path.join(args.data_dir, "baseline.json")
        if args.reset_baseline and os.path.exists(bpath):
            os.remove(bpath)
        engine = MonitorEngine(baseline_path=bpath, **cfg)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    log_csv = os.path.join(args.data_dir, "system_logs.csv")
    alert_csv = os.path.join(args.data_dir, "alerts.csv")
    latest = os.path.join(args.data_dir, "latest.json")
    det = engine.detector
    seen, nrec = set(), 0
    start = last = time.monotonic()
    print(f"Monitoring every {engine.interval}s -> {args.data_dir}/ (Ctrl+C to stop)")
    if not engine.cpu.psi.available:
        print("Note: PSI (/proc/pressure) is unavailable; diagnoses rely on saturation indicators only "
              "and confidence is capped at MEDIUM.")
    try:
        while True:
            time.sleep(engine.interval)
            now = time.monotonic()
            dt, last = now - last, now
            s, active, procs = engine.step(dt)
            stamp = s["timestamp"]
            st = det.status
            s.update(cpu_state=st.get("CPU"), mem_state=st.get("Memory"), disk_state=st.get("Disk I/O"))
            busiest = max(s.get("devices") or [{}], key=lambda d: d.get("util") or 0, default={})
            s["busiest_disk"] = busiest.get("name")
            append_row(log_csv, FIELDS, s)
            keys = set()
            for a in active:                       # log an alert only when it first appears / changes
                k = (a["resource"], a["device"], a["state"], a["severity"])
                keys.add(k)
                if k not in seen:
                    append_row(alert_csv, ALERT_FIELDS, alert_row(stamp, a))
                    print(f"[{stamp}] {a['severity']:<8} {a['state']:<9} {a['resource']:<8} {a['diagnosis']}")
            while nrec < len(det.recoveries):       # recovery events
                r = det.recoveries[nrec]
                nrec += 1
                append_row(alert_csv, ALERT_FIELDS, {
                    "timestamp": stamp, "resource": r["resource"], "severity": "INFO", "state": "RECOVERED",
                    "device": r["device"], "duration_s": r["duration_s"],
                    "diagnosis": f"{r['resource']} recovered after {r['duration_s']}s ({r['peak_state']} at peak)",
                    "recommendation": ""})
                print(f"[{stamp}] RECOVERED {r['resource']} after {r['duration_s']}s")
            seen = keys
            write_latest(latest, {
                "schema": SCHEMA_VERSION, "sample": s, "active_alerts": active,
                "top_cpu": procs.get("top_cpu", []), "top_mem": procs.get("top_mem", []),
                "top_io": procs.get("top_io", []), "resource_status": st,
                "attribution": det.attribution, "recoveries": list(det.recoveries),
                "baseline": None if engine.baseline is None else {
                    "samples": engine.baseline.readiness(), "min_samples": engine.baseline.min_samples,
                    "ready": engine.baseline.readiness() >= engine.baseline.min_samples},
                "window": det.window, "required": det.required, "thresholds": det.th})
            if args.duration and now - start >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.save_baseline()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
