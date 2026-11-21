# Linux Bottleneck Detective V2: Comprehensive Viva Defense Guide

**Operating Systems Project Defense & Viva Voce Reference**  
**Repository:** `Linux-Bottleneck-Detection`  
**Target Audience:** OS Viva Examiners, Faculty Evaluators, System Engineering Defense

---

## 1. Process Management & Lifecycle

### Q1: How does Linux represent processes in the kernel, and how does Bottleneck Detective read them?
**Answer:** The Linux kernel represents each process using `struct task_struct` maintained in the kernel task list. User-space utilities access this state via the pseudo-filesystem `/proc/<pid>/`. Bottleneck Detective reads `/proc/<pid>/stat` (for CPU jiffies, process state, start time, parent PID) and `/proc/<pid>/status` / `/proc/<pid>/statm` (for resident set size `VmRSS` and virtual memory size `VmSize`). Per-process I/O counters are obtained from `/proc/<pid>/io` (bytes read and written via syscalls).

### Q2: What are the primary Linux process states, and why does uninterruptible sleep (D state) matter for load average?
**Answer:** The primary states are:
- **`R` (TASK_RUNNING):** Currently executing on a CPU or waiting in a scheduler run queue.
- **`S` (TASK_INTERRUPTIBLE):** Sleeping, waiting for an event or signal (e.g., waiting for user input or timer).
- **`D` (TASK_UNINTERRUPTIBLE):** Sleeping, but cannot be interrupted by signals; almost always waiting for disk I/O, device driver response, or page-in from swap.
- **`T` (TASK_STOPPED / TASK_TRACED):** Suspended by SIGSTOP or debugger.
- **`Z` (TASK_DEAD / EXIT_ZOMBIE):** Terminated, waiting for parent to collect exit status via `waitpid()`.

**Critical OS Nuance:** Unlike traditional Unix, the Linux load average calculation includes both tasks in `R` state and tasks in `D` state. Therefore, a high load average does not necessarily mean high CPU demand—it often indicates tasks blocked waiting for slow storage or network filesystems.

### Q3: Why is PID alone insufficient as a unique process identifier? How does the engine solve PID recycling?
**Answer:** PIDs are finite integers allocated from `/proc/sys/kernel/pid_max` (typically 32,768 or 4,194,304). On busy systems, short-lived processes (compilers, build scripts, containers) terminate and their PIDs are recycled by the kernel. If a monitoring tool indexes processes solely by PID, a newly created process inheriting an old PID will erroneously inherit the historical resource consumption of its predecessor.  
**V2 Solution:** The engine identifies every process by the composite tuple:
$$\text{Identity} = (\text{PID}, \text{start\_time})$$
where `start_time` is the process creation timestamp in clock jiffies since system boot (field 22 of `/proc/<pid>/stat`). When a PID is recycled, its `start_time` changes, causing the `ProcessTracker` to immediately purge historical attribution for that slot.

### Q4: How does the workload supervisor ensure child processes never become orphaned if the supervisor is killed?
**Answer:** The project implements three layers of defense:
1. **Internal Worker Deadline:** Every worker process computes an absolute wall-clock deadline (`time.monotonic() + seconds + 3.0`) and self-terminates when reached.
2. **`PR_SET_PDEATHSIG`:** Upon startup, each worker invokes `prctl(PR_SET_PDEATHSIG, SIGTERM)`. If the parent supervisor process dies (even via `SIGKILL`), the Linux kernel automatically delivers `SIGTERM` to all child workers.
3. **Dedicated Process Groups:** Workloads are spawned with `start_new_session=True` or `setpgid()`. The supervisor terminates entire process groups (`os.killpg()`), verifying group ownership before signaling so foreign processes are never touched.

---

## 2. CPU Scheduling & Queuing

### Q5: How is CPU utilization calculated from `/proc/stat`? Why is a single read meaningless?
**Answer:** Cumulative ticks (jiffies since boot) for each CPU state (`user`, `nice`, `system`, `idle`, `iowait`, `irq`, `softirq`, `steal`) are exported in `/proc/stat`. Because these counters are monotonic cumulative totals, a single read provides only the total uptime since boot. To compute instantaneous utilization, two reads separated by time $\Delta t$ are required:
$$\Delta \text{Total} = \sum_{\text{all fields}} (c_{t_2} - c_{t_1}), \quad \Delta \text{Idle} = (\text{idle}_{t_2} - \text{idle}_{t_1}) + (\text{iowait}_{t_2} - \text{iowait}_{t_1})$$
$$\text{CPU Utilization (\%)} = \left( \frac{\Delta \text{Total} - \Delta \text{Idle}}{\Delta \text{Total}} \right) \times 100$$

