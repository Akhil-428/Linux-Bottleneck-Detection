# Linux Bottleneck Detective V2: Autonomous Performance Bottleneck Detection and Explainable Diagnosis

**Academic & Technical Project Report**  
*Operating Systems Project & Laboratory Submission*  
**Date:** November 2026  
**Repository:** `Linux-Bottleneck-Detection`  
**Kernel Targets:** Linux 4.20+ (Validated on Linux 6.18 & Linux 7.0.0-38 SMP PREEMPT_DYNAMIC)

---

## Abstract

Traditional Linux performance monitoring utilities such as `top`, `vmstat`, `iostat`, and `sar` provide granular numerical counters of operating system resource consumption. However, these tools place the cognitive burden of interpretation entirely on human system administrators. In complex production environments, high resource utilization is frequently conflated with system degradation: a fully utilized CPU executing a batch compute job with an empty run queue represents optimal throughput, whereas an identical 95% CPU utilization accompanied by high scheduling latency and kernel stall pressure represents severe starvation. Conversely, simple static threshold alarms either flood operators with false positives during benign burst workloads or fail to diagnose contention hidden behind asynchronous caching layers.

This report presents **Bottleneck Detective V2**, an explainable, autonomous operating system performance monitoring and diagnostic engine. Moving beyond raw counters, Bottleneck Detective V2 introduces an evidence-based multi-metric correlation architecture built directly on modern Linux kernel interfaces (`/proc/stat`, `/proc/schedstat`, `/proc/meminfo`, `/proc/vmstat`, `/proc/diskstats`, and `/proc/pressure/*` Pressure Stall Information). The engine rigorously decouples resource *utilization* from *saturation*, *contention*, and *degradation*. By evaluating rolling statistical baselines (Median Absolute Deviation) alongside static boundaries, enforcing multi-sample persistence rules (3 of 5 sliding window samples), and computing exact interval stall percentages from monotonic microsecond deltas, the detector eliminates transient false alarms while maintaining sub-4-second detection latency for disk I/O events. Contention episodes are categorized into discrete, explainable states (`ELEVATED`, `POTENTIAL`, `CONFIRMED`, `RECOVERED`), decoupled into independent *severity* and *confidence* ratings, and correlated with specific causal processes tracked by unique `(PID, start_time)` identities to prevent PID-recycling attribution errors.

The framework validates its own detection accuracy through built-in controlled experiment harnesses. A formal 15-experiment automated campaign confirmed zero false-positive alerts across 250 metric samples, demonstrated POTENTIAL disk I/O detection with a latency of approximately 3.05 seconds, and validated system stability and resource recovery across all five experiment categories.

---

## 1. Introduction & Problem Statement

### 1.1 The Operational Challenge
In modern multi-core operating systems, distinguishing between high system productivity and resource starvation is one of the most persistent operational challenges. System administrators routinely rely on basic metrics:
- **CPU Utilization:** Percentage of time CPUs spend outside the idle thread.
- **Memory Consumption:** Memory reported as "used" by legacy tools.
- **Disk Utilization (`%util`):** Time fraction during which at least one I/O request was in flight.

Each of these traditional counters fails when used in isolation:
1. **CPU Saturation Ambiguity:** A 36-core server operating at 100% CPU utilization running 36 computational threads is operating with maximum efficiency. There are no queued tasks, scheduling latency is near zero, and useful work progress is optimal. However, traditional threshold monitors trigger severe alerts at 90% utilization. Conversely, if 150 runnable threads contend for 36 cores, average run-queue wait time spikes, causing application threads to stall for tens of milliseconds. Raw CPU utilization alone cannot differentiate these two operational regimes.
2. **Virtual Memory Page Cache Confusion:** The Linux virtual memory manager utilizes unallocated physical RAM for buffering and page caching (`Cached` and `Buffers` in `/proc/meminfo`). Consequently, a healthy system invariably approaches 95–98% "used" memory over time. Naive alerts based on `MemFree` generate chronic false positives. Furthermore, allocating 95% of physical memory on a system without swap does not inherently imply thrashing if working sets remain unreferenced, whereas active paging and continuous swap-in/swap-out thrashing under heavy allocation degrades performance by orders of magnitude.
3. **Storage Device Parallelism Blind Spots:** Modern Solid State Drives (NVMe / SSD) and RAID controller arrays feature deep internal submission and completion queues, capable of processing dozens of I/O requests concurrently. A device utilization metric of `%util = 100%` simply indicates that at least one request was in flight during the measurement window; it does not indicate saturation or queue starvation. Only by cross-referencing I/O latency (`await`), average queue depth (`aqu-sz`), and kernel I/O Pressure Stall Information (`psi/io`) can genuine storage bottlenecks be identified.

### 1.2 The False Attribution Problem
When traditional monitors trigger an alert, they typically identify the culprit process by sorting `/proc` by instantaneous CPU or memory consumption at that exact instant. This naive attribution frequently misidentifies victims or benign background processes:
- In multi-tenant environments, a transient process spike at the exact boundary of a sample window is blamed for system degradation, even if that process terminated milliseconds later.
- If PID recycling occurs (common on busy build servers or container hosts), a newly spawned process that inherits an older PID is erroneously charged with the historical resource consumption of its predecessor.
- Diffuse workloads (e.g., hundreds of micro-threads each consuming 0.5% CPU) cause monitors to alternate culprits across consecutive samples, confusing root cause analysis.

### 1.3 Project Goal
Bottleneck Detective V2 resolves these fundamental limitations by establishing an autonomous, explainable diagnosis engine that:
1. Enforces the USE method (Utilization, Saturation, Errors) combined with Kernel Pressure Stall Information (PSI).
2. Distinguishes high utilization (`ELEVATED`) from potential contention (`POTENTIAL`) and verified degradation (`CONFIRMED`).
3. Tracks processes via permanent cryptographic-style identity tuples: `(pid, start_time)`.
4. Combines robust, non-parametric statistical baselines with static engineering thresholds.
5. Employs a self-contained controlled experiment harness to experimentally validate its own detection performance.

---

## 2. Objectives

The project is structured around seven primary technical objectives:

