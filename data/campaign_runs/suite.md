# Validation suite (16 runs)

## Results per experiment *(MEASURED)*

### control
- runs: 4, verdicts: {'PASS': 4}

### cpu-benign
- runs: 3, verdicts: {'PASS': 3}

### cpu
- runs: 3, verdicts: {'INEFFECTIVE_STRESS': 3}
- detection rate (confirmed): 0.0, (potential+): 0.0
- detection latency, confirmed (s): None
- diagnostic reliability: 0.0
- attribution: {'correct': 0, 'wrong': 0, 'abstained': 0, 'reliability': None}
- recovery, detector (s): None; metric (s): {'n': 3, 'mean': 1.12, 'median': 1.12, 'min': 1.12, 'max': 1.13}

### memory
- runs: 3, verdicts: {'INEFFECTIVE_STRESS': 3}
- detection rate (confirmed): 0.0, (potential+): 0.0
- detection latency, confirmed (s): None
- diagnostic reliability: 0.0
- attribution: {'correct': 0, 'wrong': 0, 'abstained': 0, 'reliability': None}
- recovery, detector (s): None; metric (s): {'n': 3, 'mean': 1.31, 'median': 1.29, 'min': 1.29, 'max': 1.34}

### disk
- runs: 3, verdicts: {'PARTIAL': 3}
- detection rate (confirmed): 0.0, (potential+): 1.0
- detection latency, confirmed (s): None
- diagnostic reliability: 0.0
- attribution: {'correct': 36, 'wrong': 0, 'abstained': 3, 'reliability': 1.0}
- recovery, detector (s): {'n': 3, 'mean': 3.1, 'median': 3.1, 'min': 3.1, 'max': 3.11}; metric (s): {'n': 1, 'mean': 1.1, 'median': 1.1, 'min': 1.1, 'max': 1.1}

## False-positive rate *(MEASURED)*

0/250 samples = 0.0  (pooled over baseline phases, control runs and the cpu-benign negative control)

## Confidence calibration *(MEASURED, small sample)*

| state/confidence | diagnoses | correct | precision |
|---|---|---|---|
| POTENTIAL/LOW | 39 | 39 | 1.0 |

Precision = share of sample-level POTENTIAL/CONFIRMED diagnoses (outside recovery) that were produced while that very resource was being stressed. Small samples: treat as a sanity check, not as a statistical calibration.

## Individual runs

- 20261118-231934-control: PASS
- 20261118-234643-control: PASS
- 20261118-234714-cpu-benign: PASS
- 20261118-234744-cpu: INEFFECTIVE_STRESS
- 20261118-234815-memory: INEFFECTIVE_STRESS
- 20261118-234845-disk: PARTIAL
- 20261118-234918-control: PASS
- 20261118-234948-cpu-benign: PASS
- 20261118-235018-cpu: INEFFECTIVE_STRESS
- 20261118-235049-memory: INEFFECTIVE_STRESS
- 20261118-235119-disk: PARTIAL
- 20261118-235152-control: PASS
- 20261118-235222-cpu-benign: PASS
- 20261118-235252-cpu: INEFFECTIVE_STRESS
- 20261118-235323-memory: INEFFECTIVE_STRESS
- 20261118-235353-disk: PARTIAL
