# Linux Bottleneck Detective V2: Presentation Slide Deck (10 Slides)

**Course:** Operating Systems Laboratory / Final Project Presentation  
**Target Duration:** 10–12 Minutes (+ 3-Minute Live Demonstration)  
**Deliverable File:** `reports/PRESENTATION_SLIDES.md`

---

## Slide 1: Title & Overview

### Visual Layout
- **Title:** Linux Bottleneck Detective V2
- **Subtitle:** Autonomous Linux Performance Bottleneck Detection & Explainable Diagnosis
- **Key Concepts:** USE Method • Pressure Stall Information (PSI) • Multi-Metric Persistence • Identity-Aware Attribution
- **Presenter Information:** Operating Systems Lab Project, November 2026

### Slide Content
- **The Core Premise:** Moving beyond raw resource counters (`top`, `vmstat`, `iostat`) to determine whether an operating system resource is **genuinely contended**, explain **why**, and identify the **responsible processes**.
- **Novelty:** Evidence-based state classification (`NORMAL`, `ELEVATED`, `POTENTIAL`, `CONFIRMED`, `RECOVERED`), decoupled severity vs. confidence, and dynamic MAD statistical baselines.

### Speaker Notes (1 Minute)
> "Good morning, professors and evaluators. Today, I am presenting Bottleneck Detective V2. Traditional tools like `top` or `vmstat` give us numbers, but they leave the burden of interpretation to humans. A system with 95% CPU utilization might be doing optimal work or starving tasks in run queues. Bottleneck Detective V2 is designed to understand the difference between high utilization and genuine contention, explain its reasoning transparently, and validate itself with controlled experiments."

---

## Slide 2: The Problem: The Diagnostic Gap

### Visual Layout
- **Two Contrasting Scenarios:**
  - *Scenario A:* 36 cores running 36 computational threads at 100% CPU. Run queue wait = 0 ms, PSI = 0%. **Verdict:** Highly productive batch system.
  - *Scenario B:* 36 cores with 150 threads contending for execution. Run queue wait > 25 ms, PSI = 45%. **Verdict:** Saturated, starving system.
- **The Core Diagnostic Failure of Traditional Alarms:**
  - Static thresholds flag *both* scenarios as critical alarms.
  - Naive memory alerts treat the page cache as "leaked memory."
  - Single-sample process sorting blames whichever process happened to execute at that exact instant.

### Speaker Notes (1 Minute)
> "Why do traditional tools fail? Consider these two systems. Both report 100% CPU utilization. But in Scenario A, every core is executing productive work without any queuing. In Scenario B, dozens of tasks are waiting for execution time. Traditional threshold alarms cannot tell them apart. Similarly, virtual memory managers naturally use spare RAM for caching, so memory always looks 'full'. Our goal in V2 is to treat waiting—queues and stalls—as the proof of a bottleneck, and utilization merely as context."

---

## Slide 3: Project Objectives

### Visual Layout
- **7 Core Objectives:**
  1. **Sustained Contention Detection:** Filter transient spikes with multi-sample persistence rules ($k \ge 3$ of $W = 5$ samples).
  2. **Multi-State Evidence Modeling:** Separate `ELEVATED` (busy) from `POTENTIAL` (partial) and `CONFIRMED` (verified degradation).
  3. **Decoupled Dimensions:** Separate Severity (overload magnitude) from Confidence (sensor consensus).
  4. **Kernel PSI Integration:** Leverage Linux Pressure Stall Information directly from cumulative microsecond counters.
  5. **Dynamic Rolling Baseline:** Median + MAD deviation detection with absolute safety floors.
  6. **Identity-Aware Attribution:** Track `(PID, start_time)` to eliminate PID-recycling attribution errors.
  7. **Self-Validating Harness:** Built-in controlled workloads to experimentally evaluate detection accuracy.

### Speaker Notes (1 Minute)
> "To solve these issues, we set seven clear objectives. Notice particularly items 2, 3, and 6. We do not just trigger an alarm; we classify the state into ELEVATED, POTENTIAL, or CONFIRMED. We separate how severe an overload is from how confident the evidence is. And we never attribute load using PID alone, because PIDs are recycled by the Linux kernel."

---

## Slide 4: System Architecture

