"""Markdown + chart output for experiment reports."""
import json

from .evaluation import TARGET


def _fmt(v, unit=""):
    return "n/a" if v is None else (f"{v}{unit}" if not isinstance(v, bool) else str(v))


def write_markdown(report, path):
    with open(path, "w") as f:
        f.write(render_markdown(report))


def render_markdown(r):
    e, p, env = r["evaluation"], r["params"], r["environment"]
    L = [f"# Experiment report: {r['kind']}  ({r['run_id']})", "",
         f"**Verdict: {e['verdict']}**  |  status: {r['status']}  |  expected: {e.get('expected', '-')}", "",
         "## Setup",
         f"- Phases: baseline {p['baseline_s']}s, stress {p['duration']}s, recovery up to {p['recovery_s']}s; "
         f"interval {p['interval']}s; persistence {r['params']['required']} of {p['window']} samples",
         f"- Workload backend: {r['plan'].get('backend')}, workers: {r['plan'].get('workers')}"
         + (f", memory target {r['plan']['memory_mb']} MB" if "memory_mb" in r["plan"] else "")
         + (f", disk {r['plan']['disk_mb']} MB x workers on {r['plan'].get('disk_fs')}" if "disk_mb" in r["plan"] else ""),
         f"- Machine: {env['cores']} core(s), {env['mem_total_mb']} MB RAM, kernel {env['kernel']}, "
         f"PSI cpu/mem/io = {env['psi_cpu']}/{env['psi_memory']}/{env['psi_io']}, "
         f"schedstat = {env['schedstat']}",
         *[f"- Note: {n}" for n in r["plan"].get("notes", [])], ""]
    pm = e["phase_means"]
    L += ["## Measured metrics by phase  *(MEASURED)*", "", "| Metric | Baseline | Stress | Recovery |", "|---|---|---|---|"]
    for m in pm["baseline"]:
        L.append(f"| {m} | {_fmt(pm['baseline'][m])} | {_fmt(pm['stress'][m])} | {_fmt(pm['recovery'][m])} |")
    fb = e["false_positives_baseline"]
    L += ["", f"False positives in the baseline phase: {fb['positive_samples']}/{fb['samples']} samples *(MEASURED)*", ""]
    if "detection" in e:
        d, dr, at = e["detection"], e["diagnostic_reliability"], e["attribution"]
        se = e.get("stress_effective")
        L += ["## Detection *(MEASURED)*", "",
              *( [f"- Was the stress effective (kernel counters, independent of the detector)? **{se['effective']}** - {se['evidence']}"] if se else [] ),
              f"- Target resource: **{e['target']}**",
              f"- Confirmed: {d['confirmed']}  (latency {_fmt(d['latency_confirmed_s'], ' s')} after workload start)",
              f"- Potential or confirmed: {d['potential_or_confirmed']}  (latency {_fmt(d['latency_potential_s'], ' s')})",
              f"- Stress samples with the target CONFIRMED: {_fmt(d['confirmed_fraction_of_stress_samples'])}",
              f"- Resources CONFIRMED during stress: {', '.join(e['resources_confirmed_during_stress']) or 'none'}",
              f"- Diagnostic reliability (target among confirmed): {dr['target_confirmed']}; "
              f"only target: {dr['only_target_confirmed']}",
              f"  - {dr['note']}",
              f"- Attribution: correct {at['correct']}, wrong {at['wrong']}, abstained {at['abstained']}, "
              f"reliability {_fmt(at['reliability'])} (workload PIDs known: {at['workload_pids_known']})", ""]
        fd = d["first_diagnosis"]
        if fd:
            L += ["## First diagnosis produced by the engine", "",
                  f"**{fd['headline']}**", "",
                  f"- State: {fd['state']}  |  Severity: {fd['severity']}  |  Confidence: {fd['confidence']}  "
                  f"|  Detection method: {fd['method']}",
                  f"- Likely cause *(INFERRED)*: {fd['likely_cause']}",
                  f"- Responsible process: {fd['responsible_text'] or 'not identified'}",
                  f"- Recommendation: {fd['recommendation']}", "", "Evidence:"]
            L += [f"  - [{x['cls']}/{x['status']}] {x['detail']}" for x in fd["evidence"]]
            if fd["contradicting"]:
                L += ["", "Contradicting evidence:"] + [f"  - {x}" for x in fd["contradicting"]]
            if fd["missing"]:
                L += ["", "Missing evidence:"] + [f"  - {x}" for x in fd["missing"]]
            L.append("")
    if "negative_control" in e:
        n = e["negative_control"]
        L += ["## Negative control *(MEASURED)*", ""] + [f"- {k}: {_fmt(v)}" for k, v in n.items()] + [""]
    if "recovery" in e:
        rc = e["recovery"]
        L += ["## Recovery *(MEASURED)*", "",
              f"- Detector reported 'cleared' {_fmt(rc['detector_clear_s'], ' s')} after the workload stopped",
              f"- `{rc['metric']}` back near its baseline after {_fmt(rc['metric_back_near_baseline_s'], ' s')}",
              f"- Recovery samples observed: {rc['observed_recovery_samples']}", ""]
    c = e["cleanup"]
    L += ["## Cleanup *(MEASURED)*", "", f"- Workload processes still alive after stop: {c['leftover_workload_pids'] or 'none'}", "",
          "## What is verified vs inferred", "", f"- {e['verified'][0]}", f"- {e['inferred'][0]}", "",
          "## Limitations", "",
          "- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.",
          "- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.",
          "- Other activity on the machine during the run can add noise (the harness cannot remove it).", ""]
    return "\n".join(L)


