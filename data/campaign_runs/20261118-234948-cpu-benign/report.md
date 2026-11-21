# Experiment report: cpu-benign  (20261118-234948-cpu-benign)

**Verdict: PASS**  |  status: completed  |  expected: High CPU utilisation but NO POTENTIAL/CONFIRMED diagnosis (nothing is waiting)

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 8
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: Negative control: one CPU-bound worker per core keeps utilisation ~100% WITHOUT oversubscription. A correct engine reports at most ELEVATED here.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 8.939 | 27.434 | 6.521 |
| psi_cpu_some_pct | 0.248 | 0.317 | 0.172 |
| runq_wait_per_core | 0.001 | 0.006 | 0.001 |
| procs_running | 1.6 | 9.267 | 7.2 |
| mem_percent | 6.431 | 6.487 | 6.403 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.38 | 0.736 | 1.0 |
| disk_await_ms | 6.425 | 3.417 | 4.294 |
| psi_io_some_pct | 0.014 | 0.004 | 0.009 |
| disk_write_mbs | 0.335 | 0.036 | 0.184 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 15
- positive_samples: 0
- false_positive_rate: 0.0
- samples_flagged_elevated_only: 0
- mean_cpu_percent: 27.434

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
