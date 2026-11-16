# Viva preparation: Bottleneck Detective V2

Answers are written so you can defend them. Where the honest answer is "this is a limitation", it says so, because
examiners ask exactly those questions.

## A. The core idea
**1. What is the difference between utilisation, saturation, contention and degradation?**
Utilisation = how busy/full a resource is. Saturation = demand exceeds capacity, so work queues (run queue, swap, device queue).
Contention = tasks actually wait for it. Degradation = the waiting hurts useful work. The kernel's PSI measures the waiting directly.
V2 requires stall (waiting) evidence before it says CONFIRMED; utilisation alone is ELEVATED.

**2. Show me that 100% CPU is not necessarily a problem.** Run `python experiment.py run cpu-benign`: one busy worker per core, CPU ~100%,
PSI ~0, no run queue. The engine reports ELEVATED/INFO and zero POTENTIAL/CONFIRMED samples. Then `run cpu` (2 x cores workers): PSI jumps, the run queue grows, CONFIRMED within ~3 s.

**3. Why three states instead of alert/no alert?** Operators need to know *how sure* the system is. ELEVATED ("busy"), POTENTIAL ("some evidence, incomplete or contradicted"), CONFIRMED ("stall + corroboration, persistent"). Collapsing them is what produced the false positives of V1.

**4. Why are severity and confidence separate?** Severity is magnitude (how far past the limits, for the whole window); confidence is agreement between independent indicators. 98% CPU with a PSI of 60% but nothing else is severe and weakly corroborated. Mixing them hides that.

## B. Operating Systems concepts
**5. How is the run queue obtained?** `/proc/schedstat` gives, per CPU, cumulative nanoseconds tasks spent running and waiting for a CPU. The change in wait time divided by the interval is the average number of runnable-but-waiting tasks; per core it should be ~0. If schedstat is unsupported we fall back to `procs_running` from `/proc/stat` (instantaneous, noisier, includes our own process so we subtract 1).

**6. Why not use load average?** It counts runnable *and* uninterruptible (D state, mostly I/O) tasks and is exponentially damped, so it lags. We keep load/core only as supporting evidence.

**7. What is PSI exactly?** Kernel Pressure Stall Information (4.20+). `some`: percentage of time at least one non-idle task was stalled on the resource; `full`: all non-idle tasks stalled at once (no useful work). `avg10/60/300` are exponentially smoothed; `total` is cumulative microseconds. We compute the stall percentage of *our* interval from the change in `total`, which has no smoothing lag, so detection and recovery are faster than with avg10. `cpu full` is not meaningful system-wide and is ignored.

**8. Why is high memory use not a problem?** The kernel uses spare RAM for the page cache, which is reclaimable. `MemAvailable` estimates what can be allocated without swapping. We saw this: allocating 93% of RAM on a swapless VM produced **no** kernel stall, and the engine correctly said ELEVATED. Pressure shows as memory PSI, swap-in/out while RAM is nearly full, and major page faults.

**9. Why does swap-in matter?** A page swapped in again means the working set did not fit and a process faulted on it (a disk access in the middle of execution): thrashing. Idle pages sitting in swap are harmless.

**10. What does disk util mean and why not trust it alone?** `util` = fraction of time the device had at least one request in flight. A device that serves requests in parallel (SSD/NVMe, RAID) can show 100% without being saturated. So we also look at latency (await), queue depth (aqu-sz) and I/O PSI. High *throughput* alone is never a problem.

**11. Device saturation vs application-level I/O waiting?** Saturation: a device shows high util/latency/queue *and* tasks stall. Application-level: tasks stall on I/O (PSI, iowait) but no device is saturated: synchronous writes/fsync, network or overlay filesystem, cgroup I/O throttling.

**12. Why compute util per device and not an aggregate?** The V1 aggregate summed busy time across devices (can exceed 100% or hide one saturated disk). Now each device is computed separately and clamped to 0-100; the system-level number is the *max*; throughput sums only top-layer devices to avoid double counting stacked (LVM/RAID) devices.

**13. Why must counters be read twice?** `/proc/stat`, `diskstats`, `vmstat`, PSI totals are cumulative since boot. Rates = difference / measured interval. Counter resets (decrease) give "unavailable", never a negative or fake value.