### Visual Layout
```
[ Linux Kernel Interfaces ]
 /proc/stat  |  /proc/schedstat  |  /proc/meminfo  |  /proc/diskstats  |  /proc/pressure/*
       │               │                 │                 │                    │
       ▼               ▼                 ▼                 ▼                    ▼
[ collectors/ Layer (cpu_monitor, memory_monitor, disk_monitor, process_monitor, psi) ]
                                       │
                                       ▼
                     [ engine.py : MonitorEngine ]
                      (Single coordination engine)
                                       │
                                       ▼
[ analyzer/ Layer: Baseline (MAD) + Attribution (PID+time) + BottleneckDetector V2 ]
                                       │
       ┌───────────────────────────────┴───────────────────────────────┐
       ▼                                                               ▼
[ Persistence : data/ ]                                     [ Presentation & Reports ]
 system_logs.csv                                             Streamlit Dashboard (9 Tabs)
 alerts.csv & latest.json                                    Executive Markdown Reports
```

### Speaker Notes (1 Minute)
> "Here is our system architecture. Collectors read directly from `/proc` and `/sys`. Notice an important design principle: the `analyzer/` package is completely pure and deterministic. It performs no I/O and has no kernel dependencies, making it 100% testable with synthetic data. Furthermore, the exact same MonitorEngine coordinates both normal background monitoring and our controlled experiment harness—there are no fake or specialized paths for tests."

---

## Slide 5: Operating System Concepts Mapped to Modules

### Visual Layout
| OS Concept | Theoretical Mechanism | Module Implementation |
|---|---|---|
| **Process Identity** | PID reuse in task table | `ProcessTracker` uses `(pid, start_time)` tuple |
| **Scheduler Latency** | Threads waiting in run queues | `CpuMonitor` computes wait deltas from `/proc/schedstat` |
| **Context Switching** | Register saving & pipeline flushes | `CpuMonitor` tracks `ctx_switches_s` per core |
| **Virtual Memory Cache** | Page cache reclamation vs exhaustion | `MemoryMonitor` uses `MemAvailable`, not `MemFree` |
| **Thrashing** | Page-in/page-out disk faults | `MemoryMonitor` calculates swap rates + major page faults |
| **Block Queue Depth** | Storage driver request queues | `DiskMonitor` calculates `await` latency & queue depth |
| **Kernel Task Stalls** | Work stalled on hardware | `PsiReader` calculates interval-exact stall % from `total` |

### Speaker Notes (1.5 Minutes)
> "This slide maps core Operating Systems principles directly to our codebase. For process management, we track process start times to ensure that when a PID is recycled, it does not inherit history. For scheduling, we read `/proc/schedstat` to measure runnable wait time directly. For memory, we respect page cache dynamics by using `MemAvailable`. For storage, we compute device latency and queue depth. And for stalls, we read Linux Kernel Pressure Stall Information."

---

## Slide 6: Mathematical Formulations & Detection Rules

### Visual Layout
- **Interval-Exact PSI Calculation:**
  $$\text{PSI Stall (\%)} = \min\left(100.0, \frac{\text{total}_{t_2} - \text{total}_{t_1}}{\Delta t \times 10^6} \times 100\right)$$
- **Median Absolute Deviation (MAD) Baseline:**
  $$\tilde{x} = \text{Median}(X), \quad \text{MAD} = \text{Median}(|X - \tilde{x}|), \quad Z = \frac{x - \tilde{x}}{\max(1.4826 \times \text{MAD}, \text{min\_scale})}$$
- **State Transition Matrix:**
  - $\text{Stall } (S) \land (\text{Saturation } (A) \lor \text{Utilization } (U)) \implies \mathbf{CONFIRMED}$
  - $\text{Stall } (S) \text{ only} \implies \mathbf{POTENTIAL}$
  - $\text{Utilization } (U) \text{ only} \implies \mathbf{ELEVATED}$
  - Contention cleared $\implies \mathbf{RECOVERED}$

### Speaker Notes (1.5 Minutes)
> "Here are the core mathematical formulas. Instead of relying on kernel averages like `avg10`—which lag by several seconds—we calculate the exact interval stall percentage from cumulative microsecond deltas. For our dynamic baseline, we use Median and MAD rather than mean and standard deviation, because MAD is immune to extreme outlier contamination. And our state engine requires both stall evidence and corroboration before confirming a bottleneck."

