# Project Guide: review, viva prep, report and slide outline

## 1. Review of the proposal (issues I fixed or you should know about)

| # | Issue in the proposal | What the code does / what you should do |
|---|---|---|
| 1 | `--vm-bytes 70%` will usually NOT push usage above the 90% rule, so the test shows no alert | Use `92%` with `--vm-keep`. On a small VM the OOM killer may fire, so use a VM you can reboot. |
| 2 | "Disk latency" is not a psutil field | Computed as `await = d(read_time+write_time) / d(read_count+write_count)`. `busy_time` gives %util. Both are Linux-only. |
| 3 | `stress-ng --hdd` writes to the current dir. If that is tmpfs/RAM, no disk I/O occurs | Use `--temp-path /var/tmp` and confirm with `df /var/tmp`. |
| 4 | Load average is described as CPU load | On Linux it also counts tasks in uninterruptible sleep (D state, usually I/O wait). Divide by core count and treat it as supporting evidence, not proof. |
| 5 | Process `cpu_percent` returns 0.0 on first call and can exceed 100 on multi-core | Counters are primed at startup; values are "top-style" (100 = one core). Rule threshold is 80 = one core nearly saturated. |
| 6 | PSI (`/proc/pressure`) is not always present (older kernels, some containers/WSL) | Reader returns NaN when absent; detector and dashboard degrade gracefully. |
| 7 | iowait is ambiguous | Counted as idle in CPU% (CPU isn't executing), but plotted separately so I/O stalls are visible. |
| 8 | "Context switching" is in the OS table but nothing measures it | `ctx_switches_s` is collected from `psutil.cpu_stats()`. |
| 9 | Scope: 9 objectives in 7 days is tight | See section 4. |

**V2 update.** The detection rules table that used to be here described V1 (plain thresholds). V2 replaced it with evidence-based rules:
see `docs/DETECTION_MODEL.md` (exact rules) and README sections 7-12. In short: utilisation alone is only ELEVATED; POTENTIAL/CONFIRMED
need PSI/saturation evidence that persists (3 of 5 samples); severity and confidence are separate; static thresholds plus a rolling baseline.

Items 6 and 8 above changed: PSI is now used for CPU, memory and I/O (`some`/`full`, interval-exact); `ctx_switches_s` is supporting evidence for CPU.
Item 9 (scope): V2 adds experiments, baseline detection and a larger test suite; keep the demo to the 3 experiments plus the negative control.

Run order: `python main.py`, then `streamlit run dashboard/app.py`, then `python experiment.py run cpu` (or the other experiments) in a third terminal.
Verification status is in README sections 13, 14 and 17 (what was measured, what could not be run here). In particular the Streamlit dashboard was not rendered in the build environment: run it and capture screenshots yourself.

## 2. Viva questions and answers

**Process management**
1. *What does the tool read for processes?* `/proc/<pid>/stat` and `/proc/<pid>/status` via psutil: PID, name, CPU time, RSS.
2. *Process states?* Running (R), sleeping (S, interruptible), uninterruptible sleep (D, usually waiting on disk), stopped (T), zombie (Z). D-state tasks inflate load average.
3. *Why report the top process, not just totals?* A bottleneck is only actionable once you know who causes it.

**CPU scheduling**
4. *How is CPU% computed?* Read `/proc/stat` twice; `((dTotal - dIdle)/dTotal)*100`. Counters are cumulative jiffies since boot, so a single read is meaningless.
5. *What is load average?* Exponentially damped average of runnable + uninterruptible tasks over 1/5/15 min. Compare with core count: load 4 on 4 cores is full, on 1 core is 4x oversubscribed.
6. *CPU% is 100 but is the system "bottlenecked"?* Only if work is waiting. Check load/core, PSI cpu, and whether one process is responsible.
7. *Context switch?* Saving one task's registers/state and loading another's. A very high rate with many runnable tasks indicates contention and wasted time.

**Memory / virtual memory**
8. *Why MemAvailable and not MemFree?* Free memory ignores reclaimable page cache. MemAvailable estimates what can be allocated without swapping.
9. *Why does swap activity matter more than swap usage?* Old idle pages sitting in swap are harmless. Continuous swap-in/out (thrashing) means the working set exceeds RAM and each fault costs a disk access.
10. *What is the OOM killer?* When reclaim fails, the kernel kills the process with the highest `oom_score` to keep the system alive.
11. *Paging vs swapping?* Paging moves individual pages; classic swapping moved whole processes. Linux pages.

**I/O management**
12. *What do util and await mean?* util = fraction of time the device had requests in flight; await = average time per I/O. High await with high util = saturated device. util near 100% on SSD/NVMe is less conclusive because they serve requests in parallel.
13. *What is PSI?* Kernel Pressure Stall Information: percent of time tasks were stalled waiting for CPU, memory, or I/O. `some` = at least one task stalled, `full` = all non-idle tasks stalled. Measures the harm directly, not just utilisation.
14. *Why not just use utilisation?* 90% utilisation with nobody waiting is fine; 60% with long queues is not. PSI and load capture waiting.

**Design**
15. *Why persistence rules (3 of 5)?* Prevent false alerts from transient spikes; trade-off is detection delay of about 6 seconds at a 2 s interval.
16. *Why read `/proc` through psutil?* `/proc` is a virtual filesystem the kernel generates on read; psutil parses it portably. Metrics.py reads `/proc/stat` directly to show the mechanism.
17. *Limitations?* See README section 17 (V2 has baselines, per-device disk stats and per-process I/O; remaining limits are listed there). More Q&A: `docs/VIVA.md`.

## 3. Report outline (≈ 25 pages)

1. Abstract. 2. Introduction and problem statement. 3. Objectives. 4. Literature/tools survey (top, vmstat, iostat, sar, PSI) and gap. 5. System architecture (diagram: kernel `/proc` → collectors → metrics → detector → CSV/JSON → dashboard). 6. OS concepts mapped to modules (table from proposal section 8). 7. Algorithms: CPU, memory, throughput formulas with a worked numeric example; detection rule table; persistence logic. 8. Implementation (module by module, key code excerpts). 9. Testing: unit tests, then stress-ng scenarios with screenshots, expected vs observed, false-alert discussion. 10. Results and discussion. 11. Limitations and future work. 12. Conclusion. References: Linux kernel docs for `/proc` and PSI, `man proc`, psutil docs, stress-ng man page, Silberschatz *Operating System Concepts*.

Evidence to capture: screenshot of healthy state, then each stress test with alert visible, the alerts.csv, and `reports/summary.md`.

## 4. Slide outline (10 slides) and 7-day plan

Slides: 1 Title; 2 Problem (manual interpretation of top/vmstat); 3 Objectives; 4 Architecture diagram; 5 OS concepts used; 6 Formulas and detection rules; 7 Persistence/false-alert handling; 8 Live demo (CPU, memory, disk); 9 Results/screenshots; 10 Limitations, future work, Q&A.

Demo script (3 min): see `docs/VIVA.md` section G (cpu-benign shows 'elevated, not a bottleneck', then cpu shows 'confirmed' with evidence, then recovery, then the experiment report).

Trimmed plan if time runs short (everything above already exists, so spend the days on testing and writing):
- Day 1: install, run the code, read every file so you can explain it.
- Day 2: unit tests; tune thresholds on your own machine.
- Day 3: stress tests, capture screenshots, fix false alerts.
- Day 4: dashboard polish and PSI check.
- Day 5-6: report.
- Day 7: slides, rehearse demo, viva questions.
Cut first if needed: report_generator, alert history expander, PSI chart. Never cut: persistence logic, process attribution, the negative-control experiment (cpu-benign), experiment evidence.