## C. Attribution
**14. How do you know which process caused it?** We don't know; we find the processes that are *consistently* consuming the resource: same identity (PID + start time) in at least 3 of the last 5 samples and a minimum share of the load. We say "correlation, not proof". If the top process keeps changing we say "none" instead of blaming the last one.

**15. Why is PID alone not enough?** PIDs are recycled. A new process with an old PID would inherit its history. The start time (from `/proc/<pid>/stat`) makes the identity unique.

**16. Can you attribute disk I/O to a device?** No. `/proc/<pid>/io` is per process, not per device, and write bytes are counted when pages are dirtied, not when they reach the disk. For other users' processes it needs root. In those cases the result is "unavailable".

## D. Baseline and thresholds
**17. Why a baseline when you have thresholds?** One threshold cannot fit every machine. The baseline learns what is normal *here* (median and MAD, robust to spikes) and flags deviation; static thresholds remain the fallback and the authority when the baseline is cold.

**18. How do you stop the baseline learning the problem as normal?** (1) warm-up, (2) values that deviate or trip a static threshold are not learned, (3) nothing from a resource with an active finding is learned, (4) a persistent shift is adopted only if there is no contention evidence, (5) experiments freeze it. Remaining weakness: the first warm-up window is assumed normal.

**19. What bug did the negative control find?** With an idle baseline (run-queue median 0), a run queue of exactly 1.0 per core is "z = +30" statistically but only means every core is busy. The baseline flagged it and CPU was reported POTENTIAL during a benign CPU-bound run. Fix: every metric needs an absolute floor in addition to z-score and minimum delta. This is why we run negative controls.

## E. Validation
**20. How do you know the detector works?** Controlled experiments: baseline, bounded stress, recovery; the same engine analyses it. We measure detection, latency, false positives, recovery, diagnostic and attribution reliability. Results are in `data/experiments/` and README section 13.

**21. Is that proof?** No. n = 1 per experiment, one VM, and some alerts on other resources occurred. It is evidence. Use `python experiment.py suite --repeats N` for rates.

**22. The memory experiment is borderline. Why?** On a 4 GB VM without swap, allocating 95% of free RAM creates almost no kernel stall; we added bounded file-cache churn so reclaim happens. Memory PSI `full` then sits around 1.9% against a 2% threshold: sometimes confirmed, sometimes not. With the threshold set to 1.5 it is detected reliably (73% of stress samples). Lesson: thresholds are machine-dependent; the harness reports `INEFFECTIVE_STRESS` rather than blaming the detector when the kernel itself shows no stall.

**23. Why does the harness check whether the stress was effective?** Ground truth that does not use the detector: if raw kernel counters show no stall, a missed detection is not a detector failure.

**24. Is the confidence a probability?** No, it is a rule-based grade (see DETECTION_MODEL.md section 5). The calibration table in the suite report is a small sanity check, not a statistical calibration.

## F. Safety and design
**25. How do you make sure an experiment can't harm the machine?** Explicit start; bounded duration/workers/size; nice 10; no root; memory capped to 95% of *available* with a free-memory floor, cgroup-aware, worker marked first for the OOM killer; disk uses one bounded file per worker in a private directory, refuses system paths/tmpfs, keeps a free-space reserve; only process groups it created are signalled; workers self-terminate at a deadline and via `PR_SET_PDEATHSIG` if the supervisor dies (tested by SIGKILLing the supervisor).

**26. Why is the engine shared with the experiments?** So there is no special path: the experiment cannot make the detector look better than it is.

**27. What are the main limitations?** See README section 17: single host; heuristic thresholds; schedstat optional; per-process I/O limitations; dashboard not rendered in the build sandbox; stress-ng backend untested against the real binary; small experiment sample.

## G. 3-minute demo script
1. `python main.py` + `streamlit run dashboard/app.py`: healthy state. 2. `python experiment.py run cpu-benign`: dashboard shows blue *Elevated*, no bottleneck. 3. `python experiment.py run cpu`: turns red *Confirmed*, evidence list (PSI, run queue, utilisation), responsible process named, recommendation. 4. Workload stops: *Recovered*, then *Normal*. 5. Open the generated `report.md` and `timeline.png`.