### Q6: How does the engine measure CPU run-queue wait time without polling?
**Answer:** When the kernel is compiled with `CONFIG_SCHEDSTATS`, `/proc/schedstat` exports per-CPU scheduler statistics. Field 8 of CPU records tracks the cumulative nanoseconds that runnable tasks spent waiting on the run queue before being scheduled onto a core. The engine calculates:
$$\text{runq\_wait\_per\_core} = \frac{\sum_{\text{cpu}} \Delta \text{wait\_nanoseconds}}{\Delta t \times 10^9 \times N_{\text{cores}}}$$
If `schedstat` is unavailable, the engine gracefully falls back to sampling `procs_running` from `/proc/stat`, computing runnable surplus as $(procs\_running - 1) / cores$.

### Q7: What is a CPU bottleneck? If CPU utilization is 100%, is the system definitely bottlenecked?
**Answer:** A CPU bottleneck occurs when runnable tasks must wait in the kernel scheduler's run queue because all CPU execution contexts are occupied by other tasks. This manifests as elevated run-queue wait time (`runq_wait_per_core`), rising CPU Pressure Stall Information (`PSI some`), and application-perceived latency increases.

**No**, 100% CPU utilization does not imply a bottleneck. If an application utilizes 100% of all cores with zero runnable tasks waiting in scheduler queues and zero Pressure Stall Information stalls, the system is executing at optimal computational throughput. A bottleneck occurs only when tasks that are ready to run are delayed because capacity is saturated. Bottleneck Detective V2 categorizes 100% CPU with empty queues as **`ELEVATED`** (`INFO` level), reserving **`CONFIRMED`** for situations with elevated run-queue latency and kernel PSI stalls.

### Q8: What does context switch rate indicate, and why is it treated as supporting evidence only?
**Answer:** A context switch involves saving the current task's architectural registers, program counter, and stack pointer, updating kernel scheduling structures, and loading the new task's state and memory translation tables (CR3 register / MMU page tables). High context switch rates indicate scheduler churn, lock contention, or thread thrashing. However, high context switching can also occur in healthy asynchronous I/O architectures (e.g., event loops). Therefore, Bottleneck Detective V2 treats context switch rates per core strictly as **supporting evidence** to corroborate CPU contention, never as an isolated alert trigger.

---

## 3. Virtual Memory & Paging

### Q9: What is memory pressure? Why does the engine use `MemAvailable` rather than `MemFree` to assess it?
**Answer:** Memory pressure is the condition in which the system's demand for physical RAM approaches or exceeds the available supply, forcing the kernel's memory management subsystem to reclaim page cache, compress memory, or swap pages to disk. When memory pressure is severe, tasks stall waiting for pages to be freed.

In the Linux virtual memory architecture, `MemFree` represents completely unallocated physical RAM. However, the kernel deliberately populates unused memory with file-backed page cache (`Cached`), buffer cache (`Buffers`), and reclaimable slab objects (`SReclaimable`) to accelerate I/O. If application demand increases, the kernel's memory management subsystem reclaims this cached memory without swapping. `MemAvailable` (introduced in Linux 3.14) provides an accurate kernel estimate of physical memory that can be allocated immediately without forcing the system into swap thrashing. Evaluating memory based on `MemFree` generates persistent false positives.

### Q10: What is the critical distinction between swap *usage* and swap *activity*?
**Answer:** 
- **Swap Usage:** The static volume of data residing in the swap partition or swap file. Inactive or abandoned memory pages (e.g., daemons initialized at boot that are never referenced) can reside in swap indefinitely with zero performance penalty.
- **Swap Activity:** The real-time rate of swapping pages out (`pswpout` in `/proc/vmstat`) and, crucially, swapping pages in (`pswpin`). Active swap-in indicates that currently referenced working sets exceed physical RAM capacity, causing page faults that require disk I/O to resolve. This condition (thrashing) halts useful thread execution. The detector triggers memory saturation only when swap activity occurs while physical memory is constrained.

