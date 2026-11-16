"""Shared PSI reader used by the CPU, memory and disk collectors.

PSI semantics (Linux >= 4.20, /proc/pressure/{cpu,memory,io}):
  some   share of time in which AT LEAST ONE runnable task was stalled on the resource
  full   share of time in which ALL non-idle tasks were stalled simultaneously
         (the system did no useful work). For CPU, 'full' is not meaningful system-wide
         (it is reported but always ~0 on older kernels) so detection never relies on it.
  avg10/60/300  exponentially weighted averages over ~10 s / 60 s / 300 s: a smoothed
         trend with lag.  Used for context (acute vs chronic pressure).
  total  cumulative stall microseconds since boot.  The difference between two samples
         divided by the interval is the EXACT stall % for that interval, with no kernel
         smoothing lag - this is what the detector uses.
"""
from analyzer.metrics import read_psi_detail, psi_interval_pct


class PsiReader:
    def __init__(self, resource, root="/proc/pressure"):
        self.resource, self.root = resource, root
        self.prefix = {"cpu": "psi_cpu", "memory": "psi_mem", "io": "psi_io"}[resource]
        self._prev = None
        self.available = read_psi_detail(resource, root) is not None
        if self.available:
            self._prev = read_psi_detail(resource, root)

    def sample(self, dt):
        p = self.prefix
        out = {p: None, f"{p}_full": None, f"{p}_avg60": None, f"{p}_avg300": None,
               f"{p}_some_pct": None, f"{p}_full_pct": None, f"{p}_available": False}
        cur = read_psi_detail(self.resource, self.root)
        if cur is None:
            self._prev = None
            return out                      # unsupported / disabled / unreadable: stay null
        out[f"{p}_available"] = True
        some, full = cur["some"], cur.get("full")
        out[p] = some["avg10"]              # legacy column: 'some' avg10
        out[f"{p}_avg60"], out[f"{p}_avg300"] = some["avg60"], some["avg300"]
        out[f"{p}_some_pct"] = psi_interval_pct(self._prev["some"]["total"] if self._prev else None,
                                                some["total"], dt)
        if full is not None:
            out[f"{p}_full"] = full["avg10"]
            out[f"{p}_full_pct"] = psi_interval_pct(
                (self._prev or {}).get("full", {}).get("total"), full["total"], dt)
        self._prev = cur
        return out
