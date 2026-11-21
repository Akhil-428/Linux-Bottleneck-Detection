# Experiment report: disk  (20261118-235119-disk)

**Verdict: PARTIAL**  |  status: completed  |  expected: CONFIRMED contention on Disk I/O during stress

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 2, disk 64 MB x workers on ext4
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 8.16 | 11.398 | 8.229 |
| psi_cpu_some_pct | 0.229 | 0.359 | 0.247 |
| runq_wait_per_core | 0.001 | 0.001 | 0.001 |
| procs_running | 2.8 | 4.067 | 2.143 |
| mem_percent | 6.446 | 6.464 | 6.423 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.37 | 61.89 | 28.571 |
| disk_await_ms | 6.275 | 0.335 | 122.363 |
| psi_io_some_pct | 0.01 | 4.736 | 2.847 |
| disk_write_mbs | 0.045 | 1190.289 | 5.214 |
| disk_read_mbs | 0.0 | 1183.037 | 6.285 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **True** - busiest disk reached 64% util, kernel I/O stall (PSI some) 4.736% (needs >= 50% or >= 1%)
- Target resource: **Disk I/O**
- Confirmed: False  (latency n/a after workload start)
- Potential or confirmed: True  (latency 3.06 s)
- Stress samples with the target CONFIRMED: 0.0
- Resources CONFIRMED during stress: none
- Diagnostic reliability (target among confirmed): False; only target: False
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 12, wrong 0, abstained 1, reliability 1.0 (workload PIDs known: True)

## First diagnosis produced by the engine

**Disk I/O [nvme0n1] potential pressure: nvme0n1: busy 60%; nvme0n1: avg queue depth 6.75; nvme0n1: throughput 2399.1 MB/s**

- State: POTENTIAL  |  Severity: WARNING  |  Confidence: LOW  |  Detection method: static+baseline
- Likely cause *(INFERRED)*: The device is slow or queued, but the kernel reports no task stalls: likely background I/O (writeback) not yet hurting foreground work.
- Responsible process: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (agy, python).
- Recommendation: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (agy, python).

Evidence:
  - [utilization/present] nvme0n1: busy 60% (threshold 80%); baseline median 1.4, z=11.7 - exceeded in 3/5 samples
  - [saturation/absent] nvme0n1: avg latency 0.3 ms (threshold 30 ms); baseline median 6.5, z=-3.1 - exceeded in 0/5 samples
  - [saturation/present] nvme0n1: avg queue depth 6.75 (threshold 2.0) - exceeded in 3/5 samples
  - [supporting/present] nvme0n1: throughput 2399.1 MB/s; baseline median 0.0, z=479.8 - exceeded in 3/5 samples
  - [stall/absent] I/O PSI some 4.2% (threshold 20%); baseline median 0.0, z=4.2 - exceeded in 0/5 samples
  - [stall/absent] I/O PSI full 4.1% (threshold 5%; all non-idle tasks stalled) - exceeded in 0/5 samples
  - [saturation/absent] iowait 2.3% of CPU time (threshold 20%); baseline median 0.0, z=1.2 - exceeded in 0/5 samples

Contradicting evidence:
  - nvme0n1: avg latency 0.3 ms (threshold 30 ms); baseline median 6.5, z=-3.1 - exceeded in 0/5 samples
  - I/O PSI some 4.2% (threshold 20%); baseline median 0.0, z=4.2 - exceeded in 0/5 samples
  - I/O PSI full 4.1% (threshold 5%; all non-idle tasks stalled) - exceeded in 0/5 samples
  - iowait 2.3% of CPU time (threshold 20%); baseline median 0.0, z=1.2 - exceeded in 0/5 samples

## Recovery *(MEASURED)*

- Detector reported 'cleared' 3.1 s after the workload stopped
- `disk_util` back near its baseline after n/a
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
