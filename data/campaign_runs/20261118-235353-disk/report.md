# Experiment report: disk  (20261118-235353-disk)

**Verdict: PARTIAL**  |  status: completed  |  expected: CONFIRMED contention on Disk I/O during stress

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 2, disk 64 MB x workers on ext4
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 7.998 | 11.041 | 9.042 |
| psi_cpu_some_pct | 0.228 | 0.333 | 0.251 |
| runq_wait_per_core | 0.001 | 0.001 | 0.001 |
| procs_running | 1.5 | 4.733 | 2.714 |
| mem_percent | 6.456 | 6.502 | 6.446 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.29 | 64.195 | 15.6 |
| disk_await_ms | 5.698 | 0.424 | 84.401 |
| psi_io_some_pct | 0.01 | 5.098 | 0.086 |
| disk_write_mbs | 0.718 | 1122.383 | 1.474 |
| disk_read_mbs | 0.0 | 1115.546 | 1.5 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **True** - busiest disk reached 89% util, kernel I/O stall (PSI some) 5.098% (needs >= 50% or >= 1%)
- Target resource: **Disk I/O**
- Confirmed: False  (latency n/a after workload start)
- Potential or confirmed: True  (latency 3.05 s)
- Stress samples with the target CONFIRMED: 0.0
- Resources CONFIRMED during stress: none
- Diagnostic reliability (target among confirmed): False; only target: False
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 12, wrong 0, abstained 1, reliability 1.0 (workload PIDs known: True)

## First diagnosis produced by the engine

**Disk I/O [nvme0n1] potential pressure: nvme0n1: busy 63%; nvme0n1: avg queue depth 6.60; nvme0n1: throughput 2352.2 MB/s**

- State: POTENTIAL  |  Severity: WARNING  |  Confidence: LOW  |  Detection method: static+baseline
- Likely cause *(INFERRED)*: The device is slow or queued, but the kernel reports no task stalls: likely background I/O (writeback) not yet hurting foreground work.
- Responsible process: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (agy, python).
- Recommendation: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (agy, python).

Evidence:
  - [utilization/present] nvme0n1: busy 63% (threshold 80%); baseline median 1.3, z=12.3 - exceeded in 3/5 samples
  - [saturation/absent] nvme0n1: avg latency 0.3 ms (threshold 30 ms); baseline median 6.5, z=-3.1 - exceeded in 0/5 samples
  - [saturation/present] nvme0n1: avg queue depth 6.60 (threshold 2.0) - exceeded in 3/5 samples
  - [supporting/present] nvme0n1: throughput 2352.2 MB/s; baseline median 0.0, z=470.4 - exceeded in 3/5 samples
  - [stall/absent] I/O PSI some 3.9% (threshold 20%); baseline median 0.0, z=3.9 - exceeded in 0/5 samples
  - [stall/absent] I/O PSI full 3.8% (threshold 5%; all non-idle tasks stalled) - exceeded in 0/5 samples
  - [saturation/absent] iowait 2.5% of CPU time (threshold 20%); baseline median 0.0, z=1.2 - exceeded in 0/5 samples

Contradicting evidence:
  - nvme0n1: avg latency 0.3 ms (threshold 30 ms); baseline median 6.5, z=-3.1 - exceeded in 0/5 samples
  - I/O PSI some 3.9% (threshold 20%); baseline median 0.0, z=3.9 - exceeded in 0/5 samples
  - I/O PSI full 3.8% (threshold 5%; all non-idle tasks stalled) - exceeded in 0/5 samples
  - iowait 2.5% of CPU time (threshold 20%); baseline median 0.0, z=1.2 - exceeded in 0/5 samples

## Recovery *(MEASURED)*

- Detector reported 'cleared' 3.1 s after the workload stopped
- `disk_util` back near its baseline after 1.1 s
- Recovery samples observed: 7

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