---

## Slide 7: Persistence, Severity & Process Attribution

### Visual Layout
- **Persistence Matrix:**
  - Window $W = 5$ samples, required $k = 3$ samples.
  - Spikes lasting 1 or 2 samples are suppressed, preventing false alarms.
- **Orthogonal Ratings:**
  - **Severity:** Overload magnitude (`INFO`, `WARNING`, `CRITICAL` — critical requires holding across the entire window).
  - **Confidence:** Consensus across independent kernel streams (`LOW`, `MEDIUM`, `HIGH` — high requires PSI + $\ge 3$ agreeing sensors).
- **Attribution Logic:**
  - Evaluates sustained load across the persistence window.
  - Verdicts: `primary` ($\ge 50\%$), `partial` ($15-49\%$), `multiple` ($\ge 2$), or `none` (diffuse/rotating load).

### Speaker Notes (1 Minute)
> "To prevent alarm fatigue, an indicator must persist in at least 3 of the last 5 samples. Notice how severity and confidence are orthogonal: a critical alert can have low confidence if only one sensor saw it, while a moderate alert can have high confidence if CPU, PSI, and run queues all agree. For attribution, we never blame the top process from a single sample; a process must persistently account for load across the window to be named as a primary contributor."

---

## Slide 8: Experiment Campaign Architecture

### Visual Layout
- **15-Experiment Automated Campaign:**
  - 5 categories × 3 repetitions each
  - 3-phase lifecycle per experiment: Baseline (10 s) → Stress (15 s) → Recovery (15 s)
  - Campaign runner with manifest persistence, recovery, and locking
- **Conservative Safety Constraints Applied:**
  - No sudo / no root
  - CPU: 12 workers max (on 36-core host, reduced priority nice 10)
  - Memory: 10% of RAM cap (~5.7 GB), `oom_score_adj=1000`
  - Disk: 128 MB total scratch, isolated `/var/tmp` directories, full cleanup

### Speaker Notes (1.5 Minutes)
> "The campaign uses a systematic, repeatable design: five experiment types, each repeated three times, for a total of 15 experiments. Each experiment follows three phases: a baseline phase to measure normal system state, a stress phase to inject bounded workload, and a recovery phase to observe how quickly the system returns to normal. The campaign runner persists state to a manifest so it can resume after interruption. All experiments used conservative safety limits—no root access, bounded CPU workers, capped memory allocation, and isolated disk scratch directories with full cleanup."

---

## Slide 9: Experiment Results — 15-Experiment Campaign

### Visual Layout

**Campaign Summary (All 15 experiments completed; 0 failed)**

| Category | Result | Key Finding |
|---|---|---|
| **Control** | 3/3 **PASS** | Zero false alerts; detector correctly silent |
| **CPU-benign** | 3/3 **PASS** | ~27–28% CPU usage; zero false positives |
| **CPU stress** | 3/3 **INEFFECTIVE_STRESS** | 12 workers on 36 cores: insufficient for oversubscription |
| **Memory stress** | 3/3 **INEFFECTIVE_STRESS** | 5.7 GB on 57 GB system: no kernel memory stalls |
| **Disk stress** | 3/3 **PARTIAL** | POTENTIAL detected in ~3.05 s; NVMe handled load efficiently |

**Key Observations:**
- **Zero false-positive alerts** across 250 metric samples (baseline + control + cpu-benign phases)
- **Disk POTENTIAL detection latency:** ~3.05 s (matches theoretical 3-sample persistence minimum)
- **Alert clearance after workload stop:** ~3.10 s
- **CPU metric recovery:** ~1.12 s | **Memory metric recovery:** ~1.29–1.34 s
- **Process attribution (disk runs):** 36 correct / 0 wrong / 3 abstentions
- **Unit tests (pre-campaign):** 189 passed / 0 failed