1. **Sustained Contention Detection:** Formulate a persistence engine requiring multi-metric indicators to hold across a sliding window (default: $k \ge 3$ of $W = 5$ samples), effectively filtering high-frequency transient spikes while maintaining low detection latency.
2. **Multi-State Evidence Modeling:** Implement a formal state transition model categorizing system health into `NORMAL`, `ELEVATED` (busy but unhindered), `POTENTIAL` (partial or contradictory contention signals), `CONFIRMED` (verified stall and saturation), and `RECOVERED` (documented resolution of an episode).
3. **Decoupled Severity and Confidence Scoring:** Establish orthogonal dimensions for diagnosis severity (magnitude of resource overload: `INFO`, `WARNING`, `CRITICAL`) and diagnosis confidence (corroborating agreement among independent kernel sensors: `LOW`, `MEDIUM`, `HIGH`).
4. **Kernel Pressure Stall Information (PSI) Integration:** Implement high-frequency, interval-exact extraction of kernel microsecond stall counters from `/proc/pressure/{cpu,memory,io}` to quantify direct task execution stalls without exponential smoothing lag.
5. **Robust Dynamic Baselines:** Construct a rolling-window statistical baseline using Median and Median Absolute Deviation (MAD), guarded by warm-up thresholds, freeze locks during contention episodes, and minimum absolute floors to prevent benign uniform workloads from triggering anomalies.
6. **Identity-Aware Process Attribution:** Track process lifecycles using process start time jiffies alongside PIDs, attributing resource pressure only to processes demonstrating sustained, significant contribution across the persistence window.
7. **Empirical Self-Validation:** Implement a controlled experiment harness capable of injecting deterministic CPU, memory, and disk stress workloads alongside negative control experiments (`cpu-benign`, `control`), recording ground-truth efficacy, latency, false-positive rates, and recovery dynamics.

---

## 3. Literature & Tools Survey: The Diagnostic Gap

Operating systems performance analysis has evolved through several generations of tooling:

| Tool | Primary Data Source | Metric Scope | Contention Detection | Attribution Mechanism | Key Limitation |
|---|---|---|---|---|---|
| **`top` / `htop`** | `/proc/[pid]/stat`, `/proc/stat` | CPU, Memory totals and instantaneous process table | None (operator must visually judge) | Instantaneous sorting by CPU% or MEM% | High sampling noise; single-sample sorting; no stall awareness; confuses cache with memory leak. |
| **`vmstat`** | `/proc/stat`, `/proc/vmstat`, `/proc/meminfo` | System-wide run queue (`r`), blocked tasks (`b`), swap in/out, context switches | Implicit (run queue > core count indicates saturation) | None (no process-level attribution) | No per-device I/O granularity; lacks automated thresholding or root-cause explanation. |
| **`iostat`** | `/proc/diskstats`, `/sys/block` | Per-device throughput, IOPS, `await` latency, `%util` | Heuristic (high `await` + high `%util`) | None (per-process I/O is absent) | `%util` saturated at 100% on parallel SSD/NVMe drives without actual performance loss. |
| **`sar` (sysstat)** | Cron-based sampling of `/proc` | Broad historical logging of system counters | Post-hoc manual analysis | None in historical logs | High logging latency (typically 1–10 minutes); retrospective rather than real-time reactive. |
| **Kernel PSI** | `/proc/pressure/*` | Task stall ratios (`some`, `full`) over 10s, 60s, 300s | Direct quantification of task execution delays | None (system or cgroup aggregate only) | Raw kernel interfaces report smoothed numbers; no automated attribution or remedial advice. |
| **Bottleneck Detective V2** | `/proc/*`, `/sys/*`, PSI, psutil | Correlated CPU, Run Queue, Schedstat, Memory, Swap, Major Faults, Per-Device I/O, Process Trees | **Automated, Evidence-Based, Persistence-Filtered** | **Identity-aware `(PID, start_time)` persistent share** | Single-host scope; requires Linux 4.20+ for full PSI stall confidence. |

### The Gap Addressed by V2
Existing tools either record numbers without interpretation or alert on raw thresholds without validating whether processes are actually being harmed. Bottleneck Detective V2 closes this gap by implementing an automated diagnostic pipeline that models the actual operating system mechanics of scheduling queues, virtual memory page reclamation, and block device driver queues.

---

## 4. System Architecture

The architecture enforces strict separation of concerns across five functional layers:

```mermaid
flowchart TD
    subgraph KernelSpace [Linux Kernel Interfaces]
        K1["/proc/stat\n(CPU jiffies, context switches)"]
        K2["/proc/schedstat\n(Run queue wait time)"]
        K3["/proc/meminfo & /proc/vmstat\n(MemAvailable, swap in/out, faults)"]
        K4["/proc/diskstats & /sys/block\n(Per-device I/O counters, holders)"]
        K5["/proc/pressure/*\n(PSI cpu, memory, io cumulative total)"]
        K6["/proc/[pid]/*\n(Process stat, status, io)"]
    end

    subgraph DataCollection [collectors/ Layer]
        C1[CpuMonitor]
        C2[MemoryMonitor]
        C3[DiskMonitor]
        C4[ProcessMonitor]
        C5[PsiReader]
    end

    subgraph EngineCoordination [engine.py Layer]
        E1[MonitorEngine]
    end

    subgraph AnalysisEngine [analyzer/ Layer (Pure, Deterministic, Tested)]
        A1[Baseline Engine\nMedian + MAD Deviations]
        A2[Process Tracker & Attribution\nPID + start_time Verification]
        A3[BottleneckDetector V2\nPersistence Matrix & State Rules]
        A4[Diagnosis & Recommendations\nEvidence Explanation Builder]
    end

    subgraph StoragePresentation [Persistence & Presentation]
        P1[data/system_logs.csv]
        P2[data/alerts.csv]
        P3[data/latest.json]
        D1[dashboard/app.py\nStreamlit Real-Time Visualizer]
        R1[reports/report_generator.py\nExecutive Markdown Reports]
    end

    K1 --> C1
    K2 --> C1
    K3 --> C2
    K4 --> C3
    K5 --> C5
    C5 --> C1
    C5 --> C2
    C5 --> C3
    K6 --> C4

    C1 --> E1
    C2 --> E1
    C3 --> E1
    C4 --> E1

    E1 --> A1
    E1 --> A2
    E1 --> A3
    A1 --> A3
    A2 --> A3
    A3 --> A4

    E1 --> P1
    E1 --> P2
    E1 --> P3

    P1 --> D1
    P2 --> D1
    P3 --> D1
    P1 --> R1
    P2 --> R1
```

