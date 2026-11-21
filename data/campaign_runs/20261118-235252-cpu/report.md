# Experiment report: cpu  (20261118-235252-cpu)

**Verdict: INEFFECTIVE_STRESS**  |  status: completed  |  expected: CONFIRMED contention on CPU during stress

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 12
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: 12 CPU-bound workers on 36 core(s): demand does NOT exceed CPU capacity.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 7.738 | 37.641 | 6.405 |
| psi_cpu_some_pct | 0.209 | 0.273 | 0.158 |
| runq_wait_per_core | 0.001 | 0.009 | 0.001 |
| procs_running | 1.3 | 13.667 | 1.2 |
| mem_percent | 6.431 | 6.522 | 6.389 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.28 | 1.444 | 1.4 |
| disk_await_ms | 6.25 | 6.485 | 5.625 |
| psi_io_some_pct | 0.013 | 0.001 | 0.011 |
| disk_write_mbs | 0.05 | 0.05 | 0.648 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **False** - CPU 37.641% busy, kernel CPU stall (PSI some) 0.273% (needs >= 90% and >= 10%)
- Target resource: **CPU**
- Confirmed: False  (latency n/a after workload start)
- Potential or confirmed: False  (latency n/a)
- Stress samples with the target CONFIRMED: 0.0
- Resources CONFIRMED during stress: none
- Diagnostic reliability (target among confirmed): False; only target: False
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 0, wrong 0, abstained 0, reliability n/a (workload PIDs known: True)

## Recovery *(MEASURED)*

- Detector reported 'cleared' n/a after the workload stopped
- `cpu_percent` back near its baseline after 1.13 s
- Recovery samples observed: 5

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