### Q11: What is the Linux Out-Of-Memory (OOM) Killer, and how does the experiment harness interact with it?
**Answer:** When physical memory and swap are completely exhausted and page cache reclaim fails, the kernel invokes the OOM Killer to prevent a full system freeze. The kernel computes an `oom_score` for each process based on physical memory consumption and adjusts it using `/proc/<pid>/oom_score_adj` (ranging from -1000 to +1000).  
**Harness Safeguard:** In the experiment harness, memory workers explicitly write `1000` to `/proc/self/oom_score_adj`. This designates the stress worker as the kernel's preferred sacrifice if memory reaches critical limits, protecting desktop processes and system daemons.

---

## 4. Block Storage & I/O

### Q12: What is disk I/O contention? Why is device `%util` from `/proc/diskstats` unreliable on modern SSD and NVMe drives?
**Answer:** Disk I/O contention occurs when I/O requests are submitted to a storage device faster than it can service them, causing requests to queue and application threads to stall waiting for I/O completion.

Device `%util` measures the percentage of time during which at least one I/O request was in flight. On legacy mechanical hard drives (HDDs) that process one request at a time via a physical head, `%util = 100%` correlates directly with hardware saturation. However, modern solid-state drives (NVMe) feature deep hardware command queues (e.g., 64,000 queues with 64,000 commands each) and execute requests concurrently across parallel NAND channels. An NVMe drive handling 1 request continuously reports `%util = 100%`, yet has vast remaining throughput and IOPS capacity. The engine therefore correlates `%util` with average request latency (`await`), average queue depth (`aqu-sz`), and I/O Pressure Stall Information (`psi/io`).

### Q13: How does the engine compute per-device `await` latency?
**Answer:** In `/proc/diskstats`, field 4 (`read_ticks`) and field 8 (`write_ticks`) track cumulative milliseconds spent processing reads and writes, while field 1 (`reads_completed`) and field 5 (`writes_completed`) track total I/O count. The engine computes:
$$\text{await} = \frac{\Delta \text{read\_ticks} + \Delta \text{write\_ticks}}{\max(1, \Delta \text{reads\_completed} + \Delta \text{writes\_completed})} \quad [\text{ms}]$$
This metric represents the average time an I/O request spent from submission to completion (queue latency + device service time).

### Q14: What is the difference between device-level saturation and application-level I/O waiting?
**Answer:** 
- **Device-Level Saturation:** A physical block device exhibits elevated `%util`, high `await` latency, and deep queue depth, while tasks stall in I/O wait. Hardware capacity is saturated.
- **Application-Level I/O Waiting:** Tasks experience significant I/O stalls (measured via `psi/io` or high `iowait`), but no individual block device exhibits high utilization or queue depth. This occurs during frequent synchronous `fsync()` flushes (where threads block waiting for cache flushes despite low data volume), cgroup I/O throttling, or delays over network/virtual filesystems (NFS, overlayfs). The engine distinguishes these two regimes cleanly in its diagnoses.

---

## 5. Linux Kernel Pressure Stall Information (PSI)

### Q15: What is Linux PSI? What is the difference between `some` and `full` stalls?
**Answer:** Kernel Pressure Stall Information (introduced in Linux 4.20) directly quantifies the productivity lost to resource starvation across CPU, Memory, and I/O:
- **`some`:** The percentage of wall-clock time during which at least one non-idle task was stalled waiting for that resource.
- **`full`:** The percentage of wall-clock time during which *all* non-idle tasks were simultaneously stalled on the resource. When `full` stall occurs, no useful compute work is being performed by any task.
- *Note on CPU:* System-wide `cpu.full` is not defined because at least one task is always executing if CPU is busy; CPU only exports `some`. Memory and I/O export both `some` and `full`.

### Q16: Why does the detector calculate interval-exact PSI from `total` rather than using `avg10`?
**Answer:** The kernel exports smoothed moving averages: `avg10`, `avg60`, and `avg300`. Because `avg10` is an exponentially damped 10-second window, it exhibits significant lag: it requires several seconds to rise after stress begins and continues to show high stall percentages long after the workload has ceased. The engine calculates:
$$\text{Interval Stall (\%)} = \frac{\Delta \text{total\_microseconds}}{\Delta t \times 10^6} \times 100$$
This interval-exact delta responds immediately at the onset of contention and recovers to zero the instant tasks stop waiting.

