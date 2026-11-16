"""Summarise the CSV history into a Markdown report.  python reports/report_generator.py

Reads data/system_logs.csv and data/alerts.csv (old files without the V2 columns still work)
and, if present, the controlled-experiment results under data/experiments/.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

STAT_COLS = ["cpu_percent", "iowait_percent", "load1", "mem_percent", "swap_percent",
             "disk_read_mbs", "disk_write_mbs", "disk_util", "disk_await_ms",
             "psi_cpu_some_pct", "psi_mem_some_pct", "psi_io_some_pct", "runq_wait_per_core"]


def _fmt(x):
    return "n/a" if pd.isna(x) else x


def generate(data_dir=ROOT / "data", out=ROOT / "reports" / "summary.md"):
    data_dir = Path(data_dir)
    df = pd.read_csv(data_dir / "system_logs.csv")
    cols = [c for c in STAT_COLS if c in df.columns]
    stats = df[cols].apply(pd.to_numeric, errors="coerce").agg(["mean", "max"]).round(2).T
    lines = [f"# Performance report ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"Samples: {len(df)}  |  Period: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}", "",
             "Blank/`n/a` means the metric was unavailable on this system (never a silent zero).", "",
             "| Metric | Mean | Max |", "|---|---|---|"]
    lines += [f"| {m} | {_fmt(r['mean'])} | {_fmt(r['max'])} |" for m, r in stats.iterrows()]
    for col, name in (("cpu_state", "CPU"), ("mem_state", "Memory"), ("disk_state", "Disk I/O")):
        if col in df.columns and df[col].notna().any():
            counts = df[col].value_counts()
            lines += ["", f"State time share, {name}: " + ", ".join(f"{k} {v / len(df):.0%}" for k, v in counts.items())]
    alerts_path = data_dir / "alerts.csv"
    lines += ["", "## Diagnoses"]
    if alerts_path.exists() and alerts_path.stat().st_size:
        al = pd.read_csv(alerts_path)
        if "state" not in al.columns:                       # alerts written by V1
            al["state"] = "UNSPECIFIED"
        for c in ("confidence", "method", "device", "responsible"):
            if c not in al.columns:
                al[c] = ""
        al = al.fillna("")
        real = al[~al["state"].isin(["ELEVATED", "RECOVERED"])]
        lines += ["", "Only POTENTIAL/CONFIRMED rows are contention findings. ELEVATED means 'busy, nothing waiting'.", "",
                  "Counts by resource / state / severity:"]
        lines += [f"- {n} x {r} / {st} / {s}" for (r, st, s), n in al.groupby(["resource", "state", "severity"]).size().items()]
        lines += ["", "| Time | Resource | State | Severity | Confidence | Method | Diagnosis | Responsible |",
                  "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {r.timestamp} | {r.resource} | {r.state} | {r.severity} | {r.confidence} | {r.method} | "
                  f"{r.diagnosis} | {r.responsible} |" for r in al.itertuples()]
        if real.empty:
            lines += ["", "No contention (POTENTIAL/CONFIRMED) was diagnosed in this period."]
    else:
        lines.append("None.")
    exp = data_dir / "experiments"
    if exp.is_dir():
        rows = []
        for d in sorted(exp.iterdir()):
            try:
                r = json.loads((d / "report.json").read_text())
                rows.append(f"| {r['run_id']} | {r['kind']} | {r['evaluation']['verdict']} |")
            except (OSError, ValueError, KeyError):
                continue
        if rows:
            lines += ["", "## Controlled experiments", "", "| Run | Kind | Verdict |", "|---|---|---|"] + rows
            if (exp / "suite.md").exists():
                lines += ["", f"Aggregate validation: `{exp / 'suite.md'}`"]
    Path(out).write_text("\n".join(lines) + "\n")
    return out


if __name__ == "__main__":
    try:
        print("Wrote", generate())
    except FileNotFoundError:
        raise SystemExit("No data/system_logs.csv yet. Run `python main.py` first.")
