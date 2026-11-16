# Experiment report: memory  (20261114-162307-memory)

**Verdict: PASS**  |  status: completed  |  expected: CONFIRMED contention on Memory during stress

## Setup
- Phases: baseline 20.0s, stress 30.0s, recovery up to 30.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 1, memory target 3476 MB
- Machine: 1 core(s), 3992 MB RAM, kernel 6.18.44-fc-v470, PSI cpu/mem/io = True/True/True, schedstat = False
- Note: stress-ng not found: using the built-in Python workload generator.
- Note: Memory stress adds a bounded 512 MB file-cache churn in /tmp so that reclaim stalls are actually produced (private file, deleted afterwards).
- Note: On a machine with ample RAM and no swap, 80% may raise utilisation WITHOUT any kernel-reported stall; that is correct engine behaviour and the report will say the stress was ineffective rather than blame the detector.
- Note: Memory worker is marked first-to-kill for the OOM killer (oom_score_adj=1000).

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 0.796 | 66.887 | 2.131 |
| psi_cpu_some_pct | 0.058 | 4.927 | 0.375 |
| runq_wait_per_core | n/a | n/a | n/a |
| procs_running | 1.0 | 1.533 | 1.0 |
| mem_percent | 6.444 | 88.763 | 7.338 |
| psi_mem_some_pct | 0.0 | 1.761 | 0.003 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.0 | 52.658 | 2.549 |
| disk_await_ms | 0.262 | 0.423 | 0.104 |
| psi_io_some_pct | 0.008 | 35.598 | 2.569 |
| disk_write_mbs | 0.008 | 17.162 | 0.019 |
| disk_read_mbs | 0.0 | 2698.801 | 4.29 |

False positives in the baseline phase: 0/20 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **True** - kernel memory stall (PSI some) 1.761%, swap-out 0.0 B/s (needs PSI >= 1% or swapping)
- Target resource: **Memory**
- Confirmed: True  (latency 10.01 s after workload start)
- Potential or confirmed: True  (latency 10.01 s)
- Stress samples with the target CONFIRMED: 0.1
- Resources CONFIRMED during stress: CPU, Disk I/O, Memory
- Diagnostic reliability (target among confirmed): True; only target: False
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 3, wrong 0, abstained 0, reliability 1.0 (workload PIDs known: True)

## First diagnosis produced by the engine

**Memory contention confirmed: memory used 93% (MemAvailable based); memory PSI full 2.1%**

- State: CONFIRMED  |  Severity: WARNING  |  Confidence: MEDIUM  |  Detection method: static+baseline
- Likely cause *(INFERRED)*: Tasks are stalling in memory reclaim, though swapping was not observed; memory is tight. About 267 MB is still counted as available (page cache 375 MB is reclaimable).
- Responsible process: Dominant contributor: 'python3' (PID 150) 87% of RAM avg, present in 5/5 samples, ~93% of measured load.
- Recommendation: Largest sustained holder: 'python3' (PID 150). It grew by 1 MB during the window (possible leak). Check for leaks or reduce its working set; otherwise add RAM.

Evidence:
  - [utilization/present] memory used 93% (MemAvailable based) (threshold 90%); baseline median 6.4, z=43.4 - exceeded in 5/5 samples
  - [stall/absent] memory PSI some 2.3% (threshold 10%); baseline median 0.0, z=2.3 - exceeded in 0/5 samples
  - [stall/present] memory PSI full 2.1% (threshold 2%; all non-idle tasks stalled) - exceeded in 3/5 samples
  - [saturation/absent] swapping out 0.00 MB/s, in 0.00 MB/s while RAM 93% used (needs RAM >= 80%); baseline median 0.0, z=0.0 - exceeded in 0/5 samples
  - [supporting/absent] major page faults 0/s - exceeded in 0/5 samples

Contradicting evidence:
  - memory PSI some 2.3% (threshold 10%); baseline median 0.0, z=2.3 - exceeded in 0/5 samples
  - swapping out 0.00 MB/s, in 0.00 MB/s while RAM 93% used (needs RAM >= 80%); baseline median 0.0, z=0.0 - exceeded in 0/5 samples

## Recovery *(MEASURED)*

- Detector reported 'cleared' 1.31 s after the workload stopped
- `mem_percent` back near its baseline after 1.31 s
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
