# Detection model reference (exact rules)

This document is the precise specification of what `analyzer/bottleneck_detector.py` does. Every number below
comes from `analyzer/validation.py` (`THRESHOLD_SPEC`) or `analyzer/baseline.py` (`PARAMS`).

## 1. Pipeline per sample
1. Normalise the sample (`None`, `NaN`, `inf`, bool and strings all mean *unavailable*).
2. Evaluate every indicator on the new sample: `static` (threshold) and `baseline` (statistical deviation).
   An indicator that cannot be evaluated is *unavailable* for that sample, not false.
3. Keep the last `window` samples (default 5). An indicator is **present** if `static or baseline` held in at least
   `required` (default 3) of them, **absent** if evaluable but not persistent, **unavailable** if never evaluable.
4. Classify by evidence class, then state, severity and confidence (below). Attribute processes. Track episodes.
5. Learn the baseline from the sample (never from anything flagged, see section 6).

## 2. Indicators and classes
| Resource | Indicator | Class | Static rule | Baseline family |
|---|---|---|---|---|
| CPU | utilisation | utilization | `cpu_percent >= cpu` | `cpu_percent` |
| CPU | CPU PSI (some) | **stall** | `psi_cpu_some_pct (else avg10) >= psi_cpu` | `psi_cpu` |
| CPU | run queue | saturation | `runq_wait_per_core >= runq_wait_per_core` (schedstat), else `(procs_running-1)/cores >= runnable_per_core` | `runq_wait` / `runq_run` |
| CPU | load average | supporting | `load1/cores >= load_per_core` | - |
| CPU | context switches | supporting | `ctx/s per core >= ctx_per_core` | `ctx_per_core` |
| Memory | utilisation | utilization | `mem_percent (MemAvailable based) >= mem` | `mem_percent` |
| Memory | PSI some | **stall** | `psi_mem_some >= psi_mem` | `psi_mem` |
| Memory | PSI full | **stall** | `psi_mem_full >= psi_mem_full` | - |
| Memory | swap activity | saturation | `mem_percent >= mem_low` AND (`swap_out >= swap_out_bps` OR `swap_in >= swap_in_bps`) | `swap_out_s` (gated by the RAM condition) |
| Memory | major faults | supporting | `>= major_faults_ps` | - |
| Disk (per device) | utilisation | utilization | `util >= disk_util` | `disk_util:<dev>` |
| Disk (per device) | latency | saturation | `await_ms >= disk_await_ms` AND device active | `disk_await_ms:<dev>` |
| Disk (per device) | queue depth | saturation | `aqu-sz >= disk_queue` | - |
| Disk (per device) | throughput | supporting | none (context only) | `disk_mbs:<dev>` |
| Disk (system) | I/O PSI some | **stall** | `psi_io_some >= psi_io` | `psi_io` |
| Disk (system) | I/O PSI full | **stall** | `psi_io_full >= psi_io_full` | - |
| Disk (system) | iowait | saturation | `iowait >= iowait` | `iowait` |

`supporting` indicators never change the state; they only appear in the explanation (and in confidence counting).

## 3. State rules (identical for CPU, memory, disk)
Let S = present stall indicators, A = present saturation, U = present utilization.

| Condition | State |
|---|---|
| S and (A or U) | CONFIRMED |
| S only | POTENTIAL |
| no stall indicator evaluable (PSI unavailable), A and U | CONFIRMED (confidence capped at MEDIUM by rule 5) |
| PSI unavailable, A only | POTENTIAL |
| PSI available but absent, A and U | POTENTIAL (the quiet PSI is listed as contradicting evidence) |
| otherwise, if any of S/A/U present | ELEVATED |
| none present | no diagnosis |

Disk: if a device has its own utilization/saturation evidence it gets its own diagnosis (the system-wide PSI is attached to it).
If nobody has device evidence but PSI is present, one diagnosis without a culprit device is produced and described as
**application-level I/O waiting**.

## 4. Severity (magnitude, separate from confidence)
ELEVATED -> INFO. POTENTIAL/CONFIRMED -> CRITICAL if the critical predicate held in **every** sample of a full window, else WARNING.
Critical predicates: CPU `util >= cpu_critical` or `psi_cpu >= psi_cpu_critical`; Memory `used >= mem_critical` or
`psi_some >= psi_mem_critical` or `psi_full >= 3 x psi_mem_full`; Disk any device `util >= disk_util_critical` or
`psi_io >= psi_io_critical` or `psi_io_full >= 3 x psi_io_full`.

## 5. Confidence (agreement, not probability)
`agree` = present indicators (all classes), `available` = evaluable indicators; shown as `agree/available`.
`contradicting` = evaluable stall/saturation indicators that are absent.
* CONFIRMED: **HIGH** if a stall indicator is present, `agree >= 3` and no contradicting indicator; otherwise MEDIUM.
* POTENTIAL: MEDIUM if `agree >= 3` and no contradiction; otherwise LOW.
* ELEVATED: LOW (confidence that it is a *bottleneck*).
Because a CONFIRMED diagnosis without PSI has no stall indicator present, it can never be HIGH.
Empirical sanity check (from the experiment runs): see `data/experiments/suite.md`, "Confidence calibration".