def render_suite(agg, reports):
    L = [f"# Validation suite ({agg['runs']} runs)", "", "## Results per experiment *(MEASURED)*", ""]
    for kind, d in agg["by_kind"].items():
        L += [f"### {kind}", f"- runs: {d['runs']}, verdicts: {d['verdicts']}"]
        if "detection_rate_confirmed" in d:
            L += [f"- detection rate (confirmed): {d['detection_rate_confirmed']}, (potential+): {d['detection_rate_potential']}",
                  f"- detection latency, confirmed (s): {d['latency_confirmed_s']}",
                  f"- diagnostic reliability: {d['diagnostic_reliability']}",
                  f"- attribution: {d['attribution']}",
                  f"- recovery, detector (s): {d['recovery_detector_clear_s']}; metric (s): {d['recovery_metric_s']}"]
        L.append("")
    fp = agg["false_positives"]
    L += ["## False-positive rate *(MEASURED)*", "", f"{fp['positive_samples']}/{fp['samples']} samples = {fp['rate']}  ({fp['note']})", "",
          "## Confidence calibration *(MEASURED, small sample)*", "", "| state/confidence | diagnoses | correct | precision |", "|---|---|---|---|"]
    L += [f"| {k} | {v['diagnoses']} | {v['correct']} | {v['precision']} |" for k, v in agg["calibration"].items()]
    L += ["", agg["calibration_note"], "", "## Individual runs", ""]
    L += [f"- {r['run_id']}: {r['evaluation']['verdict']}" for r in reports]
    return "\n".join(L) + "\n"


def write_chart(report, path):
    """Timeline PNG (optional: skipped silently if matplotlib is missing)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    tl = report["timeline"]
    if not tl:
        return False
    t = [r["t"] for r in tl]
    kind, m = report["kind"], report["marks"]
    series = {"cpu": [("cpu_percent", "CPU %"), ("psi_cpu_some_pct", "CPU PSI some %")],
              "cpu-benign": [("cpu_percent", "CPU %"), ("psi_cpu_some_pct", "CPU PSI some %")],
              "memory": [("mem_percent", "Memory used %"), ("psi_mem_some_pct", "Memory PSI some %")],
              "disk": [("disk_util", "busiest disk util %"), ("psi_io_some_pct", "I/O PSI some %")],
              "control": [("cpu_percent", "CPU %"), ("psi_cpu_some_pct", "CPU PSI some %")]}[kind]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for key, label in series:
        a1.plot(t, [r["metrics"].get(key) for r in tl], label=label)
    a1.axvspan(m["stress_start"], m["stress_end"], color="red", alpha=0.08, label="workload running")
    a1.legend(fontsize=8, loc="upper left")
    a1.set_title(f"{kind} experiment {report['run_id']}  verdict: {report['evaluation']['verdict']}", fontsize=9)
    rank = {"NORMAL": 0, "RECOVERED": 0, "ELEVATED": 1, "POTENTIAL": 2, "CONFIRMED": 3}
    tgt = TARGET.get(kind) or "CPU"
    a2.step(t, [rank.get(r["status"].get(tgt, "NORMAL"), 0) for r in tl], where="post")
    a2.set_yticks([0, 1, 2, 3])
    a2.set_yticklabels(["normal", "elevated", "potential", "confirmed"], fontsize=7)
    a2.set_xlabel("seconds since experiment start")
    a2.set_title(f"engine state for {tgt}", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True
