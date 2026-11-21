# Experiment report: control  (20261118-231934-control)

**Verdict: PASS**  |  status: completed  |  expected: No POTENTIAL/CONFIRMED diagnosis on an idle system

## Setup
- Phases: baseline 5.0s, stress 5.0s, recovery up to 5.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: none, workers: 0
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: Control run: NO workload is started; measures false positives on a quiet system.

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 4.309 | 4.499 | 4.823 |
| psi_cpu_some_pct | 0.152 | 0.171 | 0.177 |
| runq_wait_per_core | 0.0 | 0.0 | 0.0 |
| procs_running | 7.6 | 1.2 | 9.8 |
| mem_percent | 8.171 | 8.189 | 8.194 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 0.78 | 1.195 | 1.48 |
| disk_await_ms | 3.171 | 5.51 | 6.329 |
| psi_io_some_pct | 0.016 | 0.022 | 0.008 |
| disk_write_mbs | 0.051 | 0.105 | 0.041 |
| disk_read_mbs | 0.0 | 0.012 | 0.0 |

False positives in the baseline phase: 0/5 samples *(MEASURED)*

## Negative control *(MEASURED)*

- stress_samples: 5
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
