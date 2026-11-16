"""Turn a recorded experiment timeline into validation metrics.

Pure functions (no I/O, no system access) so they can be unit-tested with synthetic timelines.

Definitions (all times relative to the moment the workload was launched / stopped):
  detection (confirmed)  a CONFIRMED diagnosis for the stressed resource appeared during stress
  detection (potential)  a POTENTIAL or CONFIRMED diagnosis appeared
  detection latency      workload start -> first such diagnosis
  false positive         a POTENTIAL/CONFIRMED diagnosis when the resource was NOT being stressed
                         (baseline phase, 'control' runs, and the 'cpu-benign' negative control).
                         ELEVATED is deliberately not a positive: it only says "busy".
  diagnostic reliability the stressed resource is among the CONFIRMED resources
  attribution reliability among samples where the engine NAMED a process: share whose named process
                         was really part of the workload (abstentions are counted separately)
  recovery time          workload stop -> first sample where the target has no active
                         POTENTIAL/CONFIRMED diagnosis  (detector view), and -> metric back near baseline
                         for 3 consecutive samples  (measurement view)
"""
import statistics

TARGET = {"cpu": "CPU", "cpu-benign": "CPU", "memory": "Memory", "disk": "Disk I/O"}
RECOVERY_METRIC = {"cpu": ("cpu_percent", 10.0), "cpu-benign": ("cpu_percent", 10.0),
                   "memory": ("mem_percent", 3.0), "disk": ("disk_util", 15.0)}
POSITIVE = ("POTENTIAL", "CONFIRMED")
MEAN_METRICS = ["cpu_percent", "psi_cpu_some_pct", "runq_wait_per_core", "procs_running", "mem_percent",
                "psi_mem_some_pct", "swap_out_s", "disk_util", "disk_await_ms", "psi_io_some_pct",
                "disk_write_mbs", "disk_read_mbs"]
CORE_RESOURCES = ("CPU", "Memory", "Disk I/O")


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 3) if xs else None


def _positive(rec, resource=None):
    return [d for d in rec["diagnoses"] if d["state"] in POSITIVE and d["resource"] in CORE_RESOURCES
            and (resource is None or d["resource"] == resource)]


def _first(recs, pred, t0):
    for r in recs:
        if pred(r):
            return round(r["t"] - t0, 2)
    return None


def stress_effective(kind, stress):
    """Ground truth that does NOT use the detector's output: did the kernel's raw counters show
    that the workload really created pressure?  Used to separate 'engine missed it' from
    'there was nothing to detect'."""
    if not stress:
        return False, "no stress samples"
    m = lambda k: _mean(r["metrics"].get(k) for r in stress)
    if kind == "cpu":
        cpu, psi = m("cpu_percent"), m("psi_cpu_some_pct")
        return (cpu is not None and cpu >= 90 and psi is not None and psi >= 10,
                f"CPU {cpu}% busy, kernel CPU stall (PSI some) {psi}% (needs >= 90% and >= 10%)")
    if kind == "memory":
        psi, so = m("psi_mem_some_pct"), m("swap_out_s")
        return ((psi is not None and psi >= 1) or (so is not None and so >= 100_000),
                f"kernel memory stall (PSI some) {psi}%, swap-out {so} B/s (needs PSI >= 1% or swapping)")
    if kind == "disk":
        u = max((r["metrics"].get("disk_util") or 0) for r in stress)
        psi = m("psi_io_some_pct")
        return (u >= 50 or (psi is not None and psi >= 1),
                f"busiest disk reached {u:.0f}% util, kernel I/O stall (PSI some) {psi}% (needs >= 50% or >= 1%)")
    return True, ""