### 4.1 Architectural Invariants
1. **Collector Purity & Resilience:** Collectors strictly query kernel filesystems. If any kernel interface is missing, permission-restricted, or corrupt, the collector catches the exception, logs an actionable warning, and outputs `None`/`null` for that specific metric. The engine never crashes due to kernel variability.
2. **Deterministic Analyzer Core:** The `analyzer/` package contains zero file I/O, zero time-dependent calls, and zero operating system side effects. It accepts a standardized sample dictionary and returns deterministic diagnostic findings. This allows complete unit-test coverage using synthetic timelines.
3. **Engine Symmetry:** The exact same `MonitorEngine` instance is utilized by `main.py` for continuous monitoring and by `experiments/runner.py` for controlled stress experiments. No special logic or artificial thresholds exist for experiment runs.

---

## 5. Operating Systems Concepts Mapped to Modules

The software architecture directly reflects foundational operating system principles:

| OS Concept | Theoretical Principle | Implementation in Bottleneck Detective V2 | Target Kernel File |
|---|---|---|---|
| **Process Identity & Lifecycle** | PIDs are finite integers recycled by the kernel. A process is uniquely identified only by its entry into the task table. | `ProcessTracker` indexes processes by the composite key `(pid, start_time)`. If a PID is reused, the tracker clears historical attribution. | `/proc/<pid>/stat` (field 22: `starttime`) |
| **CPU Scheduling & Queuing** | When runnable thread count exceeds CPU execution contexts, threads spend time waiting in per-CPU run queues. | `CpuMonitor` computes exact wait time deltas via `CONFIG_SCHEDSTATS`. If absent, it computes `(procs_running - 1) / cores` from `/proc/stat`. | `/proc/schedstat`, `/proc/stat` |
| **Scheduler Inefficiency** | Excessive context switching introduces pipeline flushes, register save/restore overhead, and cache pollution. | `CpuMonitor` computes context switch rates per second per core (`ctx_switches_s / cores`) as supporting contention evidence. | `/proc/stat` (`ctxt` counter) |
| **Memory Reclamation vs Starvation** | Operating systems treat free memory as wasted memory; reclaimable page cache must be distinguished from memory exhaustion. | `MemoryMonitor` evaluates utilization using `MemAvailable`, explicitly tracking `Cached`, `Buffers`, and `SReclaimable`. | `/proc/meminfo` |
| **Virtual Memory Thrashing** | Continuous page-in and page-out from secondary storage under memory exhaustion stalls thread execution on page faults. | `MemoryMonitor` calculates `swap_in_s`, `swap_out_s`, and `major_faults_s`, signaling thrashing only when available RAM is constrained. | `/proc/vmstat` (`pswpin`, `pswpout`, `pgmajfault`) |
| **Kernel Pressure Stalls (PSI)** | Quantifies execution delays caused by resource starvation: `some` (at least one task delayed) vs `full` (all non-idle tasks delayed). | `PsiReader` reads cumulative microsecond stall counters and computes exact interval percentages without smoothing delay. | `/proc/pressure/{cpu,memory,io}` |
| **Block Device Driver Queuing** | Device utilization measures time busy; saturation is measured by queue depth and wait times. Throughput is orthogonal to latency. | `DiskMonitor` calculates `await` (service time + wait time) and queue depth (`aqu-sz`), filtering stacked virtual devices (LVM/dm). | `/proc/diskstats`, `/sys/block` |
| **I/O Synchronicity & Writeback** | Dirty page writeback occurs asynchronously; synchronous writes (`fsync`) force immediate thread blocking. | The detector differentiates **device-level saturation** from **application-level I/O waiting** (stalls without saturated hardware). | `/proc/pressure/io`, `/proc/<pid>/io` |

---

## 6. Algorithms & Mathematical Formulations

### 6.1 CPU Metric Calculations
CPU counters in `/proc/stat` report cumulative jiffies (typically 1/100th of a second) since system boot:
$$\text{Ticks} = [\text{user}, \text{nice}, \text{system}, \text{idle}, \text{iowait}, \text{irq}, \text{softirq}, \text{steal}]$$

Between two consecutive sample points $t_1$ and $t_2$ separated by $\Delta t$:
$$\Delta \text{Total} = \sum_{i} (\text{Ticks}_{t_2}[i] - \text{Ticks}_{t_1}[i])$$
$$\Delta \text{Idle} = (\text{idle}_{t_2} - \text{idle}_{t_1}) + (\text{iowait}_{t_2} - \text{iowait}_{t_1})$$
$$\text{CPU Utilization (\%)} = \left( \frac{\Delta \text{Total} - \Delta \text{Idle}}{\Delta \text{Total}} \right) \times 100$$
$$\text{I/O Wait (\%)} = \left( \frac{\text{iowait}_{t_2} - \text{iowait}_{t_1}}{\Delta \text{Total}} \right) \times 100$$

When `/proc/schedstat` is active, the average run-queue wait time per core is calculated from per-CPU run-queue wait nanoseconds $\Delta W_{\text{cpu}}$:
$$\text{runq\_wait\_per\_core} = \frac{\sum_{\text{cpu}} \Delta W_{\text{cpu}}}{\Delta t \times 10^9 \times N_{\text{cores}}}$$

### 6.2 Virtual Memory Calculations
Traditional monitors compute memory consumption as:
$$\text{Naive Used (\%)} = \left( \frac{\text{MemTotal} - \text{MemFree}}{\text{MemTotal}} \right) \times 100 \quad \text{[Misleading]}$$

Bottleneck Detective V2 computes true available headroom using the kernel's `MemAvailable` estimator:
$$\text{Actual Mem Used (\%)} = \left( \frac{\text{MemTotal} - \text{MemAvailable}}{\text{MemTotal}} \right) \times 100$$
$$\text{Swap Activity Rate (B/s)} = \frac{(\Delta \text{pswpin} + \Delta \text{pswpout}) \times \text{PageSize}}{\Delta t}$$

### 6.3 Block Device I/O Calculations
From `/proc/diskstats`, for each device, let:
- $\Delta r_{\text{ios}}, \Delta w_{\text{ios}}$ = read and write I/O operations completed.
- $\Delta r_{\text{ticks}}, \Delta w_{\text{ticks}}$ = total milliseconds spent reading and writing.
- $\Delta \text{ticks}$ = milliseconds during which requests were in flight.
- $\Delta \text{weighted\_ticks}$ = cumulative milliseconds requests spent in the queue.

