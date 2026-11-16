"""Dashboard logic without any Streamlit dependency (so it can be unit-tested).

Everything the UI shows is derived here from the files written by main.py and experiment.py.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# state -> (label, streamlit colour, one-line meaning).  The five visual categories requested:
STATE_STYLE = {
    "NORMAL": ("Normal operation", "green", "No unusual utilisation and no contention evidence."),
    "ELEVATED": ("Elevated utilisation", "blue", "Busy or full, but nothing is observed waiting. Not a bottleneck claim."),
    "POTENTIAL": ("Potential resource pressure", "orange", "Some contention evidence, incomplete or contradicted."),
    "CONFIRMED": ("Confirmed sustained contention", "red", "Stall evidence plus corroboration for the full persistence window."),
    "RECOVERED": ("Recovered", "violet", "A previously detected condition has cleared."),
}
RESOURCES = ("CPU", "Memory", "Disk I/O")


def badge(state):
    label, colour, _ = STATE_STYLE.get(state, STATE_STYLE["NORMAL"])
    return f":{colour}[**{label}**]"


def load_latest(data_dir):
    """latest.json or None (missing, half-written or corrupt)."""
    try:
        return json.loads((Path(data_dir) / "latest.json").read_text())
    except (OSError, ValueError):
        return None


def load_csv(data_dir, name, tail=None):
    import pandas as pd
    try:
        df = pd.read_csv(Path(data_dir) / name)
        return df.tail(tail) if tail else df
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def overall_banner(latest):
    """(level, text) where level in healthy|info|warning|critical.  Utilisation alone never
    produces warning/critical: only POTENTIAL/CONFIRMED diagnoses do."""
    alerts = [a for a in (latest or {}).get("active_alerts", []) if a.get("resource") != "Process"]
    conf = [a for a in alerts if a["state"] == "CONFIRMED"]
    pot = [a for a in alerts if a["state"] == "POTENTIAL"]
    if any(a["severity"] == "CRITICAL" for a in conf):
        return "critical", "Confirmed sustained contention (critical): " + ", ".join(sorted({a["resource"] for a in conf}))
    if conf:
        return "warning", "Confirmed sustained contention: " + ", ".join(sorted({a["resource"] for a in conf}))
    if pot:
        return "warning", "Potential resource pressure: " + ", ".join(sorted({a["resource"] for a in pot}))
    if any(a["state"] == "ELEVATED" for a in alerts):
        return "info", "High utilisation without contention evidence (not a bottleneck)"
    return "healthy", "No bottleneck detected"


def resource_states(latest):
    st = (latest or {}).get("resource_status") or {}
    return {r: st.get(r, "NORMAL") for r in RESOURCES}


def alerts_for(latest, resource):
    return [a for a in (latest or {}).get("active_alerts", []) if a.get("resource") == resource]


def evidence_rows(alert):
    """Rows for an evidence table: class, indicator status and the detail text."""
    return [{"class": e["cls"], "status": e["status"], "indicator": e["name"], "detail": e["detail"]}
            for e in alert.get("evidence", [])]


def diagnosis_summary(alert):
    """Ordered (label, text) pairs: exactly what the brief asks to show for each issue."""
    resp = alert.get("responsible") or {}
    return [("Resource", alert["resource"] + (f" [{alert['device']}]" if alert.get("device") else "")),
            ("Current condition", alert.get("state_label", alert["state"])),
            ("Severity", alert["severity"]),
            ("Confidence", f"{alert['confidence']} (indicators agreeing: {alert['evidence_ratio']})"),
            ("Duration", f"{alert.get('duration_s', 0):.0f} s"),
            ("Detection method", alert.get("method", "static")),
            ("Supporting metrics", "; ".join(e["name"] for e in alert.get("evidence", []) if e["status"] == "present") or "-"),
            ("Likely cause", alert.get("likely_cause", "")),
            ("Responsible process", alert.get("responsible_text") or resp.get("note") or "not identified"),
            ("Recommended action", alert.get("recommendation", ""))]


# ---------------------------------------------------------------- experiments
def list_experiments(root):
    """Finished experiment runs, newest first: [{run_id, kind, verdict, status, path}]."""
    out = []
    root = Path(root)
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir(), reverse=True):
        rp = d / "report.json"
        if not rp.exists():
            continue
        try:
            r = json.loads(rp.read_text())
            out.append({"run_id": r["run_id"], "kind": r["kind"], "status": r["status"],
                        "verdict": r["evaluation"]["verdict"], "path": str(d)})
        except (OSError, ValueError, KeyError):
            continue
    return out


def running_experiment(root, max_age=10.0):
    """state.json of an experiment that is still alive (updated within max_age seconds)."""
    best = None
    root = Path(root)
    if not root.is_dir():
        return None
    for d in root.iterdir():
        sp = d / "state.json"
        try:
            st = json.loads(sp.read_text())
        except (OSError, ValueError):
            continue
        if st.get("status") == "running" and time.time() - st.get("updated", 0) <= max_age:
            if best is None or st["updated"] > best["updated"]:
                best = st
    return best


def build_launch_cmd(kind, duration, baseline, recovery, workers=None, memory_pct=None, disk_mb=None,
                     disk_path=None, backend="auto", out=None):
    cmd = [sys.executable, str(ROOT / "experiment.py"), "run", kind, "--yes", "--duration", str(duration),
           "--baseline", str(baseline), "--recovery", str(recovery), "--backend", backend]
    for flag, val in (("--workers", workers), ("--memory-pct", memory_pct), ("--disk-mb", disk_mb),
                      ("--disk-path", disk_path), ("--out", out)):
        if val not in (None, ""):
            cmd += [flag, str(val)]
    return cmd


def launch_experiment(cmd, root):
    """Start an experiment as a detached child (never blocks the dashboard)."""
    Path(root).mkdir(parents=True, exist_ok=True)
    log = open(Path(root) / "last_launch.log", "w")
    try:
        return subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    finally:
        log.close()


def request_cancel(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / "CANCEL").touch()