def evaluate(report):
    kind, tl, marks = report["kind"], report["timeline"], report["marks"]
    target = TARGET.get(kind)
    t_start, t_stop = marks["stress_start"], marks["stress_end"]
    base = [r for r in tl if r["phase"] == "baseline"]
    stress = [r for r in tl if r["phase"] == "stress"]
    rec = [r for r in tl if r["phase"] == "recovery"]
    pids = set(report.get("workload_pids", []))
    ev = {"kind": kind, "target": target, "status": report["status"],
          "samples": {"baseline": len(base), "stress": len(stress), "recovery": len(rec)}}

    # ---- false positives while nothing was being stressed ----------------------------------
    fp_base = sum(1 for r in base if _positive(r))
    ev["false_positives_baseline"] = {"samples": len(base), "positive_samples": fp_base,
                                      "rate": round(fp_base / len(base), 3) if base else None}

    # ---- phase means (measured) -------------------------------------------------------------
    ev["phase_means"] = {ph: {m: _mean(r["metrics"].get(m) for r in recs) for m in MEAN_METRICS}
                         for ph, recs in (("baseline", base), ("stress", stress), ("recovery", rec))}

    # ---- stress phase ------------------------------------------------------------------------
    conf_res = {d["resource"] for r in stress for d in _positive(r) if d["state"] == "CONFIRMED"}
    pos_res = {d["resource"] for r in stress for d in _positive(r)}
    ev["resources_confirmed_during_stress"] = sorted(conf_res)
    ev["resources_positive_during_stress"] = sorted(pos_res)
    if kind in ("cpu", "memory", "disk"):
        det_c = _first(stress, lambda r: any(d["state"] == "CONFIRMED" for d in _positive(r, target)), t_start)
        det_p = _first(stress, lambda r: bool(_positive(r, target)), t_start)
        n_conf = sum(1 for r in stress if any(d["state"] == "CONFIRMED" for d in _positive(r, target)))
        first = next((d for r in stress for d in _positive(r, target) if d["state"] == "CONFIRMED"), None) or \
            next((d for r in stress for d in _positive(r, target)), None)
        ev["detection"] = {"confirmed": det_c is not None, "potential_or_confirmed": det_p is not None,
                           "latency_confirmed_s": det_c, "latency_potential_s": det_p,
                           "confirmed_fraction_of_stress_samples": round(n_conf / len(stress), 3) if stress else None,
                           "first_diagnosis": first}
        ev["diagnostic_reliability"] = {
            "target_confirmed": target in conf_res,
            "only_target_confirmed": conf_res == {target},
            "other_resources_confirmed": sorted(conf_res - {target}),
            "note": ("Other resources may be genuine side effects of the stress (e.g. memory pressure causing "
                     "swap I/O); the harness cannot tell side effects from false alarms.")}
        # ---- attribution ---------------------------------------------------------------------
        correct = wrong = abstain = 0
        for r in stress:
            for d in _positive(r, target):
                if d["responsible_verdict"] in (None, "none", "unavailable") or not d["responsible_pids"]:
                    abstain += 1
                elif pids & set(d["responsible_pids"]):
                    correct += 1
                else:
                    wrong += 1
        named = correct + wrong
        ev["attribution"] = {"correct": correct, "wrong": wrong, "abstained": abstain,
                             "reliability": round(correct / named, 3) if named else None,
                             "workload_pids_known": bool(pids)}
        # ---- verdict -------------------------------------------------------------------------
        ev["expected"] = f"CONFIRMED contention on {target} during stress"
        eff, why = stress_effective(kind, stress)
        ev["stress_effective"] = {"effective": eff, "evidence": why}
        ev["verdict"] = "PASS" if det_c is not None else ("PARTIAL" if det_p is not None else "FAIL")
        if det_c is None and not eff:
            ev["verdict"] = "INEFFECTIVE_STRESS"      # nothing measurable to detect: not a detector failure
    elif kind == "cpu-benign":
        fp = sum(1 for r in stress if _positive(r))
        elev = sum(1 for r in stress if any(d["state"] == "ELEVATED" and d["resource"] == "CPU" for d in r["diagnoses"]))
        ev["negative_control"] = {"stress_samples": len(stress), "positive_samples": fp,
                                  "false_positive_rate": round(fp / len(stress), 3) if stress else None,
                                  "samples_flagged_elevated_only": elev,
                                  "mean_cpu_percent": ev["phase_means"]["stress"]["cpu_percent"]}
        ev["expected"] = "High CPU utilisation but NO POTENTIAL/CONFIRMED diagnosis (nothing is waiting)"
        ev["verdict"] = "PASS" if fp == 0 else "FAIL"
    else:  # control: nothing is started; the 'stress' phase is just more quiet time
        fp = sum(1 for r in stress if _positive(r))
        ev["negative_control"] = {"stress_samples": len(stress), "positive_samples": fp,
                                  "false_positive_rate": round(fp / len(stress), 3) if stress else None,
                                  "note": "baseline-phase positives are counted separately (no double counting)"}
        ev["expected"] = "No POTENTIAL/CONFIRMED diagnosis on an idle system"
        ev["verdict"] = "PASS" if fp == 0 and fp_base == 0 else "FAIL"
    if report["status"] == "cancelled":
        ev["verdict"] = "INCONCLUSIVE (cancelled)"

    # ---- recovery -----------------------------------------------------------------------------
    if target and kind in ("cpu", "memory", "disk"):
        was_pos = any(_positive(r, target) for r in stress)
        t_clear = _first(rec, lambda r: not _positive(r, target), t_stop) if was_pos else None
        metric, tol = RECOVERY_METRIC[kind]
        bvals = [r["metrics"].get(metric) for r in base if isinstance(r["metrics"].get(metric), (int, float))]
        t_metric = None
        if len(bvals) >= 3:
            limit = statistics.mean(bvals) + max(3 * statistics.pstdev(bvals), tol)
            streak = 0
            for i, r in enumerate(rec):
                v = r["metrics"].get(metric)
                streak = streak + 1 if isinstance(v, (int, float)) and v <= limit else 0
                if streak >= 3:
                    t_metric = round(rec[i - 2]["t"] - t_stop, 2)
                    break
        ev["recovery"] = {"applicable": was_pos, "detector_clear_s": t_clear, "metric": metric,
                          "metric_back_near_baseline_s": t_metric,
                          "recovered_within_window": t_clear is not None if was_pos else None,
                          "observed_recovery_samples": len(rec)}
    ev["cleanup"] = {"leftover_workload_pids": report.get("leftover_pids", []),
                     "ok": not report.get("leftover_pids")}
    ev["verified"] = ["Measured: metric means per phase, detection latency, recovery times, PIDs of the workload "
                      "processes (read from the OS), whether any workload process survived cleanup."]
    ev["inferred"] = ["Inferred (not independently verified): the 'likely cause' text of each diagnosis and "
                      "any statement about side-effect resources."]
    return ev


