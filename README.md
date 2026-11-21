# Bottleneck Detective V2: Linux Performance Bottleneck Detection and Explainable Diagnosis

An Operating Systems project that goes beyond `top`/`vmstat`: it decides whether a resource is
**genuinely contended** (not merely busy), explains *why*, names the processes that plausibly cause it
(only when the evidence supports it), and **validates itself** with controlled experiments.

Contents: [1 Objective](#1-objective) | [2 Problem](#2-problem-statement) | [3 How it works](#3-how-it-works) |
[4 Architecture](#4-system-architecture) | [5 Data collection](#5-data-collection) | [6 Kernel interfaces](#6-linux-kernel-interfaces-used) |
[7 Detection](#7-detection-algorithms) | [8 Correlation](#8-multi-metric-correlation) | [9 PSI](#9-psi-integration) |
[10 Attribution](#10-process-attribution) | [11 Baseline](#11-baseline-based-detection) | [12 Explainability](#12-explainable-diagnosis) |
[13 Experiments](#13-controlled-experiment-methodology) | [14 Testing](#14-testing-and-validation) | [15 Install/run](#15-installation-and-execution) |
[16 Dependencies/permissions](#16-dependencies-and-permissions) | [17 Limitations](#17-known-limitations) | [OS concepts](#operating-systems-concepts-demonstrated)

Deeper references: [docs/DETECTION_MODEL.md](docs/DETECTION_MODEL.md) (exact rules and parameters),
[docs/VIVA.md](docs/VIVA.md) (viva questions), [GUIDE.md](GUIDE.md) (report/slide outline),
`data/experiments/` (real experiment reports produced by this code).

---

## 1. Objective
Build a monitor that can (1) detect *sustained* contention, (2) tell **utilisation** from **saturation**
from **contention** from **actual degradation**, (3) correlate several OS metrics, (4) identify likely causes
and responsible processes, (5) explain each diagnosis, (6) recommend evidence-specific actions and
(7) experimentally validate its own detection.

## 2. Problem statement
Resource monitors report numbers; operators must interpret them. "CPU 95%" may be a healthy batch job or a
starving web server. A threshold alone cannot tell them apart, produces false alarms, and blames whichever
process happened to be on top in the last sample. V2 treats *waiting* (queues, stalls) as the evidence of a
bottleneck and utilisation only as context.

## 3. How it works
Every `interval` seconds the engine collects a sample, evaluates **indicators** against static thresholds
and a learned baseline, requires each indicator to **persist** (3 of the last 5 samples by default),
classifies the resource as **ELEVATED / POTENTIAL / CONFIRMED**, grades **severity** and **confidence**
separately, attributes load to processes with identity checks, and writes an explanation.

| State | Meaning | Example |
|---|---|---|
| Normal | nothing unusual | |
| **ELEVATED** | busy/full, nothing observed waiting. *Not* a bottleneck claim | 100% CPU, no run queue, PSI ~0 |
| **POTENTIAL** | some contention evidence, incomplete or contradicted | PSI high but CPU only 30% |
| **CONFIRMED** | stall evidence + corroboration, for the whole persistence rule | PSI 45% + run queue > cores + 98% busy |
| Recovered | a POTENTIAL/CONFIRMED condition has cleared | |

## 4. System architecture
```
 kernel (/proc, /sys) -> collectors/ -> engine.MonitorEngine -> analyzer/ -> data/ (CSV, JSON) -> dashboard/, reports/
                         cpu_monitor     (one step = one       baseline.py     system_logs.csv    app.py (Streamlit)
                         memory_monitor   sample + detection)  attribution.py  alerts.csv         data.py (testable logic)
                         disk_monitor          ^               diagnosis.py    latest.json
                         process_monitor       |               bottleneck_detector.py
                         psi.py          experiments/ (same engine!)   validation.py, metrics.py (parsers, strict JSON)
```
Separation: collectors only read the kernel; `analyzer/` is pure (no I/O, deterministic, unit-tested with synthetic
samples); the dashboard only draws. `MonitorEngine` is shared by `main.py` **and** the experiments, so an
experiment exercises exactly the normal detection path; nothing is special-cased or forced.

## 5. Data collection
| Collector | Metrics |
|---|---|
| CPU | utilisation, iowait, load 1/5/15, context switches/s, run queue (`/proc/schedstat` wait time, else `procs_running`), CPU PSI |
| Memory | MemAvailable-based % used, available/cached/reclaimable MB, naive free-based % (for contrast), swap % and swap-in/out B/s, major faults/s, memory PSI |
| Disk | **per device**: util %, latency (await), queue depth, IOPS, MB/s; aggregates (documented below); I/O PSI |
| Process | per process (pid + start time): CPU % (100 = one core), % RAM, RSS, read/write B/s |

Rates use the *measured* interval (monotonic clock). Counter resets or missing interfaces yield `null`, never a
fake zero. Disk aggregates: `disk_util` = **max** over devices (always <= 100), `disk_util_mean`, throughput summed
over *top-layer* devices only (a device-mapper volume and its disk are not double counted). A collector that raises
is logged and its metrics become unavailable for that sample; monitoring continues.

## 6. Linux kernel interfaces used
| Interface | Used for | Needs |
|---|---|---|
| `/proc/stat` | CPU jiffies (util, iowait), `ctxt`, `procs_running/blocked` | any kernel |
| `/proc/schedstat` | exact run-queue wait time per interval | `CONFIG_SCHEDSTATS` (optional; falls back) |
| `/proc/loadavg` | load average | any |
| `/proc/meminfo` | MemTotal/Available/Free/Cached/Buffers/SReclaimable, swap | 3.14+ for MemAvailable |
| `/proc/vmstat` | `pswpin/pswpout` (swap), `pgmajfault` | any |
| `/proc/diskstats`, `/sys/block` | per-device I/O counters; whole-device and stacking (holders) detection | 2.6+ |
| `/proc/pressure/{cpu,memory,io}` | PSI | 4.20+ with PSI enabled (optional) |
| `/proc/<pid>/{stat,status,io}` via psutil | process CPU/RSS/I/O, start time | `io` readable only for own processes unless root |
| `/proc/mounts`, `/sys/fs/cgroup/memory.*` | experiment safety checks (tmpfs, cgroup limits) | |

## 7. Detection algorithms
Full tables in [docs/DETECTION_MODEL.md](docs/DETECTION_MODEL.md). Summary:
1. **Persistence** (kept from V1, configurable `--window/--required`): an *indicator* is present only if it holds in >= `required` of the last `window` samples.
2. **Indicator classes**: `stall` (PSI), `saturation` (run queue, latency, queue depth, swapping, iowait), `utilization`, `supporting` (context switches, load, throughput, major faults).
3. **State rules** (same for every resource): stall + (saturation or utilization) -> CONFIRMED; stall alone -> POTENTIAL; utilization alone -> ELEVATED. Without PSI: saturation + utilization -> CONFIRMED with confidence capped at MEDIUM. PSI quiet but saturation + utilization -> POTENTIAL with the contradiction listed.
4. **Severity** (INFO/WARNING/CRITICAL): magnitude; CRITICAL only if the critical level held for the *entire* window. ELEVATED is always INFO.
5. **Confidence** (LOW/MEDIUM/HIGH): HIGH needs PSI present, >= 3 agreeing indicators and no contradicting stall/saturation indicator. The raw `agree/available` ratio is always shown. No invented percentages. Severity and confidence are independent: a CRITICAL diagnosis can have LOW confidence.

## 8. Multi-metric correlation
* **CPU**: utilisation, CPU PSI (stall), run queue (saturation); load/core and context switches (supporting only).
  Context switches are interpreted *with* contention ("supports scheduler churn"), never alone.
* **Memory**: utilisation on MemAvailable (cache is *not* pressure), PSI some/full (stall), swap activity while RAM is nearly full (saturation; swap-in counts because it means re-faulting), major faults (supporting).
* **Disk**: per device: utilisation, latency, queue depth; system-wide I/O PSI some/full and iowait. Distinguishes **device saturation** (device evidence + stall) from **application-level I/O waiting** (stall but no saturated device) and from **busy-but-healthy** (util without stall). Throughput is context only.

## 9. PSI integration
`some` = share of time at least one task was stalled; `full` = all non-idle tasks stalled (productivity lost; CPU `full`
is not meaningful system-wide and is ignored). `avg10/60/300` are kernel-smoothed averages (lagging); `total` is cumulative
stall microseconds. **Detection uses the exact per-interval stall % computed from `total` deltas** (no smoothing lag, faster
recovery); avg10 is the fallback and avg300 gives acute-vs-chronic context. Unsupported/disabled PSI: fields are `null`,
the dashboard and report say so, CONFIRMED confidence is capped at MEDIUM, and detection falls back to saturation indicators.

## 10. Process attribution
Identity is **(pid, start_time)**, so a recycled PID never inherits history. A process is named only if it contributed in >= `required` samples of the window **and** explains a minimum share of the measured load. Verdicts: `primary` (>= 50% of load), `partial`, `multiple`, `none` (rankings keep changing: reported honestly, e.g. "top process changed 4 times"), `unavailable` (e.g. per-process I/O unreadable). Memory attribution reports RSS growth ("possible leak"). Output always carries the note that this is **correlation, not proof**. Per-process I/O (`/proc/<pid>/io`) cannot be mapped to a specific device; write attribution is approximate (page-cache writeback).

## 11. Baseline-based detection
Per metric (and per device) the engine keeps a rolling window (default 300 samples) and uses **median + MAD** (robust to spikes). A value deviates if `z >= 4`, exceeds the median by a minimum practical amount, **and** is above an absolute floor (so "every core busy" is not mistaken for contention against an idle baseline; this bug was found by the negative-control experiment, see section 14). Guards: warm-up (`min_samples`, default 60; before that only static thresholds run and the diagnosis says so), **freeze** (deviating values, and values from a resource with an active finding, are never learned), slow **adoption** of benign persistent shifts only when no contention evidence exists, persistence to disk, corrupt-file tolerance. Every alert states `method`: `static`, `baseline` or `static+baseline`. Static thresholds always remain the fallback. The warm-up period assumes the machine is normal while the baseline is first learned (values that trip a static threshold are still excluded).

## 12. Explainable diagnosis
Each diagnosis carries: resource (and device), state, severity, confidence + evidence ratio, duration, detection method, the full evidence list (class, present/absent/unavailable, values and thresholds), **contradicting evidence**, **missing evidence**, likely cause, responsible-process text and an evidence-specific recommendation. Example (from a real run of this project):

```
Disk I/O [vda] contention confirmed: vda: busy 90%; vda: throughput 658 MB/s; I/O PSI some 86%; I/O PSI full 66%
State CONFIRMED | Severity WARNING | Confidence MEDIUM | Method static+baseline
Likely cause: Device-level saturation with tasks actually waiting on I/O ...
```

## 13. Controlled experiment methodology
`python experiment.py run <cpu|memory|disk|cpu-benign|control>` runs: **baseline** (idle) -> **stress** (bounded workload) -> **recovery**, then writes `report.md/json` and `timeline.png`. The runner only observes; it never forces an alert.
* `cpu`: oversubscribe cores (2 x cores workers). `memory`: bounded allocation (<= 95% of *available*, free-memory floor) plus a bounded file-cache churn that makes the kernel report reclaim stalls. `disk`: bounded rewrite+fsync of one file per worker in a private directory.
* `cpu-benign` (**negative control**): one busy worker per core: utilisation ~100% with no oversubscription. A correct engine says ELEVATED, never contention. `control`: nothing started: false-positive check.
* **Safety**: explicit start (`--yes` or interactive confirm), bounded duration/workers/size, nice 10, no root, protected-path and tmpfs refusal for disk, free-space reserve, cgroup-aware memory limit and OOM-first-victim setting, cleanup of the process groups it created (never other processes), worker self-deadline and `PR_SET_PDEATHSIG` so workers die even if the supervisor is killed, Ctrl+C/`cancel` = clean stop. `stress-ng` is used if installed (`--backend auto`), otherwise the built-in generator.
* **Metrics**: detection (confirmed / potential), false positives, detection latency, recovery time (detector view and metric view), diagnostic reliability, attribution reliability, plus an independent **"was the stress effective"** check on raw kernel counters so "the engine missed it" is distinguished from "there was nothing to detect". Reports label results **MEASURED** vs **INFERRED**.

### Results from this project's runs (kernel 6.18 VM, 1 core, 4 GB RAM, no swap, `stress-ng` not installed)
| Experiment | Verdict | Key measurements (single run each; see `data/experiments/`) |
|---|---|---|
| cpu | PASS | confirmed 3.0 s after start, attribution 28/28 correct, detector cleared 3.1 s after stop |
| cpu-benign | PASS | CPU ~100% for 30 s; **0** POTENTIAL/CONFIRMED samples (ELEVATED only) |
| disk | PASS | confirmed 3.0 s, correct device, attribution 27 correct / 0 wrong / 1 abstained, cleared 4.4 s |
| control (idle) | PASS | 0 false positives |
| memory, default thresholds | **borderline** | this run PASS (confirmed after 10 s, but in only 10% of stress samples); an earlier run on the same machine FAILED. Kernel memory stall (PSI full ~1.9%) sits right at the 2% threshold, so the outcome flips run to run |
| memory, `psi_mem_full: 1.5` | PASS (tuned, separate run) | confirmed 9 s, 73% of stress samples, attribution 100%, cleared 3.4 s |

Pooled false-positive rate over baseline phases and controls: **0/160 samples**. Caveats: n = 1 per experiment (no statistical confidence), one machine; alerts on *other* resources occurred during stress (e.g. CPU/Disk during the memory test: plausible side effects, which the harness cannot tell from false alarms and scores conservatively). In the calibration table `CONFIRMED/HIGH` was right 28/28 and `POTENTIAL/LOW` 0/28; that sample is tiny and polluted by those side effects. Treat all of this as evidence, not proof.

## 14. Testing and validation
`python -m unittest discover -s tests -v` (189 tests across 10 modules, about 1 minute: roughly 55 s of that is real short experiment runs and process-cleanup checks; everything else uses mocked metrics or fake `/proc` trees and is deterministic).
Covered: normal behaviour, temporary spikes, sustained CPU/memory/disk pressure, false-positive scenarios (busy CPU, high RAM use with cache, high throughput), missing/NaN/inf/None/string metrics, missing PSI and unsupported interfaces, changing process rankings, PID reuse and termination, baseline warm-up/freeze/adoption/persistence, recovery events, config validation, strict JSON, counter resets, experiment safety refusals, real process cleanup including SIGKILL of the supervisor, evaluation maths on synthetic timelines, runner cancel handling, dashboard logic. Deliberately breaking the state rules (mutation checks) is caught by the suite.
Two bugs were found *only* by real experiments: a baseline false positive on busy-but-uncontended CPU (caught by the `cpu-benign` run) and a misleading FAIL verdict when a memory stress never produced pressure (now reported as `INEFFECTIVE_STRESS`).

## 15. Installation and execution
```bash
sudo apt install python3 python3-venv            # optional: stress-ng
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt                  # psutil streamlit matplotlib pandas
python main.py                                   # collector + detector (terminal 1)
streamlit run dashboard/app.py                   # dashboard (terminal 2)
python reports/report_generator.py               # -> reports/summary.md
python -m unittest discover -s tests -v
```
`main.py` options: `--interval 2 --duration 0 --data-dir data --window 5 --required 3 --config config.example.json --no-baseline --reset-baseline --baseline-min-samples 60 --z-threshold 4`. Invalid values print a readable error and exit with code 2.

Experiments (non-production machine only):
```bash
python experiment.py check                       # prerequisites and permissions
python experiment.py run cpu                     # add --duration 30 --baseline 20 --recovery 30 --workers N
python experiment.py run memory --memory-pct 80  # 5-95 % of AVAILABLE RAM (>90 needs a disposable machine)
python experiment.py run disk --disk-path /var/tmp --disk-mb 256
python experiment.py run cpu-benign              # negative control
python experiment.py run control                 # idle false-positive check
python experiment.py suite --repeats 3           # everything, aggregate suite.md (~6 min per repeat)
python experiment.py report                      # re-aggregate saved runs
python experiment.py cancel                      # stop a running experiment cleanly (also Ctrl+C)
```
The dashboard's **Experiments** tab runs the same commands (behind a confirmation checkbox); the **Results** tab shows the reports.

### Automated campaign and packaging
```bash
python campaign_runner.py                # show the 15-run plan (5 categories x 3 repeats), run nothing
python campaign_runner.py --dry-run      # simulate manifest checkpointing, no load
python campaign_runner.py --test         # one harmless 5 s control run
python campaign_runner.py --run          # execute the campaign (resumable; Ctrl+C is safe)
python package_project.py                # verify essential files, build dist/*.tar.gz + SHA256SUMS
```
`campaign_runner.py` writes to `data/campaign_runs/` (manifest, per-run reports, aggregate `suite.md/json`), uses deliberately
conservative bounds (no root, `nice 10`, 10% of *available* RAM, 64 MB disk files) and a lock file to refuse concurrent campaigns.
Campaign results (36-core / 62 GB host, 15 formal runs + 1 earlier control = 16 in `suite.md`): control and cpu-benign **PASS** (0/250
false-positive samples); disk **PARTIAL** (POTENTIAL detected ~3 s, correct attribution, never CONFIRMED); cpu and memory
**INEFFECTIVE_STRESS** (the safety-bounded workload created no kernel-visible pressure on such a large host, so the confirmed
path was not exercised there). See `reports/COMPREHENSIVE_REPORT.md` and `reports/VIVA_DEFENSE_GUIDE.md`.

## 16. Dependencies and permissions
Tested on Python 3.12 (older 3.x not tested), `psutil`, `pandas`, `matplotlib`, `streamlit` (dashboard only). Linux only. No root needed; as non-root, per-process I/O of other users' processes is unavailable (reported as unavailable, so disk attribution degrades to "unavailable"). Optional: `stress-ng`, `/proc/schedstat`, PSI. No new third-party dependency was added in V2.

## 17. Known limitations
* **Dashboard not executed during development**: Streamlit could not be installed in the build sandbox. `dashboard/app.py` compiles and all its logic (`dashboard/data.py`) is unit-tested, but the page itself has not been rendered by me. Run it and check it.
* The `stress-ng` backend was not exercised against the real binary (not installed); its command lines and lifecycle are tested with a fake binary only.
* Experiments ran on one single-core VM without swap; memory-stall detection there is borderline (section 13). Real swap or cgroup-constrained systems behave differently; thresholds are machine-dependent and configurable.
* Run-queue accuracy needs `/proc/schedstat`; the fallback samples `procs_running` instantaneously and is noisier (this sandbox had no schedstat, so the fallback is the path that was validated end to end; the schedstat path is covered by fake-`/proc` unit tests only).
* Per-process I/O is not per device; write attribution is approximate; other users' I/O needs root.
* Attribution only sees the top-N processes per resource (union of leaders); a diffuse load is reported as "none".
* PSI `cpu full` is ignored (not meaningful system-wide); cgroup-scoped pressure is not monitored.
* Confidence is a rule-based categorical grade, not a statistical probability.
* Single host; no network or GPU bottlenecks; thresholds are heuristics.

---
## Operating Systems concepts demonstrated
| Concept | Where |
|---|---|
| Process management | process identity (PID + start time), PID reuse, process states and the D-state/load relationship, process groups and signals in the experiment supervisor, orphan handling (`PDEATHSIG`) |
| CPU scheduling / runnable processes | run queue from scheduler statistics (`schedstat`), runnable vs running vs waiting, load average semantics, context switches as scheduler churn, nice priority |
| Memory management | MemAvailable vs free, page cache and reclaim, working set, RSS growth, cgroup limits, OOM killer and `oom_score_adj` |
| Paging and swapping | swap-in vs swap-out rates, thrashing, major page faults, why swap *activity* matters more than swap *usage* |
| I/O management | per-device util/latency/queue depth, device saturation vs application-level waiting, page cache vs device writes (`fsync`), write-back, device stacking (device-mapper) |
| Kernel interfaces | `/proc`, `/sys`, PSI, cumulative counters and why two samples are required |
| Resource contention | utilisation vs saturation vs stall (the USE method plus PSI), persistence to separate spikes from sustained contention |
| System performance analysis | multi-metric evidence, baselines, false-positive control with negative experiments, validation methodology |
