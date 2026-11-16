"""Streamlit dashboard.  Run:  streamlit run dashboard/app.py   (while main.py is running)

Tabs separate MONITORING (what the numbers are) from DIAGNOSIS (what the engine concludes and why):
  Overview (system overview + active bottlenecks) | CPU | Memory | Disk I/O | Processes |
  Diagnosis details | Trends | Experiments | Results
All logic lives in dashboard/data.py (unit-tested); this file only draws.
"""
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dashboard import data as D                                       # noqa: E402
from analyzer.validation import ConfigError                           # noqa: E402

DATA = D.ROOT / "data"
EXP = DATA / "experiments"

st.set_page_config(page_title="Linux Bottleneck Detector", layout="wide")
st.title("Linux Bottleneck Detection and Diagnosis")

refresh = st.sidebar.slider("Refresh every (s)", 1, 10, 2)
points = st.sidebar.slider("History points", 30, 600, 150)
auto = st.sidebar.checkbox("Auto-refresh", True,
                           help="Untick while filling in the Experiments form; the page re-runs on every refresh.")
st.sidebar.caption("Condition legend")
for key in ("NORMAL", "ELEVATED", "POTENTIAL", "CONFIRMED", "RECOVERED"):
    st.sidebar.markdown(f"{D.badge(key)}  \n<small>{D.STATE_STYLE[key][2]}</small>", unsafe_allow_html=True)


def num(v, fmt="{:.0f}", unit=""):
    return "n/a" if v is None or (isinstance(v, float) and v != v) else fmt.format(v) + unit


def plot(df, cols, title, ylabel, ylim=None, thresh=None):
    cols = [c for c in cols if c in df and df[c].notna().any()]
    if not cols:
        st.caption(f"{title}: no data available (metric unsupported on this system).")
        return
    fig, ax = plt.subplots(figsize=(6, 2.6))
    for c in cols:
        ax.plot(df["time"], df[c], label=c)
    if thresh is not None:
        ax.axhline(thresh, ls="--", lw=1, color="red")
    ax.set_title(title, fontsize=10)
    ax.set_ylabel(ylabel)
    if ylim:
        ax.set_ylim(*ylim)
    if len(cols) > 1:
        ax.legend(fontsize=8, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)          # avoid leaking figures across reruns


def show_alert(a, expanded=False):
    label = f"{a['resource']}{' [' + a['device'] + ']' if a.get('device') and a['resource'] != 'Process' else ''}" \
            f" - {a.get('state_label', a['state'])} - severity {a['severity']}, confidence {a['confidence']}"
    with st.expander(label, expanded=expanded):
        st.markdown(f"**{a['diagnosis']}**")
        for k, v in D.diagnosis_summary(a):
            st.markdown(f"- **{k}:** {v}")
        rows = D.evidence_rows(a)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if a.get("contradicting"):
            st.markdown("**Contradicting evidence**")
            for x in a["contradicting"]:
                st.markdown(f"- {x}")
        if a.get("missing"):
            st.markdown("**Missing evidence / limitations**")
            for x in a["missing"]:
                st.markdown(f"- {x}")


latest = D.load_latest(DATA)
df = D.load_csv(DATA, "system_logs.csv", points)
t_over, t_cpu, t_mem, t_disk, t_proc, t_diag, t_trend, t_exp, t_res = st.tabs(
    ["Overview", "CPU", "Memory", "Disk I/O", "Processes", "Diagnosis details", "Trends", "Experiments", "Results"])

have = latest is not None and not df.empty
if have:
    s, alerts = latest["sample"], latest["active_alerts"]
    df["time"] = pd.to_datetime(df["timestamp"])
    states = D.resource_states(latest)

