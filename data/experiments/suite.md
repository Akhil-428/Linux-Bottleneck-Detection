# Validation suite (5 runs)

## Results per experiment *(MEASURED)*

### cpu
- runs: 1, verdicts: {'PASS': 1}
- detection rate (confirmed): 1.0, (potential+): 1.0
- detection latency, confirmed (s): {'n': 1, 'mean': 3.02, 'median': 3.02, 'min': 3.02, 'max': 3.02}
- diagnostic reliability: 1.0
- attribution: {'correct': 28, 'wrong': 0, 'abstained': 0, 'reliability': 1.0}
- recovery, detector (s): {'n': 1, 'mean': 3.06, 'median': 3.06, 'min': 3.06, 'max': 3.06}; metric (s): {'n': 1, 'mean': 1.06, 'median': 1.06, 'min': 1.06, 'max': 1.06}

### cpu-benign
- runs: 1, verdicts: {'PASS': 1}

### disk
- runs: 1, verdicts: {'PASS': 1}
- detection rate (confirmed): 1.0, (potential+): 1.0
- detection latency, confirmed (s): {'n': 1, 'mean': 3.01, 'median': 3.01, 'min': 3.01, 'max': 3.01}
- diagnostic reliability: 1.0
- attribution: {'correct': 27, 'wrong': 0, 'abstained': 1, 'reliability': 1.0}
- recovery, detector (s): {'n': 1, 'mean': 4.36, 'median': 4.36, 'min': 4.36, 'max': 4.36}; metric (s): {'n': 1, 'mean': 2.36, 'median': 2.36, 'min': 2.36, 'max': 2.36}

### control
- runs: 1, verdicts: {'PASS': 1}

### memory
- runs: 1, verdicts: {'PASS': 1}
- detection rate (confirmed): 1.0, (potential+): 1.0
- detection latency, confirmed (s): {'n': 1, 'mean': 10.01, 'median': 10.01, 'min': 10.01, 'max': 10.01}
- diagnostic reliability: 1.0
- attribution: {'correct': 3, 'wrong': 0, 'abstained': 0, 'reliability': 1.0}
- recovery, detector (s): {'n': 1, 'mean': 1.31, 'median': 1.31, 'min': 1.31, 'max': 1.31}; metric (s): {'n': 1, 'mean': 1.31, 'median': 1.31, 'min': 1.31, 'max': 1.31}

## False-positive rate *(MEASURED)*

0/160 samples = 0.0  (pooled over baseline phases, control runs and the cpu-benign negative control)

## Confidence calibration *(MEASURED, small sample)*

| state/confidence | diagnoses | correct | precision |
|---|---|---|---|
| CONFIRMED/HIGH | 28 | 28 | 1.0 |
| CONFIRMED/MEDIUM | 57 | 31 | 0.544 |
| POTENTIAL/LOW | 28 | 0 | 0.0 |

Precision = share of sample-level POTENTIAL/CONFIRMED diagnoses (outside recovery) that were produced while that very resource was being stressed. Small samples: treat as a sanity check, not as a statistical calibration.

## Individual runs

- 20261114-161914-cpu: PASS
- 20261114-162011-cpu-benign: PASS
- 20261114-162109-disk: PASS
- 20261114-162209-control: PASS
- 20261114-162307-memory: PASS
