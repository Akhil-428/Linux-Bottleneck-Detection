# Experiment report: memory  (20261118-235323-memory)

**Verdict: INEFFECTIVE_STRESS**  |  status: completed  |  expected: CONFIRMED contention on Memory during stress

## Setup
- Phases: baseline 10.0s, stress 15.0s, recovery up to 15.0s; interval 1.0s; persistence 3 of 5 samples
- Workload backend: builtin, workers: 1, memory target 5984 MB
- Machine: 36 core(s), 63930 MB RAM, kernel 7.0.0-38-generic, PSI cpu/mem/io = True/True/True, schedstat = True
- Note: Memory stress adds a bounded 512 MB file-cache churn in /var/tmp so that reclaim stalls are actually produced (private file, deleted afterwards).
- Note: On a machine with ample RAM and no swap, 80% may raise utilisation WITHOUT any kernel-reported stall; that is correct engine behaviour and the report will say the stress was ineffective rather than blame the detector.
- Note: Memory worker is marked first-to-kill for the OOM killer (oom_score_adj=1000).

## Measured metrics by phase  *(MEASURED)*

| Metric | Baseline | Stress | Recovery |
|---|---|---|---|
| cpu_percent | 7.271 | 9.275 | 5.692 |
| psi_cpu_some_pct | 0.196 | 0.146 | 0.128 |
| runq_wait_per_core | 0.001 | 0.001 | 0.001 |
| procs_running | 1.2 | 2.4 | 1.4 |
| mem_percent | 6.404 | 15.273 | 6.452 |
| psi_mem_some_pct | 0.0 | 0.0 | 0.0 |
| swap_out_s | 0.0 | 0.0 | 0.0 |
| disk_util | 1.29 | 2.022 | 1.0 |
| disk_await_ms | 6.575 | 6.331 | 4.65 |
| psi_io_some_pct | 0.009 | 0.176 | 0.019 |
| disk_write_mbs | 0.039 | 34.198 | 0.055 |
| disk_read_mbs | 0.0 | 0.0 | 0.0 |

False positives in the baseline phase: 0/10 samples *(MEASURED)*

## Detection *(MEASURED)*

- Was the stress effective (kernel counters, independent of the detector)? **False** - kernel memory stall (PSI some) 0.0%, swap-out 0.0 B/s (needs PSI >= 1% or swapping)
- Target resource: **Memory**
- Confirmed: False  (latency n/a after workload start)
- Potential or confirmed: False  (latency n/a)
- Stress samples with the target CONFIRMED: 0.0
- Resources CONFIRMED during stress: none
- Diagnostic reliability (target among confirmed): False; only target: False
  - Other resources may be genuine side effects of the stress (e.g. memory pressure causing swap I/O); the harness cannot tell side effects from false alarms.
- Attribution: correct 0, wrong 0, abstained 0, reliability n/a (workload PIDs known: True)

## Recovery *(MEASURED)*

- Detector reported 'cleared' n/a after the workload stopped
- `mem_percent` back near its baseline after 1.29 s
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