# --------------------------------------------------------------------------------------------
def _stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 2), "median": round(statistics.median(xs), 2),
            "min": min(xs), "max": max(xs)}


def aggregate(reports):
    """Pool several experiment reports (from `experiment.py suite`)."""
    out = {"runs": len(reports), "by_kind": {}, "false_positives": {}, "calibration": {}}
    by = {}
    for r in reports:
        by.setdefault(r["kind"], []).append(r)
    for kind, rs in by.items():
        evs = [r["evaluation"] for r in rs]
        d = {"runs": len(rs), "verdicts": {v: sum(1 for e in evs if e["verdict"] == v) for v in
                                           {e["verdict"] for e in evs}}}
        if kind in ("cpu", "memory", "disk"):
            d["detection_rate_confirmed"] = round(sum(e["detection"]["confirmed"] for e in evs) / len(evs), 3)
            d["detection_rate_potential"] = round(sum(e["detection"]["potential_or_confirmed"] for e in evs) / len(evs), 3)
            d["latency_confirmed_s"] = _stats(e["detection"]["latency_confirmed_s"] for e in evs)
            d["latency_potential_s"] = _stats(e["detection"]["latency_potential_s"] for e in evs)
            d["diagnostic_reliability"] = round(sum(e["diagnostic_reliability"]["target_confirmed"] for e in evs) / len(evs), 3)
            c = sum(e["attribution"]["correct"] for e in evs)
            w = sum(e["attribution"]["wrong"] for e in evs)
            a = sum(e["attribution"]["abstained"] for e in evs)
            d["attribution"] = {"correct": c, "wrong": w, "abstained": a,
                                "reliability": round(c / (c + w), 3) if c + w else None}
            d["recovery_detector_clear_s"] = _stats(e["recovery"]["detector_clear_s"] for e in evs if "recovery" in e)
            d["recovery_metric_s"] = _stats(e["recovery"]["metric_back_near_baseline_s"] for e in evs if "recovery" in e)
        out["by_kind"][kind] = d
    fp_s = fp_n = 0
    for r in reports:
        e = r["evaluation"]
        fp_s += e["false_positives_baseline"]["positive_samples"]
        fp_n += e["false_positives_baseline"]["samples"]
        if r["kind"] in ("cpu-benign", "control"):
            fp_s += e["negative_control"]["positive_samples"]
            fp_n += e["negative_control"]["stress_samples"]
    out["false_positives"] = {"positive_samples": fp_s, "samples": fp_n,
                              "rate": round(fp_s / fp_n, 4) if fp_n else None,
                              "note": "pooled over baseline phases, control runs and the cpu-benign negative control"}
    # confidence calibration: of the sample-level positive diagnoses, how many matched the truth?
    cal = {}
    for r in reports:
        target = TARGET.get(r["kind"])
        for rec in r["timeline"]:
            if rec["phase"] == "recovery":
                continue
            stressed = rec["phase"] == "stress" and r["kind"] in ("cpu", "memory", "disk")
            for d in _positive(rec):
                key = (d["state"], d["confidence"])
                c = cal.setdefault(key, {"diagnoses": 0, "correct": 0})
                c["diagnoses"] += 1
                c["correct"] += int(stressed and d["resource"] == target)
    out["calibration"] = {f"{s}/{c}": {**v, "precision": round(v["correct"] / v["diagnoses"], 3)}
                          for (s, c), v in sorted(cal.items())}
    out["calibration_note"] = ("Precision = share of sample-level POTENTIAL/CONFIRMED diagnoses (outside recovery) "
                               "that were produced while that very resource was being stressed. Small samples: "
                               "treat as a sanity check, not as a statistical calibration.")
    return out
