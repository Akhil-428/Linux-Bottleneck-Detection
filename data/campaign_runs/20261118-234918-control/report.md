# Experiment report: control  (20261118-234918-control)

**Verdict: PASS**  |  status: completed  |  expected: No POTENTIAL/CONFIRMED diagnosis on an idle system

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: none, workers: 0
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: Control run: NO workload is started; measures false positives on a quiet system.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 8.122 | 9.648 | 15.459 |
| psi_cpu_some_pct | 0.224 | 0.246 | 0.569 |
| runq_wait_per_core | 0.001 | 0.002 | 0.005 |
| procs_running | 5.2 | 3.467 | 7.8 |
| mem_percent | 6.452 | 6.445 | 6.432 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 7.22 | 1.543 | 1.52 |
| disk_await_ms | 78.266 | 6.343 | 6.15 |
| psi_io_some_pct | 0.029 | 0.017 | 0.009 |
| disk_write_mbs | 0.393 | 0.152 | 0.06 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 15
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