## 6. Baseline
| Family | min_scale | min_delta | min_abs |
|---|---|---|---|
| `cpu_percent` | 5.0 | 20.0 | 50.0 |
| `psi_cpu` | 1.0 | 5.0 | 3.0 |
| `runq_wait` | 0.1 | 0.15 | 0.15 |
| `runq_run` | 0.25 | 1.0 | 1.1 |
| `ctx_per_core` | 500.0 | 5000.0 | 2000.0 |
| `mem_percent` | 2.0 | 8.0 | 50.0 |
| `psi_mem` | 1.0 | 5.0 | 3.0 |
| `swap_in_s` | 50000.0 | 200000.0 | 200000.0 |
| `swap_out_s` | 50000.0 | 200000.0 | 200000.0 |
| `psi_io` | 1.0 | 8.0 | 3.0 |
| `disk_await_ms` | 2.0 | 10.0 | 5.0 |
| `disk_util` | 5.0 | 25.0 | 30.0 |
| `disk_mbs` | 5.0 | 20.0 | 20.0 |
| `iowait` | 2.0 | 10.0 | 5.0 |

Deviation = `z = (x - median) / max(1.4826 x MAD, min_scale) >= z_threshold` AND `x - median >= min_delta` AND `x >= min_abs`
(upward only). Defaults: `{'enabled': True, 'window': 300, 'min_samples': 60, 'z': 4.0, 'adopt_after': 900, 'path': None}`.
* **Warm-up**: no deviation until `min_samples`; the diagnosis lists "baseline warming up" under missing evidence.
* **Freeze**: a value is *not* learned if it deviates, if it tripped a static threshold, or if its resource has a POTENTIAL/CONFIRMED diagnosis.
* **Adoption**: after `adopt_after` consecutive deviating samples with no contention evidence the baseline resumes learning (benign workload change). `0` disables adoption. Experiments freeze the baseline once stress starts.
* **Method label**: per indicator `static`, `baseline` or `both`; the diagnosis shows `static`, `baseline` or `static+baseline`.

## 7. Process attribution rules
Identity `(pid, start_time)`; weak identity `(pid, name)` if the start time is unavailable (flagged). Contributor = value >= `attr_min_*`
in >= `required` window samples (absent = 0). Share = mean contribution / mean system load. `primary` share >= 0.5,
listed contributors need share >= 0.15. `partial` = one consistent contributor explaining less than half. `multiple` >= 2. `none` = no persistent
contributor (reports how many times the leader changed). Memory adds RSS growth over the window. I/O adds `unavailable` when no counters are readable.

## 8. Episodes, duration, recovery
An episode (resource, device) starts when a diagnosis first appears; `duration_s` runs from the first sample in the window that showed evidence.
When a POTENTIAL/CONFIRMED episode disappears, a recovery event is recorded (`recoveries`, `alerts.csv` state `RECOVERED`) and the resource
reports `RECOVERED` for one window before returning to `NORMAL`. An ELEVATED-only episode does not create a recovery event.

## 9. Thresholds (all configurable: `--config`, constructor keywords)
| Name | Default | Valid range | Meaning |
|---|---|---|---|
| `cpu` | 85 | 1-100 | CPU utilisation % that counts as 'high' (utilisation evidence) |
| `cpu_critical` | 95 | 1-100 | CPU utilisation % used for CRITICAL severity |
| `load_per_core` | 1.0 | 0.1-100 | load1 / cores treated as 'queueing' (supporting evidence) |
| `psi_cpu` | 10 | 0.1-100 | CPU PSI 'some' stall % (interval or avg10) = tasks waiting for CPU |
| `psi_cpu_critical` | 40 | 0.1-100 | CPU PSI 'some' % used for CRITICAL severity |
| `runq_wait_per_core` | 0.25 | 0.01-100 | avg tasks waiting for a CPU, per core (schedstat) |
| `runnable_per_core` | 1.25 | 0.1-100 | runnable tasks per core (procs_running fallback) |
| `ctx_per_core` | 20000 | 100-10000000 | context switches/s per core (supporting only) |
| `mem` | 90 | 1-100 | % memory used (MemAvailable based) that counts as 'high' |
| `mem_critical` | 95 | 1-100 | % memory used for CRITICAL severity |
| `mem_low` | 80 | 1-100 | memory % above which swapping is suspicious |
| `swap_out_bps` | 1000000 | 1-1000000000000.0 | bytes/s swapped out |
| `swap_in_bps` | 262144 | 1-1000000000000.0 | bytes/s swapped in (re-faulting = working set > RAM) |
| `psi_mem` | 10 | 0.1-100 | memory PSI 'some' stall % |
| `psi_mem_full` | 2 | 0.1-100 | memory PSI 'full' stall % (all non-idle tasks stalled) |
| `psi_mem_critical` | 30 | 0.1-100 | memory PSI 'some' % used for CRITICAL severity |
| `major_faults_ps` | 200 | 1-1000000000.0 | major page faults/s (supporting only) |
| `disk_util` | 80 | 1-100 | per-device %util that counts as 'busy' |
| `disk_util_critical` | 95 | 1-100 | per-device %util used for CRITICAL severity |
| `disk_await_ms` | 30 | 0.1-100000 | average I/O latency (ms) that counts as 'slow' |
| `disk_queue` | 2.0 | 0.1-100000 | average I/O queue depth (aqu-sz) that counts as 'queued' |
| `psi_io` | 20 | 0.1-100 | I/O PSI 'some' stall % |
| `psi_io_full` | 5 | 0.1-100 | I/O PSI 'full' stall % |
| `psi_io_critical` | 50 | 0.1-100 | I/O PSI 'some' % used for CRITICAL severity |
| `iowait` | 20 | 0.1-100 | iowait % of CPU time (supporting evidence) |
| `proc_cpu` | 80 | 1-100000 | single process CPU % (100 = one core) |
| `proc_mem` | 30 | 0.1-100 | single process % of RAM |
| `attr_min_cpu` | 20 | 1-100000 | min CPU % (of one core) for a process to count as a contributor |
| `attr_min_mem` | 5 | 0.1-100 | min % of RAM for a process to count as a memory contributor |
| `attr_min_io_bps` | 1000000 | 1-1000000000000.0 | min bytes/s for a process to count as an I/O contributor |
