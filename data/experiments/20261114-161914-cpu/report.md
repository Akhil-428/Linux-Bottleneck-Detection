# Experiment report: cpu  (20261114-161914-cpu)

**Verdict: PASS**  |  status: completed  |  expected: CONFIRMED contention on CPU during stress

## Setup
- Phases: baseline 20.0s, stress 30.0s, recovery up to 30.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 2
- Machine: 1 core(s), 3992 MB RAM, kernel 6.18.44-fc-v470, PSI cpu/mem/io = True/True/True, schedstat = False
- Note: stress-ng not found: using the built-in Python workload generator.
- Note: 2 CPU-bound workers on 1 core(s): demand exceeds CPU capacity.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 0.796 | 100.0 | 1.119 |
| psi_cpu_some_pct | 0.061 | 99.874 | 0.379 |
| runq_wait_per_core | n/a | n/a | n/a |
| procs_running | 1.0 | 3.0 | 1.0 |
| mem_percent | 5.966 | 6.168 | 6.082 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.04 | 0.027 | 0.057 |
| disk_await_ms | 0.262 | 0.22 | 0.429 |
| psi_io_some_pct | 0.009 | 0.025 | 0.011 |
| disk_write_mbs | 0.011 | 0.006 | 0.004 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/20 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **True** - CPU 100.0% busy, kernel CPU stall (PSI some) 99.874% (needs >= 90% and >= 10%)
- Target resource: **CPU**
- Confirmed: True  (latency 3.02 s after workload start)
- Potential or confirmed: True  (latency 3.02 s)
- Stress samples with the target CONFIRMED: 0.933
- Resources CONFIRMED during stress: CPU
- Diagnostic reliability (target among confirmed): True; only target: True
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 28, wrong 0, abstained 0, reliability 1.0 (workload PIDs known: True)

## First diagnosis produced by the engine

**CPU contention confirmed: CPU utilisation 100%; CPU PSI some 99.8%; 2.00 runnable tasks per core (procs_running)**

- State: CONFIRMED  |  Severity: WARNING  |  Confidence: HIGH  |  Detection method: static+baseline
- Likely cause *(INFERRED)*: Sustained CPU demand exceeding available processing capacity: tasks are delayed waiting for a CPU.
- Responsible process: Multiple contributors: 'python3' (PID 131) 26% of a core avg, present in 3/5 samples, ~43% of measured load; 'python3' (PID 132) 26% of a core avg, present in 3/5 samples, ~42% of measured load.
- Recommendation: Several processes contribute ('python3' (PID 131), 'python3' (PID 132)); reduce their concurrency or priority.

Evidence:
  - [utilization/present] CPU utilisation 100% (threshold 85%); baseline median 1.0, z=19.8 - exceeded in 3/5 samples
  - [stall/present] CPU PSI some 99.8% (threshold 10%); baseline median 0.0, z=99.7 - exceeded in 3/5 samples
  - [saturation/present] 2.00 runnable tasks per core (procs_running) (threshold 1.25); baseline median 0.0, z=8.0 - exceeded in 3/5 samples
  - [supporting/absent] load1/core 0.21 (threshold 1.0; includes uninterruptible I/O waiters) - exceeded in 0/5 samples
  - [supporting/absent] context switches 297/s per core; baseline median 40.0, z=0.5 - exceeded in 0/5 samples

## Recovery *(MEASURED)*

- Detector reported 'cleared' 3.06 s after the workload stopped
- `cpu_percent` back near its baseline after 1.06 s
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