### Q17: What is the difference between resource utilization and resource contention? Why does this distinction matter?
**Answer:** 
- **Utilization** measures how busy a resource is (percentage of time it was in use). A CPU at 80% utilization means 80% of the measurement interval had work executing.
- **Contention** (or saturation) measures whether demand *exceeds* capacity — specifically, whether tasks had to *wait* to access the resource. Contention is measured by run-queue wait time, PSI stall percentages, and I/O queue depth.

A resource can be highly utilized without contention (8 workers on 36 cores: busy but no queuing). It can also experience contention at moderate utilization if many tasks compete for a bottleneck (e.g., all writing to the same disk sector). This distinction is fundamental: Bottleneck Detective V2 requires evidence of contention (waiting, stalling) — not merely high utilization — before confirming a bottleneck.

---

## 6. Architecture & Detection Engine Design

### Q18: Why use a 3-of-5 persistence filter instead of alerting on individual spikes?
**Answer:** Real-world operating systems exhibit frequent transient resource spikes: a browser rendering a webpage, a compiler opening header files, or a periodic cron job. These spikes naturally subside in 1–2 seconds without degrading overall service. Requiring an indicator to persist in $\ge 3$ of the last 5 samples filters out high-frequency noise while guaranteeing a deterministic detection response time of approximately 3 seconds at a 1-second sampling interval.

### Q19: Why are Severity and Confidence modeled as separate, orthogonal dimensions?
**Answer:** 
- **Severity** reflects the magnitude of the overload: how far past safe operating thresholds the system has been pushed (`INFO`, `WARNING`, `CRITICAL`).
- **Confidence** reflects the consistency and corroboration of independent kernel data streams (`LOW`, `MEDIUM`, `HIGH`).
A diagnosis can be `CRITICAL` in severity (e.g., 99% CPU utilization) but have `LOW` confidence if only one sensor registered it and PSI stalls are absent. Conversely, a moderate overload with agreeing CPU, run queue, and PSI telemetry yields `HIGH` confidence. Conflating them hides critical diagnostic context from operators.

### Q20: Why use Median and MAD for baselines instead of Mean and Standard Deviation?
**Answer:** The arithmetic mean ($\mu$) and standard deviation ($\sigma$) are parametric statistics that are easily skewed by extreme outliers. In a performance monitor, the very bottleneck spikes being detected would artificially inflate the mean and expand the standard deviation, blinding the detector to subsequent anomalies. The Median ($\tilde{x}$) and Median Absolute Deviation ($\text{MAD}$) are non-parametric estimators with a breakdown point of 50%, meaning that up to half the sample window can consist of extreme outliers without corrupting the baseline.

### Q21: What critical bug was uncovered by the negative control (`cpu-benign`)?
**Answer:** During initial development of the baseline engine, an idle system established a run-queue median of 0. When `cpu-benign` executed (pegging cores at 100% with exactly 1 task per core), `procs_running` shifted from 0 to 1 per core. Statistically, this produced a modified $Z$-score greater than $+30$, causing the baseline engine to falsely flag CPU contention. The negative control exposed that statistical deviation alone is insufficient: every metric family requires an **absolute floor** ($\text{min\_abs\_floor}$) in addition to $Z$-score and minimum delta, ensuring that normal, fully occupied core execution is never mistaken for queue starvation.

---

## 7. Campaign Results — Specific Questions

### Q22: Why did the CPU stress experiments not produce confirmed CPU bottleneck detection?
**Answer:** The CPU stress experiments used 12 workers at reduced priority (`nice 10`) on a 36-core host. With 12 workers and 36 available cores, the kernel scheduler had ample execution capacity: no thread needed to wait in a run queue, so no scheduling stalls occurred. The measured CPU utilization was approximately 37–41% and CPU PSI was approximately 0.25–1.04%. The confirmation thresholds require CPU utilization ≥ 90% AND CPU PSI ≥ 10%. Neither was approached.

This is a consequence of the safety-constrained campaign design, not a detector failure. To trigger confirmed CPU contention, the number of workers must exceed the core count to force oversubscription. Future experiments would need to relax the 12-worker cap (with appropriate safeguards) or use a host with fewer cores.

### Q23: Why did the memory stress experiments not produce confirmed memory bottleneck detection?
**Answer:** The memory workload was capped at 10% of available RAM (~5.7 GB) on a host with approximately 57 GB available. This left more than 50 GB of RAM freely available. The kernel's memory management subsystem did not need to invoke page reclamation, compression, or swap. Measured memory PSI remained at exactly 0.0% and swap-out activity was 0 B/s throughout all three runs. The confirmation thresholds require either memory PSI some ≥ 1% or non-zero swap-out activity. Neither condition was satisfied.