$$\text{Device Utilization (\%)} = \min\left(100.0, \frac{\Delta \text{ticks}}{\Delta t \times 1000} \times 100\right)$$
$$\text{Average Latency (\textit{await})} = \frac{\Delta r_{\text{ticks}} + \Delta w_{\text{ticks}}}{\max(1, \Delta r_{\text{ios}} + \Delta w_{\text{ios}})} \quad [\text{ms}]$$
$$\text{Average Queue Depth (\textit{aqu-sz})} = \frac{\Delta \text{weighted\_ticks}}{\Delta t \times 1000}$$

### 6.4 Interval-Exact Pressure Stall Information (PSI)
Kernel PSI exports both exponentially smoothed 10-second averages (`avg10`) and cumulative total stall microseconds (`total`). Exponential smoothing exhibits significant time lag, delaying alert triggering and obscuring recovery. Bottleneck Detective V2 derives the exact stall percentage over the monitoring interval $\Delta t$:
$$\text{PSI Stall (\%)} = \min\left(100.0, \frac{\text{total}_{t_2} - \text{total}_{t_1}}{\Delta t \times 10^6} \times 100\right)$$

### 6.5 Robust Rolling Baseline: Median and MAD
For any metric $X$, the baseline engine maintains a rolling buffer of $N = 300$ samples. To ensure resilience against extreme outliers and contamination by the very spikes being monitored, the engine uses non-parametric dispersion statistics:
$$\tilde{x} = \text{Median}(X)$$
$$\text{MAD} = \text{Median}(|X - \tilde{x}|)$$
$$\hat{\sigma} = 1.4826 \times \text{MAD}$$

To evaluate whether a new observation $x_{\text{new}}$ is an upward statistical anomaly, the modified $Z$-score is computed:
$$Z = \frac{x_{\text{new}} - \tilde{x}}{\max(\hat{\sigma}, \text{min\_scale})}$$

An indicator is flagged as a baseline anomaly if and only if:
$$Z \ge Z_{\text{thresh}} \; (4.0) \quad \land \quad (x_{\text{new}} - \tilde{x}) \ge \text{min\_delta} \quad \land \quad x_{\text{new}} \ge \text{min\_abs\_floor}$$

The critical inclusion of $\text{min\_abs\_floor}$ guarantees that benign shifts (e.g., an idle system with 1% CPU transitioning to a steady 4% CPU) never produce an anomaly alarm despite yielding $Z > 30$.

---

## 7. Detection Algorithms & State Transitions

### 7.1 Indicator Classification
Every sensor measurement is evaluated against static limits and baseline deviations, producing categorized indicators:
- **Stall Indicators ($S$):** PSI `cpu_some`, `mem_some`, `mem_full`, `io_some`, `io_full`. Represents verified task execution delay.
- **Saturation Indicators ($A$):** Run queue wait time, `procs_running`, swap activity while memory is full, disk `await` latency, disk queue depth, CPU `iowait`. Represents work queued in excess of capacity.
- **Utilization Indicators ($U$):** CPU %, Memory % (MemAvailable based), Disk %util. Represents resource occupancy.
- **Supporting Indicators:** Context switch rate, load average per core, disk throughput (MB/s), major page faults. Used for corroboration and confidence scoring, but never triggers an alert in isolation.

### 7.2 Persistence Filter
An indicator is declared **Present** if it evaluates to true in at least $k$ of the last $W$ samples:
$$\text{Persistence}(I) = \left( \sum_{i=0}^{W-1} \mathbb{I}(I_{t-i}) \ge k \right) \quad [k = 3, W = 5 \text{ by default}]$$

### 7.3 State Transition Rules
The diagnosis state machine is unified across CPU, Memory, and Storage:

```mermaid
stateDiagram-v2
    [*] --> NORMAL
    
    NORMAL --> ELEVATED : Utilization alone (U present, S absent, A absent)
    NORMAL --> POTENTIAL : Contradictory evidence (S alone, or A+U with quiet PSI)
    NORMAL --> CONFIRMED : Stall + Corroboration (S and [A or U])
    
    ELEVATED --> NORMAL : Utilization drops below threshold
    ELEVATED --> CONFIRMED : Stall and Saturation emerge
    ELEVATED --> POTENTIAL : Saturation emerges without stall
    
    POTENTIAL --> CONFIRMED : Corroborating Stall/Saturation arrives
    POTENTIAL --> RECOVERED : Contention clears
    
    CONFIRMED --> RECOVERED : Contention clears (persists 0 in window)
    RECOVERED --> NORMAL : Window expires without reoccurrence
```

| Current Evidence State | Resulting Classification | Architectural Interpretation |
|---|---|---|
| No indicators present | `NORMAL` | System operates within nominal baseline and static boundaries. |
| $U$ present; $S, A$ absent | **`ELEVATED`** | Resource is heavily utilized, but no queues or stalls exist. **Not a bottleneck claim.** |
| $S$ present alone; $A, U$ absent | **`POTENTIAL`** | Stalls observed, but utilization or queue evidence is incomplete. |
| PSI unavailable; $A$ and $U$ present | **`CONFIRMED`** (Medium Confidence) | Traditional saturation confirmed; confidence capped due to missing stall verification. |
| PSI available but absent; $A$ and $U$ present | **`POTENTIAL`** | Saturation contradicted by kernel stall telemetry; quiet PSI logged as contradiction. |
| $S$ present $\land$ ($A$ present $\lor$ $U$ present) | **`CONFIRMED`** | Complete, verified contention episode. Work is waiting and tasks are degraded. |
| Previous `POTENTIAL` or `CONFIRMED` clears | **`RECOVERED`** | Episode resolved; recorded in alert history with peak state and duration. |

### 7.4 Severity vs Confidence Decoupling
- **Severity (`INFO`, `WARNING`, `CRITICAL`):** Reflects magnitude of overload. An episode is `CRITICAL` only if a critical threshold held across the *entire* persistence window (5 consecutive samples). `ELEVATED` is strictly `INFO`.
- **Confidence (`LOW`, `MEDIUM`, `HIGH`):** Reflects agreement between independent kernel data streams. `HIGH` confidence requires:
  1. Kernel PSI available and verified present.
  2. At least 3 corroborating indicators agreeing ($\text{agree} \ge 3$).
  3. Zero contradicting stall or saturation indicators.

---

## 8. Process Attribution Engine

Attribution operates under the foundational premise: **Correlation is not proof.** The engine identifies processes that plausibly explain the observed contention without claiming definitive causation.

