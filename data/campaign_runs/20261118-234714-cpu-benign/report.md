# Experiment report: cpu-benign  (20261118-234714-cpu-benign)

**Verdict: PASS**  |  status: completed  |  expected: High CPU utilisation but NO POTENTIAL/CONFIRMED diagnosis (nothing is waiting)

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 8
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: Negative control: one CPU-bound worker per core keeps utilisation ~100% WITHOUT oversubscription. A correct engine reports at most ELEVATED here.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 6.961 | 27.684 | 6.258 |
| psi_cpu_some_pct | 0.194 | 0.317 | 0.122 |
| runq_wait_per_core | 0.001 | 0.006 | 0.0 |
| procs_running | 1.4 | 13.6 | 13.8 |
| mem_percent | 6.408 | 6.46 | 6.368 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.43 | 0.885 | 0.86 |
| disk_await_ms | 6.685 | 3.508 | 3.376 |
| psi_io_some_pct | 0.008 | 0.003 | 0.007 |
| disk_write_mbs | 1.02 | 0.042 | 0.068 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 15
- positive_samples: 0
- false_positive_rate: 0.0
- samples_flagged_elevated_only: 0
- mean_cpu_percent: 27.684

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
