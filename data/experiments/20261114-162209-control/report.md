# Experiment report: control  (20261114-162209-control)

**Verdict: PASS**  |  status: completed  |  expected: No POTENTIAL/CONFIRMED diagnosis on an idle system

## Setup
- Phases: baseline 20.0s, stress 30.0s, recovery up to 30.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: none, workers: 0
- Machine: 1 core(s), 3992 MB RAM, kernel 6.18.44-fc-v470, PSI cpu/mem/io = True/True/True, schedstat = False
- Note: Control run: NO workload is started; measures false positives on a quiet system.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 1.141 | 0.862 | 0.798 |
| psi_cpu_some_pct | 0.067 | 0.038 | 0.016 |
| runq_wait_per_core | n/a | n/a | n/a |
| procs_running | 1.0 | 1.0 | 1.0 |
| mem_percent | 6.515 | 6.427 | 6.427 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.0 | 0.053 | 0.0 |
| disk_await_ms | 0.224 | 0.246 | 0.2 |
| psi_io_some_pct | 0.007 | 0.007 | 0.003 |
| disk_write_mbs | 0.009 | 0.015 | 0.004 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/20 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 30
- positive_samples: 0
- false_positive_rate: 0.0
- note: baseline-phase positives are counted separately (no double counting)

## Cleanup *(MEASURED)*

- Workload processes still alive after stop: none

## What is verified vs inferred

- Measured: metric means per phase, detection latency, recovery times, PIDs of the workload processes (read from the OS), whether any workload process survived cleanup.
- Inferred (not independently verified): the 'likely cause' text of each diagnosis and any statement about side-effect resources.

## Limitations

- A single run is an anecdote; use `python experiment.py suite --repeats N` for rates.
- Latency is quantised by the sampling interval and the 3-of-5 persistence rule.
- Other activity on the machine during the run can add noise (the harness cannot remove it).