with t_over:
    if not have:
        st.info("No data yet. Start the collector first:  `python main.py`")
    else:
        level, text = D.overall_banner(latest)
        {"critical": st.error, "warning": st.warning, "info": st.info, "healthy": st.success}[level](text)
        c = st.columns(3)
        for col, r in zip(c, D.RESOURCES):
            col.markdown(f"**{r}**  \n{D.badge(states[r])}")
        m = st.columns(6)
        m[0].metric("CPU util", num(s.get("cpu_percent"), unit="%"))
        m[1].metric("Load / core", num((s.get("load1") or 0) / max(s.get("cores") or 1, 1), "{:.2f}"))
        m[2].metric("Memory used", num(s.get("mem_percent"), unit="%"), "available-based", delta_color="off")
        m[3].metric("Swap used", num(s.get("swap_percent"), unit="%"))
        m[4].metric("Busiest disk", num(s.get("disk_util"), unit="%"), s.get("busiest_disk"), delta_color="off")
        m[5].metric("PSI (cpu/mem/io)", "/".join(num(s.get(k), "{:.0f}") for k in
                                                 ("psi_cpu_some_pct", "psi_mem_some_pct", "psi_io_some_pct")))
        bl = latest.get("baseline")
        st.caption("Detection: static thresholds + historical baseline "
                   + (f"(baseline ready, {bl['samples']} samples)" if bl and bl["ready"]
                      else f"(baseline warming up: {bl['samples']}/{bl['min_samples']} samples; static thresholds only)"
                      if bl else "(baseline disabled)")
                   + f"; persistence {latest['required']} of {latest['window']} samples.")
        st.subheader("Active bottlenecks")
        diag = [a for a in alerts if a["resource"] != "Process"]
        if not diag:
            st.write("No bottlenecks detected.")
        for a in sorted(diag, key=lambda x: ("CONFIRMED", "POTENTIAL", "ELEVATED").index(x["state"])):
            show_alert(a, expanded=a["state"] != "ELEVATED")
        rec = latest.get("recoveries") or []
        if rec:
            st.subheader("Recovered conditions")
            st.dataframe(pd.DataFrame(rec[::-1]), hide_index=True, use_container_width=True)

with t_cpu:
    if have:
        st.markdown(f"State: {D.badge(states['CPU'])}")
        m = st.columns(5)
        m[0].metric("Utilisation", num(s.get("cpu_percent"), unit="%"))
        m[1].metric("CPU PSI some", num(s.get("psi_cpu_some_pct"), "{:.1f}", "%"), help="share of time at least one task waited for a CPU")
        m[2].metric("Run queue", num(s.get("runq_wait_per_core") if s.get("runq_wait_per_core") is not None else s.get("procs_running"), "{:.2f}"),
                    s.get("runq_source"), delta_color="off")
        m[3].metric("Load / core", num((s.get("load1") or 0) / max(s.get("cores") or 1, 1), "{:.2f}"))
        m[4].metric("Ctx switches/s", num(s.get("ctx_switches_s"), "{:,.0f}"))
        l, r = st.columns(2)
        with l:
            plot(df, ["cpu_percent", "iowait_percent"], "CPU utilisation (busy is not the same as contended)", "%", (0, 100), 85)
            plot(df, ["load1", "load5", "load15"], "Load average", "tasks")
        with r:
            plot(df, ["psi_cpu_some_pct", "psi_cpu"], "CPU PSI 'some' (interval vs kernel avg10)", "% stalled")
            plot(df, ["runq_wait_per_core", "procs_running"], "Run queue evidence", "tasks")
        for a in D.alerts_for(latest, "CPU"):
            show_alert(a, a["state"] != "ELEVATED")

with t_mem:
    if have:
        st.markdown(f"State: {D.badge(states['Memory'])}")
        m = st.columns(5)
        m[0].metric("Used (MemAvailable)", num(s.get("mem_percent"), unit="%"))
        m[1].metric("Available", num(s.get("mem_available_mb"), "{:,.0f}", " MB"))
        m[2].metric("Reclaimable cache", num(s.get("mem_reclaimable_mb"), "{:,.0f}", " MB"))
        m[3].metric("Swap in / out", f"{num((s.get('swap_in_s') or 0) / 2**20, '{:.2f}')} / {num((s.get('swap_out_s') or 0) / 2**20, '{:.2f}')} MB/s")
        m[4].metric("Mem PSI some/full", f"{num(s.get('psi_mem_some_pct'), '{:.1f}')} / {num(s.get('psi_mem_full_pct'), '{:.1f}')} %")
        st.caption("'Used' counts only memory that cannot be reclaimed. A high naive 'used' figure that is "
                   f"mostly cache ({num(s.get('mem_naive_used_percent'), unit='%')} by free-based accounting) is not pressure.")
        l, r = st.columns(2)
        with l:
            plot(df, ["mem_percent", "mem_naive_used_percent", "swap_percent"], "Memory (available-based vs naive) and swap", "%", (0, 100), 90)
            plot(df, ["swap_in_s", "swap_out_s"], "Swap activity", "bytes/s")
        with r:
            plot(df, ["psi_mem_some_pct", "psi_mem_full_pct"], "Memory PSI some / full", "% stalled")
            plot(df, ["major_faults_s"], "Major page faults", "/s")
        for a in D.alerts_for(latest, "Memory"):
            show_alert(a, a["state"] != "ELEVATED")