As with CPU, this reflects the conservative safety boundaries rather than a detector deficiency. Creating genuine memory pressure requires allocating a substantially higher fraction of RAM — typically 60–80% or more of available capacity.

### Q24: Why were disk alerts classified as POTENTIAL instead of CONFIRMED?
**Answer:** `CONFIRMED` disk I/O detection requires evidence of genuine storage saturation — specifically, that average request latency exceeds the saturation threshold (30 ms) in sufficient samples, and/or that I/O PSI stalls exceed their threshold (20%). During the disk stress experiments, the NVMe device (nvme0n1) demonstrated outstanding throughput (~2,238–2,373 MB/s combined) and minimal request latency (0.3–0.4 ms average). Although utilization reached 62–64% and queue depth reached approximately 6.55, the latency and stall evidence was absent (latency 0.3 ms vs. 30 ms threshold). Without these saturation indicators, the detector correctly classified the state as `POTENTIAL` — disk activity and queueing were observed, but the device was not saturated.

### Q25: What does `INEFFECTIVE_STRESS` mean as a campaign verdict?
**Answer:** `INEFFECTIVE_STRESS` means that the experiment completed successfully, but the workload did not create sufficient resource pressure to satisfy the confirmation thresholds required to exercise the detector's confirmed bottleneck classification path. The experiment infrastructure, safety mechanisms, and detector all functioned correctly. The workload was simply too mild (relative to the available hardware capacity) to generate the kernel-level stalls that confirmed detection requires.

**Key distinction:** `INEFFECTIVE_STRESS` describes the workload, not the detector. The detector correctly remained in `NORMAL` or low-level states because there genuinely was no contention to detect.

### Q26: What does `PARTIAL` mean as a campaign verdict?
**Answer:** `PARTIAL` means the workload created measurable resource pressure (the stress was effective enough to register activity), and the detector identified potential activity by raising `POTENTIAL` alerts — but the evidence did not escalate to `CONFIRMED` because one or more saturation or stall indicators fell below their confirmation thresholds. The detector partially validated its detection path: it correctly identified disk pressure, but the hardware handled the load efficiently enough that full saturation confirmation was not established.

### Q27: Does 15/15 experiments completed mean all three bottleneck types were detected?
**Answer:** **No.** Experiment completion and bottleneck detection are independent outcomes. All 15 experiments completed without error or crash. However:
- Control and CPU-benign experiments were designed to produce zero alerts (and did).
- CPU and memory experiments did not create enough pressure to trigger even `POTENTIAL` alerts.
- Disk experiments triggered `POTENTIAL` alerts but not `CONFIRMED`.

Therefore, confirmed bottleneck detection was **not** demonstrated for any of the three resource types during this campaign. The campaign validated false-positive avoidance, disk POTENTIAL detection, and system stability — which are meaningful and important results, but distinct from confirmed contention detection.

---

## 8. False Positives & Attribution

### Q28: What is a false positive in the context of a bottleneck detector?
**Answer:** A false positive occurs when the detector generates a `POTENTIAL` or `CONFIRMED` bottleneck alert during a period when the specified resource is not actually experiencing contention. For example, if the detector reports `CONFIRMED CPU contention` while the system is idle, that is a false positive. False positives cause operator alert fatigue, erode trust in the monitoring system, and lead to unnecessary remediation effort.

### Q29: How was the false-positive observation calculated in this campaign?
**Answer:** The campaign framework pools metric samples collected during phases where no bottleneck is expected: idle baseline phases at the start of every experiment, the full 30-second window of each control experiment, and the stress phase of cpu-benign experiments (where CPU utilization was elevated but not contended). Any `POTENTIAL` or `CONFIRMED` alert generated during these phases is counted as a false positive. Across the 15-experiment campaign, the total pool was **250 samples** with **0 false positives**, yielding an observed false-positive rate of 0.00%.

> **Important:** This is the observed rate within this specific campaign context. It does not constitute a universal guarantee of zero false positives under all possible workload conditions.