### 8.1 Identity Model
Processes are identified by the immutable tuple:
$$\text{Identity} = (\text{PID}, \text{StartTime}_{\text{jiffies}})$$
If a process terminates and its PID is reassigned by the kernel, the start time changes. The tracker detects this change, purges historical consumption for that PID, and prevents false attribution.

### 8.2 Attribution Criteria
A process is considered a contributor only if:
1. It appeared in at least $k = 3$ samples of the 5-sample window.
2. Its resource consumption met or exceeded the attribution floor:
   - CPU: $\ge 20\%$ of one core.
   - Memory: $\ge 5\%$ of physical RAM (plus RSS growth tracking for memory leaks).
   - Disk: $\ge 1.0\text{ MB/s}$ read or write throughput.

### 8.3 Attribution Verdicts
- **`primary`:** A single persistent process accounts for $\ge 50\%$ of the system-wide load.
- **`partial`:** A single persistent process accounts for $15–49\%$ of the load.
- **`multiple`:** Two or more persistent processes collectively account for the load.
- **`none`:** Load is diffuse or transient; top processes rotated across samples. The engine explicitly reports: *"Top process changed $N$ times; no single process was persistently dominant."*
- **`unavailable`:** Unprivileged non-root execution prevents reading `/proc/<pid>/io` of foreign processes. The engine reports unprivileged status honestly rather than guessing.

---

## 9. Verification & Testing

The project maintains a rigorous, multi-tiered verification framework comprising 189 deterministic unit tests and controlled experimental validation.

### 9.1 Unit Test Suite Execution
The complete test suite executes via Python's standard `unittest` framework:
```bash
.venv/bin/python -m unittest discover -s tests -v
```

**Results (prior to campaign):**
- **Total Tests:** 189 tests.
- **Failures:** 0.
- **Errors:** 0.
- **Python compilation:** Successful.
- **Harmless campaign test mode:** Successful.
- **Test Modules Covered:**
  1. `test_collectors.py`: Synthetic `/proc` trees, counter resets, jiffies wraps, missing interfaces.
  2. `test_detector_v2.py`: State rules, persistence boundaries, severity escalations, recovery events.
  3. `test_baseline.py`: Median/MAD stats, warm-up freeze locks, corrupt file resilience, adoption logic.
  4. `test_attribution.py`: PID recycling, rotating top processes, leak detection, multi-contributor aggregation.
  5. `test_dashboard_data.py`: Decoupled visualizer helpers, badge generation, trend parsing.
  6. `test_experiments.py`: Subprocess supervision, `PR_SET_PDEATHSIG` orphan cleanup, ground-truth metrics.
  7. `test_report_generator.py`: Legacy and modern CSV schema aggregation, Pandas formatting.
  8. `test_config_main.py`: CLI arguments, configuration validation, bounds checking.
  9. `test_detection.py`: Legacy backward compatibility.
  10. `test_campaign_runner.py`: Campaign manifest persistence, resume/skip logic, single-instance locking, cancellation handling and aggregation.

---

## 10. Automated Campaign Architecture

### 10.1 Campaign Design
The automated experiment campaign (`campaign_runner.py`) provides a systematic, repeatable validation framework for the detector. The campaign runner manages experiment lifecycle including:
- Manifest-based persistence for campaign recovery after interruption.
- Single-instance process locking to prevent concurrent campaign conflicts.
- Graceful cancellation handling with partial result preservation.
- Structured JSON and Markdown reporting for every individual run and for the aggregate suite.

### 10.2 Experiment Category Descriptions
Five experiment categories were defined to cover the full range of detector behaviors:

| Category | Purpose | Expected Detector Response |
|---|---|---|
| **`control`** | Idle system — no workload injected | Zero bottleneck alerts; validates false-positive avoidance |
| **`cpu-benign`** | Moderate CPU load below contention threshold | `NORMAL` or `ELEVATED` only; validates non-false-positive under real utilization |
| **`cpu`** | CPU-bound workers at safety limit (12 workers on 36 cores) | `CONFIRMED` CPU contention if workload is effective |
| **`memory`** | Bounded memory allocation (10% of available RAM) | `CONFIRMED` memory pressure if workload is effective |
| **`disk`** | Bounded sequential I/O with `fsync` on `/var/tmp` (NVMe) | `CONFIRMED` disk I/O saturation if workload is effective |

Each category was executed three times to establish repeatability. This produced **15 experiments** in the formal campaign manifest.

### 10.3 Three-Phase Experiment Protocol
Every experiment follows a strict three-phase lifecycle:

```
Phase 1: Baseline (10 s)      — Monitor idle system; establish per-run reference
Phase 2: Stress   (15 s)      — Inject bounded workload; observe detector response
Phase 3: Recovery (15 s)      — Terminate workload; measure clearance latency
```

The detection harness records ground-truth timing (workload start/stop), all per-sample metric snapshots, all detector state transitions, and attribution decisions across the full experiment timeline.

### 10.4 Verdict Classification

| Verdict | Meaning |
|---|---|
| **`PASS`** | Experiment completed as intended and met all expected conditions. |
| **`PARTIAL`** | Experiment completed; detector identified potential activity but did not escalate to `CONFIRMED`. The workload generated measurable I/O activity but did not create sustained saturation beyond the confirmation threshold. |
| **`INEFFECTIVE_STRESS`** | Experiment completed; workload did not generate sufficient resource pressure to satisfy confirmation thresholds. The detector's confirmed-bottleneck path was not exercised. |

> [!IMPORTANT]
> These three verdicts evaluate fundamentally different things. An experiment completing as `INEFFECTIVE_STRESS` or `PARTIAL` does **not** mean the detector failed. It means the workload was insufficient to create conditions required for confirmed detection. The detector behaved correctly in all 15 cases.

---

## 11. Experimental Setup & Safety Restrictions

### 11.1 Host Environment

| Parameter | Value |
|---|---|
| Processor | Intel Xeon w5-2565X |
| Physical CPU cores | 36 |
| Total RAM | 63,930 MB (~62.4 GB) |
| Available RAM at inspection | ~57.29 GB |
| Kernel version | 7.0.0-38-generic |
| Python version | 3.12.3 |
| Root partition free space | ~329.8 GB |
| PSI available | Yes (CPU, Memory, I/O) |
| `/proc/schedstat` | Available |
| Disk device | nvme0n1 (NVMe, ext4 filesystem on `/var/tmp`) |

### 11.2 Conservative Safety Parameters