with t_disk:
    if have:
        st.markdown(f"State: {D.badge(states['Disk I/O'])}")
        devs = s.get("devices") or []
        if devs:
            tbl = pd.DataFrame(devs)[["name", "util", "await_ms", "queue", "iops", "read_mbs", "write_mbs", "top_layer"]]
            tbl.columns = ["device", "util %", "latency ms", "queue depth", "IOPS", "read MB/s", "write MB/s", "top layer"]
            st.dataframe(tbl.round(2), hide_index=True, use_container_width=True)
            st.caption("Utilisation is per device (never summed). On SSD/NVMe, 100% util can be reached without saturation; "
                       "latency, queue depth and I/O PSI decide whether tasks are actually waiting.")
        else:
            st.caption("Per-device statistics unavailable (/proc/diskstats unreadable).")
        m = st.columns(4)
        m[0].metric("I/O PSI some", num(s.get("psi_io_some_pct"), "{:.1f}", "%"), help="share of time at least one task waited on I/O")
        m[1].metric("I/O PSI full", num(s.get("psi_io_full_pct"), "{:.1f}", "%"), help="share of time ALL non-idle tasks waited")
        m[2].metric("iowait", num(s.get("iowait_percent"), "{:.1f}", "%"))
        m[3].metric("Throughput", f"{num((s.get('disk_read_mbs') or 0) + (s.get('disk_write_mbs') or 0), '{:.1f}')} MB/s")
        l, r = st.columns(2)
        with l:
            plot(df, ["disk_util", "disk_util_mean"], "Disk utilisation (max and mean over devices)", "%", (0, 100), 80)
            plot(df, ["disk_read_mbs", "disk_write_mbs"], "Disk throughput (not a problem by itself)", "MB/s")
        with r:
            plot(df, ["psi_io_some_pct", "psi_io_full_pct"], "I/O PSI some / full", "% stalled")
            plot(df, ["disk_await_ms", "disk_queue"], "Latency (ms) and queue depth", "")
        for a in D.alerts_for(latest, "Disk I/O"):
            show_alert(a, a["state"] != "ELEVATED")

with t_proc:
    if have:
        st.caption("Attribution is correlation, not proof: a process is named only if it contributed in at least "
                   f"{latest['required']} of the last {latest['window']} samples (identity = PID + start time).")
        attr = latest.get("attribution") or {}
        for key, title in (("cpu", "CPU"), ("memory", "Memory"), ("io", "Disk I/O")):
            a = attr.get(key)
            if not a:
                continue
            st.subheader(f"{title} contributors - verdict: {a['verdict']}")
            if a["contributors"]:
                st.dataframe(pd.DataFrame(a["contributors"]), hide_index=True, use_container_width=True)
            elif a.get("note"):
                st.write(a["note"])
            if a["pid_reuse_events"]:
                st.caption(f"{a['pid_reuse_events']} PID reuse event(s) detected; recycled PIDs never inherit history.")
        a1, b1, c1 = st.columns(3)
        a1.subheader("Top CPU now")
        a1.dataframe(pd.DataFrame(latest["top_cpu"]), hide_index=True, use_container_width=True)
        b1.subheader("Top memory now")
        b1.dataframe(pd.DataFrame(latest["top_mem"]), hide_index=True, use_container_width=True)
        c1.subheader("Top I/O now")
        io = latest.get("top_io") or []
        c1.dataframe(pd.DataFrame(io), hide_index=True, use_container_width=True) if io else \
            c1.caption("No per-process I/O visible (needs root for other users' processes).")