### Q30: How does process attribution work?
**Answer:** Process attribution identifies which process is most likely responsible for an observed bottleneck. The engine uses a persistence-based approach:
1. It monitors all processes across the 5-sample persistence window.
2. A process is considered a candidate only if it appeared in at least 3 of the 5 samples **and** exceeded the resource attribution floor (e.g., ≥ 20% CPU of one core, ≥ 1 MB/s disk I/O).
3. If a single process accounts for ≥ 50% of load, it is attributed as `primary`. If 15–49%, it is `partial`. If multiple processes share load, the verdict is `multiple`. If load is diffuse or rotates, the verdict is `none`.
4. Attribution is tracked using the `(PID, start_time)` identity to prevent false attribution when PIDs are recycled.

### Q31: What does an attribution abstention mean?
**Answer:** An abstention occurs when the detector generates an alert but cannot confidently identify a single responsible process. This happens when the top contributor to I/O or CPU changes across the 5-sample persistence window — no single process was consistently dominant. During the disk experiments, abstentions occurred because I/O responsibility alternated between the monitoring agent (`agy`) and the Python workload workers across different samples. An abstention is an honest, explicit result: the engine reports that no single process was persistently responsible, rather than guessing or blaming whichever process was at the top at the final sample.

---

## 9. System Safety & Campaign Design

### Q32: How was system safety maintained during the experiments?
**Answer:** Multiple safety mechanisms were applied:
- **No root/sudo**: All experiments ran under a normal unprivileged user account, preventing access to system-wide modification.
- **Worker count cap**: CPU workers capped at 12 on a 36-core machine to prevent oversubscription.
- **Reduced scheduling priority**: CPU workers ran at `nice 10`, yielding execution priority to system processes.
- **Memory cap**: Memory allocation limited to 10% of available RAM with a minimum free-memory floor of ~3.1 GB.
- **OOM designation**: Memory workers set `oom_score_adj=1000`, making them the first candidate for OOM termination.
- **Disk isolation**: Each disk experiment used an exclusive, isolated scratch directory under `/var/tmp` with full cleanup after completion.
- **Worker self-termination**: Workers used absolute wall-clock deadlines and `PR_SET_PDEATHSIG` to self-terminate even if the supervisor was killed.
- **Campaign locking**: A single-instance lock prevented concurrent campaign runs.

### Q33: Why was sudo not used?
**Answer:** Running with elevated privileges (root or sudo) would create several risks:
1. Errors in workload scripts could damage system files, kernel state, or other users' processes.
2. An automated, unattended campaign with root access could cause irreversible system harm if a bug caused resource exhaustion beyond intended limits.
3. It is a security principle of least privilege: experiments should only use the minimum privileges required. All the resource pressure conditions the detector needs to observe are achievable as a normal user. The only capabilities lost are reading other users' `/proc/<pid>/io` files, which is acknowledged as a limitation.

### Q34: How were temporary disk files cleaned up?
**Answer:** Each disk experiment created an exclusive scratch directory with a unique identifier under `/var/tmp`. After the stress phase completed, the experiment harness explicitly deleted all files created within that directory and removed the directory itself. The cleanup was verified by checking `leftover_pids` and `cleanup.ok` in the report JSON. All 15 experiments reported `cleanup.ok: true` and zero leftover workload PIDs.

### Q35: How was resource recovery measured?
**Answer:** Recovery was measured using two separate metrics:
1. **Detector clearance time (`detector_clear_s`):** Time from workload termination to the detector returning to `NORMAL` (i.e., the alert state clearing). Measured only when alerts were raised (disk runs only: ~3.10–3.11 s).
2. **Metric recovery time (`metric_back_near_baseline_s`):** Time from workload termination until the primary resource metric (CPU%, memory%, disk util%) returned to within a small fraction of the pre-stress baseline. Measured for CPU (~1.12 s) and memory (~1.29–1.34 s) runs even though no alerts were raised.

---

## 10. Limitations & Future Work

### Q36: What are the current limitations of the system?
**Answer:**
1. **Non-root I/O visibility**: Cannot read `/proc/<pid>/io` for processes owned by other users, limiting disk attribution accuracy.
2. **Asynchronous writeback**: Buffered writes are committed to the page cache immediately but physically written to disk later by kernel workers, creating a temporal disconnect in disk attribution.
3. **Hardware scope**: Only CPU, memory, and block I/O are monitored. Network, GPU, and hardware accelerator bottlenecks are not covered.
4. **Single-host**: The system does not address distributed or multi-host bottlenecks.
5. **Campaign workload constraints**: The safety-limited campaign could not exercise confirmed CPU and memory detection paths on a 36-core, 57 GB system.

