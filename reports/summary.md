# Linux Bottleneck Detective V2 — Project Summary

**Operating Systems Project | November 2026**  
**Repository:** `Linux-Bottleneck-Detection`  

---

## Project Purpose

Linux Bottleneck Detective V2 is an autonomous, explainable performance monitoring and diagnostic engine for Linux systems. It goes beyond traditional tools (`top`, `vmstat`, `iostat`) by automatically determining whether a resource — CPU, memory, or disk I/O — is **genuinely contended** (tasks are waiting and stalling), not merely busy. It explains its reasoning using evidence from multiple kernel data sources, identifies the responsible processes, and validates its own accuracy through built-in controlled experiments.

---

## Main Features

| Feature | Description |
|---|---|
| **5-state detection** | `NORMAL` → `ELEVATED` → `POTENTIAL` → `CONFIRMED` → `RECOVERED` |
| **Evidence-based diagnosis** | Requires corroborating stall + saturation + utilization evidence before confirming a bottleneck |
| **Persistence filter** | Requires 3 of 5 consecutive samples to hold before alerting — eliminates transient spikes |
| **Interval-exact PSI** | Reads cumulative kernel PSI microseconds directly; no exponential-smoothing lag |
| **Dynamic MAD baseline** | Rolling Median + Median Absolute Deviation baseline; resistant to spike contamination |
| **Identity-aware attribution** | Tracks `(PID, start_time)` pairs; immune to PID-recycling misattribution |
| **Automated experiment harness** | 3-phase controlled experiments (Baseline → Stress → Recovery) with structured JSON/Markdown reporting |
| **Campaign runner** | Automated multi-experiment campaigns with manifest persistence, recovery, and locking |
| **Streamlit dashboard** | Real-time 9-tab visualizer of all system metrics and detector state |
| **189-test suite** | Fully automated unit test coverage (0 failures, 0 errors) |

---

## Technologies & Linux Mechanisms

| Mechanism | Usage |
|---|---|
| `/proc/stat` | CPU jiffies; context switches |
| `/proc/schedstat` | Per-CPU run-queue wait nanoseconds |
| `/proc/meminfo`, `/proc/vmstat` | `MemAvailable`, swap rates, major page faults |
| `/proc/diskstats`, `/sys/block` | Per-device I/O counters, queue depth, latency |
| `/proc/pressure/{cpu,memory,io}` | Pressure Stall Information (PSI) — cumulative microsecond stall totals |
| `/proc/<pid>/stat`, `/proc/<pid>/io` | Per-process CPU jiffies, start time, I/O bytes |
| `psutil` | Cross-platform process enumeration and supplementary system metrics |
| `prctl(PR_SET_PDEATHSIG)` | Orphan prevention for workload worker processes |
| Streamlit | Real-time web dashboard |
| Python `unittest` | Automated test framework |

---

## Campaign Overview

An automated 15-experiment campaign was executed on **2026-11-18** using the campaign runner:

| Category | Repeats | Configuration |
|---|---|---|
| `control` | 3 | No workload (idle system) |
| `cpu-benign` | 3 | 8 CPU workers on 36-core host |
| `cpu` stress | 3 | 12 CPU workers (nice 10) on 36-core host |
| `memory` stress | 3 | 10% RAM allocation (~5.7 GB) on 57 GB system |
| `disk` stress | 3 | 2 workers, 64 MB fsync blocks on NVMe (`/var/tmp`) |

**Safety constraints:** No sudo, 12 CPU workers max, 10% RAM cap, 128 MB disk scratch per run, isolated directories with full cleanup, `oom_score_adj=1000` on memory workers.

All 15 experiments completed: 0 failed, 0 cancelled, 0 pending.

---

## Main Results

| Category | Verdict | Key Finding |
|---|---|---|
| Control (3/3) | **PASS** | Zero false alerts; detector correctly silent during idle |
| CPU-benign (3/3) | **PASS** | ~27–28% CPU; zero false positives under moderate load |
| CPU stress (3/3) | **INEFFECTIVE_STRESS** | 12/36 workers created no oversubscription; thresholds not reached |
| Memory stress (3/3) | **INEFFECTIVE_STRESS** | 5.7 GB on 57 GB system: no kernel memory stalls |
| Disk stress (3/3) | **PARTIAL** | POTENTIAL disk detection in ~3.05 s; NVMe handled load efficiently |

**Key measurements:**

- **False-positive alerts:** 0 out of 250 samples (observed rate: 0.00%)
- **Disk POTENTIAL detection latency:** ~3.05 s (matches 3-sample persistence minimum)
- **Detector alert clearance (disk):** ~3.10–3.11 s after workload termination
- **CPU metric recovery:** ~1.12 s | **Memory metric recovery:** ~1.29–1.34 s
- **Process attribution (disk runs):** 36 correct / 0 wrong / 3 abstentions; reliability = 1.0 on decided cases
- **Confidence calibration:** 39/39 POTENTIAL/LOW diagnoses correctly identified the stressed resource

---

## Key Limitations

1. **CPU and memory confirmation not exercised**: Safety limits prevented workloads from reaching confirmation thresholds. Confirmed detection requires oversubscribed CPU workers and higher RAM allocation percentages.
2. **Disk saturation not confirmed**: The NVMe device's high throughput (>2,000 MB/s) and low latency (0.3 ms) prevented the saturation threshold (30 ms await) from being reached under 128 MB scratch limits.
3. **Non-root I/O attribution**: Per-process disk I/O tracking requires `/proc/<pid>/io` read access, restricted to process owners. Foreign process disk attribution is unavailable without elevated privileges.
4. **Single-host, block-storage scope**: No network or GPU bottleneck monitoring.

---

## Final Conclusion

Bottleneck Detective V2 successfully demonstrates zero-false-alarm detection across 250 metric samples, correct disk I/O potential detection in approximately 3.05 seconds, accurate alert clearance, and correct process attribution with zero wrong decisions across 36 decided cases. The 15-experiment campaign validates system stability and resource recovery under conservative safety constraints.

The campaign honestly reveals that confirmed CPU and memory bottleneck detection requires more intensive workloads than the safety-bounded campaign could provide. This is a valid experimental finding: it identifies exactly what hardware capacity and workload conditions are needed to exercise those detection paths, providing clear direction for future experiments.

The system is ready for academic review and final packaging.
