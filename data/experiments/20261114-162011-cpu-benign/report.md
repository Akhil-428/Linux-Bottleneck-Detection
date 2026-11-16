# Experiment report: cpu-benign  (20261114-162011-cpu-benign)

**Verdict: PASS**  |  status: completed  |  expected: High CPU utilisation but NO POTENTIAL/CONFIRMED diagnosis (nothing is waiting)

## Setup
- Phases: baseline 20.0s, stress 30.0s, recovery up to 30.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 1
- Machine: 1 core(s), 3992 MB RAM, kernel 6.18.44-fc-v470, PSI cpu/mem/io = True/True/True, schedstat = False
- Note: stress-ng not found: using the built-in Python workload generator.
- Note: Negative control: one CPU-bound worker per core keeps utilisation ~100% WITHOUT oversubscription. A correct engine reports at most ELEVATED here.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 0.548 | 100.0 | 0.988 |
| psi_cpu_some_pct | 0.032 | 1.535 | 0.489 |
| runq_wait_per_core | n/a | n/a | n/a |
| procs_running | 1.0 | 2.0 | 1.0 |
| mem_percent | 6.233 | 6.091 | 6.091 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.0 | 0.013 | 0.0 |
| disk_await_ms | 0.25 | 0.342 | 0.233 |
| psi_io_some_pct | 0.009 | 0.029 | 0.008 |
| disk_write_mbs | 0.004 | 0.011 | 0.014 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/20 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 30
- positive_samples: 0
- false_positive_rate: 0.0
- samples_flagged_elevated_only: 28
- mean_cpu_percent: 100.0

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