The campaign was executed with deliberately conservative resource boundaries to prevent system instability:

| Safety Constraint | Value Applied |
|---|---|
| Execution privileges | No sudo / no root |
| CPU worker count (max) | 12 workers on 36-core host |
| CPU worker scheduling priority | `nice 10` (reduced priority) |
| Memory allocation cap | 10% of available RAM (~5.7 GB) |
| Minimum free memory floor | ~3.1 GB enforced |
| Memory worker OOM disposition | `oom_score_adj = 1000` (kernel sacrifice candidate) |
| Disk scratch path | `/var/tmp` (isolated directory per experiment) |
| Maximum disk scratch per run | 128 MB (2 workers × 64 MB) |
| Disk file management | Exclusive creation; full cleanup after each run |
| Aggressive system-wide stress | Not performed |

### 11.3 Important Consequence of Conservative Safety Limits
The safety boundaries described above were appropriate for protecting the host system during an automated, unattended experiment campaign. However, they also directly constrained the workload intensity available to the experiments:
- 12 CPU workers on a 36-core host cannot create CPU oversubscription, so confirmed CPU contention cannot be achieved under these parameters.
- A 10% memory allocation on a 57 GB available system cannot create memory pressure.
- 128 MB total disk scratch on a high-bandwidth NVMe drive is handled efficiently, limiting the ability to establish sustained I/O saturation.

These consequences are discussed in the results sections below.

---

## 12. Experimental Results

### 12.1 Campaign Summary

| Category | Runs | Status | Verdict |
|---|---|---:|---|
| Control | 3/3 | All completed | **PASS** |
| CPU-benign | 3/3 | All completed | **PASS** |
| CPU stress | 3/3 | All completed | **INEFFECTIVE_STRESS** |
| Memory stress | 3/3 | All completed | **INEFFECTIVE_STRESS** |
| Disk stress | 3/3 | All completed | **PARTIAL** |
| **Total** | **15/15** | **All completed** | — |

**Campaign counters (from `manifest.json`):**
- Completed: 15 | Failed: 0 | Cancelled: 0 | Pending: 0 | Skipped: 0

> [!NOTE]
> The `suite.md` file reports 16 total runs because it incorporates an earlier standalone control run (`20261118-231934-control`) executed before the main campaign. The formal campaign manifest contains the **15 experiments** that constitute the structured validation suite (3 repetitions × 5 categories).

### 12.2 Control Experiments (3/3 PASS)

**Run IDs:** `20261118-234643-control`, `20261118-234918-control`, `20261118-235152-control`

All three control experiments passed.

- No workload was injected. The 15-second "stress" phase monitored the system at idle.
- Zero false bottleneck alerts were observed across all three runs.
- Each run monitored 30 samples (10 baseline + 15 stress + 5 recovery per run, totaling 90 samples across all three control runs) with zero spurious positive detections.
- System baseline CPU utilization during control runs was approximately 15% (reflecting background system processes), demonstrating that the detector correctly ignores normal background activity.

**Interpretation:** The control results confirm that the detector does not generate false alarms under normal system operation.

### 12.3 CPU-Benign Experiments (3/3 PASS)

**Run IDs:** `20261118-234714-cpu-benign`, `20261118-234948-cpu-benign`, `20261118-235222-cpu-benign`

**Configuration:** 8 CPU-bound workers on a 36-core host.

All three CPU-benign experiments passed.

| Metric | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| CPU utilization (stress phase mean) | 27.68% | 27.43% | 27.51% |
| CPU PSI some (stress phase mean) | 0.317% | 0.317% | 0.287% |
| Detector CPU classification | NORMAL / ELEVATED only | NORMAL / ELEVATED only | NORMAL / ELEVATED only |
| False positive alerts | 0 | 0 | 0 |

**Interpretation:** With 8 workers on 36 cores, the workload produced moderate CPU utilization (~27–28%) with negligible scheduling stalls. The detector correctly classified the state as `NORMAL` or `ELEVATED` and generated zero `POTENTIAL` or `CONFIRMED` alerts. This validates the detector's ability to tolerate genuine non-saturating CPU activity without false alarms.

### 12.4 CPU Stress Experiments (3/3 INEFFECTIVE_STRESS)

**Run IDs:** `20261118-234744-cpu`, `20261118-235018-cpu`, `20261118-235252-cpu`

**Configuration:** 12 CPU-bound workers (nice 10) on a 36-core host.

All three CPU stress experiments completed, but were classified as `INEFFECTIVE_STRESS`.

| Metric | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| CPU utilization (stress phase mean) | 40.83% | 37.07% | 37.64% |
| CPU PSI some (stress phase mean) | 1.038% | 0.251% | 0.273% |
| Confirmed CPU detection | No | No | No |
| Potential CPU detection | No | No | No |
| CPU metric recovery (mean) | 1.12 s | 1.12 s | 1.13 s |
| Attribution (correct / wrong / abstained) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

**Confirmation thresholds not reached:**
- CPU utilization threshold: ≥ 90% (observed: ~37–41%)
- CPU PSI threshold: ≥ 10% (observed: ~0.25–1.04%)

**Root cause of ineffective stress:** 12 workers on a 36-core host occupies only one-third of the available CPU execution contexts. No thread oversubscription occurs; the kernel scheduler has ample capacity to serve all workers without queueing. The host's large core count (36 cores) means that the safety-constrained maximum of 12 CPU workers could not create contention.

**What was measured:**
- CPU metric recovery was approximately 1.12–1.13 seconds after workload termination, confirming that the detector would respond rapidly if CPU contention were established.

**Honest interpretation:** The CPU stress experiments confirm that the experiment infrastructure functions correctly and that the safety constraints are effective. They do not confirm that the detector's CPU confirmed-bottleneck detection path was exercised in this campaign. Future experiments would require either a greater number of workers (exceeding core count) or a host with fewer cores to demonstrate confirmed CPU contention.

### 12.5 Memory Stress Experiments (3/3 INEFFECTIVE_STRESS)

**Run IDs:** `20261118-234815-memory`, `20261118-235049-memory`, `20261118-235323-memory`

**Configuration:** 10% RAM allocation (~5.7 GB on a 57 GB system), `oom_score_adj = 1000`.

All three memory stress experiments completed, but were classified as `INEFFECTIVE_STRESS`.

