"""Historical baseline: robust rolling statistics per metric (median + MAD).

Why median/MAD instead of mean/stdev: a few spikes inside the window barely move the
median or MAD, so the baseline is not dragged towards recent abnormal behaviour.

Guards against "learning the problem as normal":
  1. WARM-UP    no deviation is reported until `min_samples` values exist.
  2. FREEZE     a value that deviates, or that belongs to a resource with an active
                static/contention finding (`hold`), is NOT added to the baseline.
  3. ADOPTION   a deviation that persists for `adopt_after` samples *without* any
                contention finding is considered a benign workload change and is
                adopted (the baseline resumes learning).  With contention evidence
                the baseline is never adopted - the static thresholds stay the authority.
  4. Optional global `frozen` flag (used by controlled experiments).
Only upward deviations matter for bottleneck detection.
"""
import json
import os
import time
from collections import deque

from .metrics import is_num, dumps_json

# family -> (min_scale, min_delta, min_abs)
#   min_scale  scale floor: stops MAD=0 (perfectly flat history) turning tiny wiggles into huge z-scores
#   min_delta  value must exceed the median by at least this much (practical significance)
#   min_abs    value must itself be at least this large.  A statistically "unusual" value is still
#              harmless if it is small in absolute terms: e.g. against an idle baseline a run queue of
#              exactly 1.0 per core is z=+30 but only means "every core is busy", not "tasks wait".
PARAMS = {
    "cpu_percent": (5.0, 20.0, 50.0), "psi_cpu": (1.0, 5.0, 3.0),
    "runq_wait": (0.1, 0.15, 0.15), "runq_run": (0.25, 1.0, 1.1),
    "ctx_per_core": (500.0, 5000.0, 2000.0), "mem_percent": (2.0, 8.0, 50.0), "psi_mem": (1.0, 5.0, 3.0),
    "swap_in_s": (50_000.0, 200_000.0, 200_000.0), "swap_out_s": (50_000.0, 200_000.0, 200_000.0),
    "psi_io": (1.0, 8.0, 3.0), "disk_await_ms": (2.0, 10.0, 5.0), "disk_util": (5.0, 25.0, 30.0),
    "disk_mbs": (5.0, 20.0, 20.0), "iowait": (2.0, 10.0, 5.0),
}
DEFAULT_PARAM = (1.0, 0.0, 0.0)


def family(key):
    return key.split(":", 1)[0]


def _median(sorted_vals):
    n = len(sorted_vals)
    mid = n // 2
    return sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2


class Baseline:
    def __init__(self, window=300, min_samples=60, z=4.0, adopt_after=900):
        self.window, self.min_samples, self.z, self.adopt_after = window, min_samples, z, adopt_after
        self.values = {}          # key -> deque of accepted values
        self.streak = {}          # key -> consecutive deviating samples
        self.frozen = False

    # ---- statistics -----------------------------------------------------
    def stats(self, key):
        """(median, robust_scale, n) or None if no data."""
        d = self.values.get(key)
        if not d:
            return None
        vals = sorted(d)
        med = _median(vals)
        mad = _median(sorted(abs(v - med) for v in vals))
        floor = PARAMS.get(family(key), DEFAULT_PARAM)[0]
        return med, max(1.4826 * mad, floor), len(vals)

    def deviation(self, key, value):
        """Compare one value with its baseline.  Never raises; unavailable -> not ready."""
        res = {"ready": False, "deviated": False, "z": None, "median": None,
               "scale": None, "n": len(self.values.get(key, ()))}
        st = self.stats(key)
        if st is None or not is_num(value):
            return res
        med, scale, n = st
        res.update(median=med, scale=scale, n=n)
        if n < self.min_samples:
            return res
        res["ready"] = True
        res["z"] = (value - med) / scale
        _, min_delta, min_abs = PARAMS.get(family(key), DEFAULT_PARAM)
        res["deviated"] = res["z"] >= self.z and (value - med) >= min_delta and value >= min_abs
        return res

    def readiness(self):
        """Smallest history length across tracked keys (for 'warming up' messages)."""
        return min((len(d) for d in self.values.values()), default=0)

    # ---- learning -------------------------------------------------------
    def update(self, values, hold=(), flags=None):
        """Learn from one sample.  `flags` = deviation results already computed for this
        sample (avoids a second computation); `hold` = keys that must not be learned."""
        for key, v in values.items():
            if not is_num(v):
                continue
            dev = (flags or {}).get(key) or self.deviation(key, v)
            blocked = key in hold or dev["deviated"]
            if self.frozen:
                continue
            if blocked:
                self.streak[key] = self.streak.get(key, 0) + 1
                benign_shift = dev["deviated"] and key not in hold
                if benign_shift and self.adopt_after and self.streak[key] >= self.adopt_after:
                    self._accept(key, v)           # persistent, contention-free shift: new normal.
                continue                           # (streak kept: keep adopting while the shift lasts)
            self._accept(key, v)
            self.streak[key] = 0

    def _accept(self, key, v):
        self.values.setdefault(key, deque(maxlen=self.window)).append(float(v))

    def reset(self):
        self.values.clear()
        self.streak.clear()

    # ---- persistence ----------------------------------------------------
    def save(self, path):
        tmp = path + ".tmp"
        payload = {"version": 1, "saved": time.time(),
                   "values": {k: list(d) for k, d in self.values.items()}}
        with open(tmp, "w") as f:
            f.write(dumps_json(payload))
        os.replace(tmp, path)

    def load(self, path):
        """Load a saved baseline.  A corrupt or missing file is ignored (returns False)."""
        try:
            with open(path) as f:
                data = json.load(f)
            vals = data["values"]
            loaded = {k: deque((float(x) for x in v if is_num(x)), maxlen=self.window)
                      for k, v in vals.items() if isinstance(v, list)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        if not loaded:
            return False
        self.values = loaded
        return True