### Speaker Notes (2 Minutes)
> "Here are our empirical results from the 15-experiment campaign. Let me be precise about what these results show.
>
> Control and CPU-benign tests all passed — the detector generated zero false alarms while the system was idle or under moderate load. This validates a core design goal.
>
> CPU and memory stress tests completed successfully, but were classified as INEFFECTIVE_STRESS. This does not mean the detector failed. It means the safety-constrained workloads — 12 workers on 36 cores, and 5.7 GB on a 57 GB system — did not create enough pressure to reach the confirmation thresholds. The detector behaved correctly; we simply could not stress it hard enough within our safety budget.
>
> Disk experiments achieved PARTIAL detection: the detector identified potential disk I/O activity in exactly 3.05 seconds — matching our theoretical minimum — but the NVMe device's high throughput and 0.3 ms latency meant the saturation threshold of 30 ms was never reached. So POTENTIAL, not CONFIRMED.
>
> Crucially, zero false-positive alerts were observed across 250 metric samples. And process attribution for disk runs achieved 36 correct decisions with zero wrong."

---

## Slide 10: Limitations, Future Work & Conclusion

### Visual Layout
- **Experiment Design Limitations:**
  - CPU confirmation requires worker count > core count (12 < 36 in our campaign)
  - Memory confirmation requires higher RAM allocation (10% was insufficient)
  - Disk CONFIRMED requires higher I/O intensity or slower storage
- **System Design Limitations:**
  - Non-root execution restricts foreign process I/O tracking (`/proc/<pid>/io`)
  - Asynchronous page-cache writeback creates temporal delay between dirtying pages and disk commit
  - Scope limited to host CPU, memory, and block storage (no network or GPU)
- **Future Directions:**
  - eBPF kernel tracepoints (`sched_stat_wait`) for microsecond queuing latency
  - Cgroup v2 hierarchical tree traversal for container / Kubernetes pod isolation
  - Adaptive workload scaling to reliably exercise all detection paths
- **Summary:**
  - Bottleneck Detective V2 delivers evidence-based, explainable performance monitoring with zero false alarms under conservative conditions; validation of the confirmed CPU and memory detection paths requires higher workload intensity in future experiments.

### Speaker Notes (1 Minute)
> "To conclude, Bottleneck Detective V2 successfully demonstrates zero-false-alarm detection, sub-4-second disk I/O detection latency, and correct process attribution. We are honest about our limitations: the safety-constrained campaign could not fully exercise the confirmed CPU and memory detection paths. Future work will address this with adaptive workload scaling. We also acknowledge that non-root execution limits per-process I/O visibility, and that our scope does not yet cover network or GPU resources. Thank you, and I look forward to your questions."

---

## 3-Minute Live Demonstration Script

**Prerequisites:** Terminal with the project active, Streamlit dashboard running.

### Step 1 — Healthy Baseline (30 seconds)
```bash
python main.py &
streamlit run dashboard/app.py
```
> *"The dashboard shows all three resources — CPU, Memory, and Disk — in NORMAL state. No alerts. This is the baseline."*

### Step 2 — Negative Control: cpu-benign (45 seconds)
```bash
.venv/bin/python experiment.py run cpu-benign
```
> *"I am now running 8 CPU-bound workers. CPU utilization is rising to roughly 27–28%. Notice the dashboard: it shows ELEVATED, not CONFIRMED. No bottleneck alert fires. This is crucial — high utilization without queue stalls is not a bottleneck."*

### Step 3 — Disk Stress: Potential Detection (60 seconds)
```bash
.venv/bin/python experiment.py run disk
```
> *"Now I am running 2 disk workers with fsync writes. Watch the Disk I/O panel. After exactly 3 seconds — the persistence filter minimum — the detector raises a POTENTIAL disk I/O alert. It identifies elevated utilization and queue depth. Notice that it also reports the contradicting evidence: average latency is only 0.3 ms, well below the 30 ms saturation threshold. That is why it stays POTENTIAL and does not escalate to CONFIRMED."*

### Step 4 — Recovery (30 seconds)
> *"The workload has terminated. The alert clears within approximately 3.1 seconds. The system returns to NORMAL. The report artifact records the full timeline, latency measurements, and attribution decisions."*

### Step 5 — Report Artifact (15 seconds)
```bash
cat data/campaign_runs/<latest-disk-run>/report.json | python3 -m json.tool | head -50
```
> *"Here is the machine-readable report from the experiment. It records verdict, detection latency, phase means, attribution, and recovery times — all measured from actual kernel interfaces."*