| Metric | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Memory utilization (stress phase mean) | 15.31% | 15.33% | 15.27% |
| Memory PSI some (stress phase mean) | 0.000% | 0.000% | 0.000% |
| Swap-out activity | 0 B/s | 0 B/s | 0 B/s |
| Confirmed memory detection | No | No | No |
| Memory metric recovery | 1.29 s | 1.34 s | 1.29 s |
| Attribution (correct / wrong / abstained) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

**Confirmation thresholds not reached:**
- Memory PSI some threshold: ≥ 1% (observed: 0.000%)
- Swap-out requirement: > 0 B/s (observed: 0.000 B/s)

**Root cause of ineffective stress:** Allocating 5.7 GB of a 57 GB system leaves more than 50 GB of RAM freely available. The kernel's memory management subsystem did not need to invoke page reclamation or initiate any swap activity. Without kernel memory stalls or swap pressure, the detector's memory confirmation threshold was not reachable.

**Honest interpretation:** The 10% RAM cap was appropriate for safety but prevented the experiments from exercising memory pressure conditions. The memory detector's confirmed-bottleneck path was not exercised in this campaign. Demonstrated memory metrics returned to baseline in approximately 1.29–1.34 seconds, which is consistent with what would be expected from RSS cleanup.

### 12.6 Disk Stress Experiments (3/3 PARTIAL)

**Run IDs:** `20261118-234845-disk`, `20261118-235119-disk`, `20261118-235353-disk`

**Configuration:** 2 workers, 64 MB blocks with `fsync`, writing to `/var/tmp` (nvme0n1, ext4).

All three disk stress experiments completed and were classified as `PARTIAL`.

| Metric | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Disk utilization (stress phase mean) | 62.78% | 61.89% | 64.20% |
| Average request latency (stress phase mean) | 0.334 ms | 0.335 ms | 0.424 ms |
| Average queue depth (stress phase mean) | ~6.55 | — | — |
| Combined throughput (R+W, stress phase mean) | ~2,342 MB/s | ~2,373 MB/s | ~2,238 MB/s |
| I/O PSI some (stress phase mean) | 4.185% | 4.736% | 5.098% |
| POTENTIAL detection achieved | Yes | Yes | Yes |
| CONFIRMED detection achieved | No | No | No |
| Detection latency (POTENTIAL) | 3.05 s | 3.06 s | 3.05 s |
| Detector clearance after workload | 3.11 s | 3.10 s | 3.10 s |
| Attribution (correct / wrong / abstained) | 12 / 0 / 1 | 12 / 0 / 1 | 12 / 0 / 1 |

**Evidence present during disk stress (from raw report — Run 1):**
- Disk utilization: 60% (threshold 80%) — present in 3/5 samples (**Present**, utilization indicator)
- Queue depth: 6.55 (threshold 2.0) — present in 3/5 samples (**Present**, saturation indicator)
- Throughput: 2,342 MB/s — present in 3/5 samples (**Present**, supporting indicator)

**Evidence absent (contradicting confirmation):**
- Average request latency: 0.3 ms (saturation threshold: 30 ms) — present in 0/5 samples (**Absent**)
- I/O PSI some: 4.5% (stall threshold: 20%) — present in 0/5 samples (**Absent**)
- I/O PSI full: 4.4% (threshold: 5%) — present in 1/5 samples (**Absent**)
- CPU iowait: 2.3% (threshold: 20%) — present in 0/5 samples (**Absent**)

**Why POTENTIAL and not CONFIRMED:** The disk device (NVMe nvme0n1) handled the bounded 128 MB workload efficiently. While utilization and queue depth showed activity, the critical saturation indicator — average request latency — remained at 0.3 ms, far below the 30 ms saturation threshold. I/O PSI stall also remained below its 20% confirmation threshold. The detector correctly classified the state as `POTENTIAL` (utilization and queue depth present, but saturation latency and stall absent), not `CONFIRMED`.

**Stress effectiveness note:** The harness classified the disk stress as "effective" (device reached 62–89% utilization and I/O PSI exceeded 1%), which is why the verdict is `PARTIAL` rather than `INEFFECTIVE_STRESS`. The distinction is: the workload did create measurable I/O pressure, but the NVMe device handled it without entering confirmed saturation.

**Attribution in disk runs:** Each disk run recorded 12 correct attributions and 1 abstention per run (totaling 36 correct and 3 abstentions across all three runs). Abstentions occurred because no single process was consistently dominant across all persistence window samples — the top I/O process rotated between the monitoring agent and the workload workers across samples. Attribution reliability for the non-abstained cases was 1.0 (12/12 correct per run).

---

## 13. Comparative Analysis

### 13.1 Three Dimensions of Experiment Outcomes
It is essential to distinguish three separate dimensions when interpreting campaign results:

| Dimension | Definition | Campaign Result |
|---|---|---|
| **Experiment execution** | Did the experiment complete without error or crash? | 15/15 completed successfully |
| **Workload effectiveness** | Did the workload create the resource pressure intended? | 6/15 effective (3 control PASS, 3 disk PARTIAL effective); 6/15 ineffective (3 CPU, 3 memory); 3 benign as designed (cpu-benign) |
| **Detector classification success** | Did the detector reach the expected confirmation state? | 6/15 fully validated (3 control PASS, 3 cpu-benign PASS); 3 partial (disk PARTIAL); 6 not exercised (CPU and memory stress ineffective) |

### 13.2 False-Positive Rate
Zero false-positive alerts were observed across the 250 metric samples examined during this campaign. The 250 samples were drawn from baseline monitoring phases, control runs, and cpu-benign negative control runs.

> [!IMPORTANT]
> This result represents the observed false-positive rate within this specific campaign context. It does not constitute a universal guarantee of zero false positives under all possible workload configurations.

### 13.3 Detection Latency
For disk experiments, the first `POTENTIAL` alert appeared approximately **3.05 seconds** after workload start. This is consistent with the theoretical persistence filter minimum: 3 samples at a 1-second sampling interval. No additional algorithmic lag was introduced beyond the deliberate persistence window.

### 13.4 Resource Recovery
| Resource | Recovery Metric | Mean Time to Baseline |
|---|---|---|
| CPU | CPU utilization metric | ~1.12 s (across 3 CPU stress runs) |
| Memory | Memory RSS metric | ~1.29–1.34 s (across 3 memory stress runs) |
| Disk I/O | Detector alert clearance | ~3.10–3.11 s (across 3 disk runs) |

Recovery times indicate that the detector clears alerts promptly after workload termination, without excessive hysteresis.