with t_diag:
    if have:
        if not alerts:
            st.write("Nothing to diagnose: no active conditions.")
        for a in alerts:
            show_alert(a, False)
        with st.expander("Alert history"):
            hist = D.load_csv(DATA, "alerts.csv")
            st.dataframe(hist.iloc[::-1] if not hist.empty else hist, hide_index=True, use_container_width=True)

with t_trend:
    if have:
        pick = st.multiselect("Metrics", [c for c in df.columns if c not in ("timestamp", "time") and df[c].dtype != object],
                              default=["cpu_percent", "mem_percent", "disk_util"])
        if pick:
            plot(df, pick, "Selected metrics", "")
        state_cols = [c for c in ("cpu_state", "mem_state", "disk_state") if c in df]
        if state_cols:
            st.caption("Engine state history")
            st.dataframe(df[["timestamp"] + state_cols].iloc[::-1].head(60), hide_index=True, use_container_width=True)

with t_exp:
    st.markdown("Controlled experiments deliberately create **bounded** load, then check whether the engine "
                "recognises it. They only start when you press the button, never need root, and clean up after themselves.")
    run = D.running_experiment(EXP)
    if run:
        st.info(f"Running: **{run['kind']}** ({run['run_id']}) - phase **{run['phase']}**")
        st.progress(min(1.0, (run.get("t") or 0) / max(run.get("total_s") or 1, 1)))
        if st.button("Cancel experiment (stops workload, cleans up)"):
            D.request_cancel(EXP)
            st.warning("Cancel requested.")
    else:
        kind = st.selectbox("Experiment", ["cpu", "memory", "disk", "cpu-benign", "control"], help=
                            "cpu: oversubscribe CPUs. memory: bounded allocation + cache churn. disk: bounded fsync writes. "
                            "cpu-benign: busy CPU WITHOUT oversubscription (the engine must NOT call it contention). "
                            "control: no workload (false-positive check).")
        c1, c2, c3 = st.columns(3)
        duration = c1.number_input("Stress seconds", 5, 600, 30)
        baseline = c2.number_input("Baseline seconds", 5, 600, 20)
        recovery = c3.number_input("Max recovery seconds", 5, 900, 30)
        workers = memory_pct = disk_mb = disk_path = None
        if kind in ("cpu", "cpu-benign"):
            workers = st.number_input("Workers (0 = safe default)", 0, 256, 0) or None
        if kind == "memory":
            memory_pct = st.number_input("% of AVAILABLE RAM", 5, 95, 80)
        if kind == "disk":
            disk_mb = st.number_input("MB per worker", 16, 4096, 256)
            disk_path = st.text_input("Scratch directory on a real disk (blank = system temp)", "") or None
        from experiments.workloads import plan_experiment
        try:
            plan = plan_experiment(kind, duration, workers, memory_pct, disk_mb, disk_path, "auto", baseline, recovery)
            for n in plan["notes"] + plan["warnings"]:
                st.write("- " + n)
            ok = st.checkbox("I understand and want to start this experiment")
            if st.button("Start experiment", disabled=not ok):
                D.launch_experiment(D.build_launch_cmd(kind, duration, baseline, recovery, workers, memory_pct,
                                                       disk_mb, disk_path, "auto", str(EXP)), EXP)
                st.success("Started. This page will show progress.")
        except ConfigError as e:
            st.error(f"Cannot run this experiment: {e}")
    log = EXP / "last_launch.log"
    if log.exists():
        with st.expander("Last launch output"):
            st.code(log.read_text()[-4000:] or "(empty)")

with t_res:
    runs = D.list_experiments(EXP)
    if (EXP / "suite.md").exists():
        with st.expander("Aggregate validation report (suite.md)"):
            st.markdown((EXP / "suite.md").read_text())
    if not runs:
        st.info("No finished experiments yet. Start one in the Experiments tab or with `python experiment.py run cpu`.")
    else:
        st.dataframe(pd.DataFrame(runs)[["run_id", "kind", "verdict", "status"]], hide_index=True, use_container_width=True)
        pick = st.selectbox("Open report", [r["run_id"] for r in runs])
        path = Path(next(r["path"] for r in runs if r["run_id"] == pick))
        if (path / "timeline.png").exists():
            st.image(str(path / "timeline.png"))
        if (path / "report.md").exists():
            st.markdown((path / "report.md").read_text())

if auto:
    time.sleep(refresh)
    st.rerun()
