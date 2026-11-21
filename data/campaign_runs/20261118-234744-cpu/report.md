# Experiment report: cpu  (20261118-234744-cpu)

**Verdict: INEFFECTIVE_STRESS**  |  status: completed  |  expected: CONFIRMED contention on CPU during stress

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 12
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: 12 CPU-bound workers on 36 core(s): demand does NOT exceed CPU capacity.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 15.174 | 40.831 | 7.287 |
| psi_cpu_some_pct | 0.367 | 1.038 | 0.139 |
| runq_wait_per_core | 0.003 | 0.022 | 0.001 |
| procs_running | 7.0 | 17.8 | 7.6 |
| mem_percent | 6.454 | 6.598 | 6.429 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.56 | 1.431 | 1.54 |
| disk_await_ms | 6.355 | 6.142 | 5.757 |
| psi_io_some_pct | 0.008 | 0.002 | 0.004 |
| disk_write_mbs | 0.383 | 0.036 | 0.068 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **False** - CPU 40.831% busy, kernel CPU stall (PSI some) 1.038% (needs >= 90% and >= 10%)
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
- `cpu_percent` back near its baseline after 1.12 s
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