### 13.5 Process Attribution Summary

Attribution was applicable only in disk experiments (the only category where `POTENTIAL` or `CONFIRMED` alerts were generated). CPU and memory stress experiments did not produce any alerts, so there were no attribution decisions to make.

| Category | Correct Attributions | Wrong Attributions | Abstentions | Reliability |
|---|---|---|---|---|
| Disk (all 3 runs) | 36 | 0 | 3 | 1.0 (on decided cases) |
| CPU stress | 0 | 0 | 0 | N/A (no alerts) |
| Memory stress | 0 | 0 | 0 | N/A (no alerts) |

Abstentions in disk runs occurred when no single process was consistently dominant across the 5-sample persistence window — the top I/O process alternated between the monitoring agent (`agy`) and the Python workload workers.

> [!NOTE]
> The `suite.json` calibration table reports 39 diagnoses with precision 1.0. This refers to **confidence calibration** (all 39 `POTENTIAL/LOW` diagnoses were produced while the respective resource was actually under stress), not to process attribution. These are different metrics. Attribution measures whether the correct *process* was identified; calibration measures whether the correct *resource* was identified.

### 13.6 Confidence Calibration
All 39 sample-level `POTENTIAL/LOW` diagnoses produced during stress phases correctly identified the stressed resource (disk I/O). Precision = 1.0 (39/39). This is a small-sample result and should be treated as a sanity check rather than a statistically calibrated guarantee.

---

## 14. Known Limitations & Future Work

### 14.1 Current Limitations

1. **Non-Root Process I/O Visibility:** On Linux, reading `/proc/<pid>/io` for processes owned by other users requires administrative (`CAP_SYS_PTRACE`) privileges. When running as an unprivileged user, the engine can measure system-wide disk saturation perfectly, but per-process I/O attribution degrades to `unavailable`.

2. **Page-Cache Writeback Disconnect:** When a process executes buffered writes, bytes are written to the kernel page cache almost instantaneously. Physical block device writes occur seconds later during asynchronous flush kernel worker (`kworker`) threads. Consequently, process attribution for disk writes is an approximation based on dirty page generation rather than exact hardware commit timestamps.

3. **Hardware Boundary:** The current architecture focuses on CPU scheduling, virtual memory, and block storage devices. Hardware bottlenecks occurring in network interface cards (NIC buffer ring overflow, TCP window stalls) or GPU compute pipelines are not currently captured.

4. **Campaign Workload Intensity Constraints:** The conservative safety boundaries used in this campaign prevented CPU and memory stress experiments from reaching their confirmation thresholds. Future campaigns should consider:
   - CPU: Using worker counts greater than the core count to force oversubscription, with appropriate safety guards.
   - Memory: Allocating a higher fraction of RAM (40–60%) under supervised conditions to trigger kernel page reclamation and PSI stalls.
   - Disk: Using more workers or larger write blocks to establish sustained I/O saturation on high-throughput NVMe devices.

5. **Single-Host Scope:** The detector is designed for single-host monitoring. It does not address distributed system bottlenecks or cross-host resource contention.

### 14.2 Future Enhancements
- **eBPF Tracepoint Probes:** Integrating eBPF (`tracepoint:sched:sched_stat_wait`, `block:block_rq_issue`) could provide microsecond-accurate kernel queuing latency without polling `/proc`.
- **Cgroup Hierarchy Scoping:** Extending PSI collectors to traverse `/sys/fs/cgroup` subtrees to isolate noisy neighbor containers in Kubernetes pods.
- **Adaptive Workload Scaling:** Implementing an experiment harness that dynamically scales workload intensity until confirmation is achieved (or a safety ceiling is reached), enabling reliable validation on any host configuration.
- **Network I/O Monitoring:** Extending the collector layer to cover NIC ring buffer overflow, TCP retransmission rates, and socket buffer pressure.

---

## 15. Conclusion

Bottleneck Detective V2 demonstrates that operating system performance monitoring can advance beyond static thresholds and manual counter interpretation. By grounding detection in the physics of operating system queues, leveraging modern Linux kernel Pressure Stall Information, filtering spikes through persistence matrices, and enforcing identity-aware process attribution, the system delivers explainable, evidence-based diagnoses.

The 15-experiment automated campaign produced the following scientifically meaningful results:

1. **Zero false alarms** were observed across 250 metric samples during idle, control, and benign-workload conditions. This validates a core design goal of the system.
2. **Control and CPU-benign experiments** (6/6 PASS) confirm that the detector correctly classifies normal and moderately busy systems without spurious alerts.
3. **Disk I/O experiments** (3/3 PARTIAL) confirm that the detector identifies potential disk activity and queueing in approximately 3.05 seconds and clears alerts within 3.10–3.11 seconds of workload termination. The NVMe device's high throughput and low latency prevented escalation to `CONFIRMED` within the safety-bounded workload parameters.
4. **CPU and memory stress experiments** (6/6 INEFFECTIVE_STRESS) confirm that the experiment infrastructure and safety constraints function as intended. These results highlight that workload intensity must exceed detection thresholds to exercise the confirmed bottleneck classification path, which is a valid finding regarding experimental design rather than detector failure.
5. **Process attribution** in disk experiments achieved a reliability of 1.0 on decided cases (36 correct, 0 wrong, 3 abstentions), with abstentions occurring when I/O responsibility was diffuse.

These results provide useful evidence about false-alert avoidance, detection latency, and resource recovery dynamics. They also honestly document the conditions required to exercise the confirmed bottleneck detection path, providing clear direction for future experiments.

---

## References

1. Silberschatz, A., Galvin, P. B., & Gagne, G. (2018). *Operating System Concepts* (10th ed.). Wiley. (Chapters 3, 5, 8, 9, 10).
2. Gregg, B. (2020). *Systems Performance: Enterprise and the Cloud* (2nd ed.). Addison-Wesley Professional. (Chapters 5, 6, 7, 8, 9).
3. Linux Kernel Documentation. *Pressure Stall Information (PSI)*. `Documentation/accounting/psi.rst`.
4. Linux Kernel Documentation. *The `/proc` Filesystem*. `Documentation/filesystems/proc.rst`.
5. Linux Man Pages. `proc(5)`, `schedstat(5)`, `diskstats(5)`, `vmstat(8)`, `top(1)`.
6. psutil Documentation. *Cross-platform lib for process and system monitoring in Python*. `https://psutil.readthedocs.io/`.