### Q37: How could future experiments better exercise the CPU and memory bottleneck detection paths?
**Answer:**
- **CPU**: Use worker counts exceeding the core count (e.g., 72+ workers on a 36-core host) to force oversubscription and run-queue stalls. Alternatively, use a smaller machine with fewer cores (e.g., a 4-core VM) where even 8–12 workers create contention. Workers should run at normal priority rather than `nice 10`.
- **Memory**: Allocate 60–80% of available RAM with active random access patterns to force kernel page reclamation and trigger memory PSI stalls. The 10% cap was appropriate for safety in this campaign but is too conservative to create measurable pressure on a 57 GB system.
- **Disk**: Increase the number of workers (e.g., 8–16) and block size, or use a slower physical disk (HDD or slow SSD) where 128 MB writes genuinely saturate the device.

### Q38: Why should workload intensity not be increased without safety checks?
**Answer:** Increasing workload intensity without safety boundaries risks:
1. **CPU**: Overloading a production machine, causing severe latency for other processes and users.
2. **Memory**: Triggering the OOM Killer for critical system processes, causing service failures or data corruption.
3. **Disk**: Exhausting disk space on shared partitions (e.g., `/var`) or creating I/O starvation for system processes that depend on the same device.

Any increase in workload intensity must be paired with updated safety guards: memory floors, disk space checks, worker count limits relative to core count, and OOM safeguards. The conservative limits in the current campaign are a deliberate design choice for safe, unattended execution on shared infrastructure.

---

## 11. Viva Defense Strategy & Live Demo Walkthrough

### The 3-Minute Demo Flow
1. **Initial Baseline:** Start `main.py` and `streamlit run dashboard/app.py`. Show the green `NORMAL` status across CPU, Memory, and Disk.
2. **Negative Control:** Execute `.venv/bin/python experiment.py run cpu-benign`. Show CPU utilization rising to ~27–28%, while the dashboard displays `ELEVATED` or `NORMAL`. Emphasize: *Moderate CPU use is not a bottleneck; run queues and PSI stalls remain near zero.*
3. **Disk Contention:** Execute `.venv/bin/python experiment.py run disk`. After exactly 3 seconds, the Disk I/O panel transitions to `POTENTIAL`. Point out the evidence table: utilization ~63%, queue depth ~6.5, throughput ~2,342 MB/s. Also highlight the **contradicting evidence**: latency 0.3 ms (threshold 30 ms), explaining why the state is POTENTIAL and not CONFIRMED.
4. **Recovery Event:** Workload terminates; demonstrate the alert clearing within ~3.1 seconds and the clean return to `NORMAL`.
5. **Report Artifact:** Show the generated `report.json` to illustrate automated, machine-readable evidence recording.

### Key Talking Points for Examiners

**If asked why CPU results show INEFFECTIVE_STRESS:**
> "The safety-constrained campaign limited CPU workers to 12 on a 36-core host. This is insufficient to create oversubscription. The detector behaved correctly — there was no contention to detect. Future experiments need worker counts exceeding the core count."

**If asked whether the campaign proved the detector works:**
> "The campaign validated several important properties: zero false alarms across 250 samples, correct disk POTENTIAL detection in 3.05 seconds, correct recovery within 3.11 seconds, and correct process attribution with zero wrong decisions in 36 decided cases. It did not exercise the confirmed CPU and memory detection paths, which requires more intensive workloads. The existing unit test suite of 189 tests validates those code paths synthetically."

**If asked about the 39/39 precision figure:**
> "That figure refers to confidence calibration, not process attribution. All 39 sample-level POTENTIAL/LOW diagnoses issued during stress phases correctly identified the right resource (disk I/O was being stressed, and disk I/O was the diagnosed resource). Process attribution — identifying which specific process is responsible — recorded 36 correct decisions, 0 wrong, and 3 abstentions."

**If asked why disk alerts stayed at POTENTIAL:**
> "The NVMe device is extremely fast: average request latency was 0.3 ms, well below the 30 ms saturation threshold. The confirmation threshold also requires I/O PSI some ≥ 20%, while we observed only 4–5%. The hardware handled our bounded 128 MB workload efficiently. POTENTIAL is the correct classification when utilization and queue activity are present but saturation latency and stall evidence are absent."
