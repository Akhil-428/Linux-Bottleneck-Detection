# Experiment report: disk  (20261114-162109-disk)

**Verdict: PASS**  |  status: completed  |  expected: CONFIRMED contention on Disk I/O during stress

## Setup
- Phases: baseline 20.0s, stress 30.0s, recovery up to 30.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 2, disk 256 MB x workers on ext4
- Machine: 1 core(s), 3992 MB RAM, kernel 6.18.44-fc-v470, PSI cpu/mem/io = True/True/True, schedstat = False
- Note: stress-ng not found: using the built-in Python workload generator.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 0.747 | 42.887 | 1.314 |
| psi_cpu_some_pct | 0.143 | 28.912 | 0.826 |
| runq_wait_per_core | n/a | n/a | n/a |
| procs_running | 1.05 | 1.267 | 1.0 |
| mem_percent | 6.174 | 8.716 | 6.451 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.02 | 81.014 | 3.599 |
| disk_await_ms | 0.35 | 3.17 | 0.531 |
| psi_io_some_pct | 0.022 | 77.459 | 3.622 |
| disk_write_mbs | 0.004 | 451.671 | 1.005 |
| disk_read_mbs | 0.0 | 442.513 | 2.0 |

False positives in the baseline phase: 0/20 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **True** - busiest disk reached 87% util, kernel I/O stall (PSI some) 77.459% (needs >= 50% or >= 1%)
- Target resource: **Disk I/O**
- Confirmed: True  (latency 3.01 s after workload start)
- Potential or confirmed: True  (latency 3.01 s)
- Stress samples with the target CONFIRMED: 0.933
- Resources CONFIRMED during stress: Disk I/O
- Diagnostic reliability (target among confirmed): True; only target: True
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 27, wrong 0, abstained 1, reliability 1.0 (workload PIDs known: True)

## First diagnosis produced by the engine

**Disk I/O [vda] contention confirmed: vda: busy 84%; vda: throughput 830.4 MB/s; I/O PSI some 80.6%; I/O PSI full 58.7%**

- State: CONFIRMED  |  Severity: WARNING  |  Confidence: MEDIUM  |  Detection method: static+baseline
- Likely cause *(INFERRED)*: Device-level saturation with tasks actually waiting on I/O: demand exceeds what this device can serve.
- Responsible process: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (python3).
- Recommendation: No single process was consistently responsible: the top process changed 2 time(s) across 5 samples (python3).

Evidence:
  - [utilization/present] vda: busy 84% (threshold 80%); baseline median 0.0, z=16.9 - exceeded in 3/5 samples
  - [saturation/absent] vda: avg latency 3.7 ms (threshold 30 ms); baseline median 0.0, z=1.9 - exceeded in 0/5 samples
  - [saturation/absent] vda: avg queue depth 4.63 (threshold 2.0) - exceeded in 2/5 samples
  - [supporting/present] vda: throughput 830.4 MB/s; baseline median 0.0, z=166.1 - exceeded in 3/5 samples
  - [stall/present] I/O PSI some 80.6% (threshold 20%); baseline median 0.0, z=80.6 - exceeded in 3/5 samples
  - [stall/present] I/O PSI full 58.7% (threshold 5%; all non-idle tasks stalled) - exceeded in 3/5 samples
  - [saturation/present] iowait 58.0% of CPU time (threshold 20%); baseline median 0.0, z=29.0 - exceeded in 3/5 samples

Contradicting evidence:
  - vda: avg latency 3.7 ms (threshold 30 ms); baseline median 0.0, z=1.9 - exceeded in 0/5 samples
  - vda: avg queue depth 4.63 (threshold 2.0) - exceeded in 2/5 samples

## Recovery *(MEASURED)*

- Detector reported 'cleared' 4.36 s after the workload stopped
- `disk_util` back near its baseline after 2.36 s
- Recovery samples observed: 8

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
