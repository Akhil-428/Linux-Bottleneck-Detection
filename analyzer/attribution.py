"""Process attribution across the persistence window.

Problems solved (Issue 3):
  * Identity   a process is (pid, start_time).  A recycled PID has a different start
               time, so it is a *different* process and never inherits history.
               If start_time is unknown the identity is weak: (pid, name).
  * Persistence  a process is only named if it contributed in at least `required`
               samples of the window - the same rule used for detecting the alert.
  * Rankings   if the top process keeps changing we say so instead of blaming the
               last one.
  * Honesty    the result is *correlation*: consistent with, not proof of, causation.
Missing from the sample list means "below the collector's top-N cut" (or exited);
it is counted as zero contribution, which is conservative.
"""
from collections import Counter, deque

PRIMARY_SHARE = 0.5      # one process explains >= half of the measured load
CONTRIB_SHARE = 0.15     # minimum share to be listed as a contributor

_BASIS = ("Correlation, not proof: these processes were observed consuming the resource "
          "during the detection window; the kernel metrics alone do not prove they caused "
          "the pressure.")


def extract_processes(s):
    """Process list from a sample.  Falls back to the legacy top_cpu_*/top_mem_* fields
    (weak identity, no start time) so older sample dicts keep working."""
    procs = s.get("processes")
    if procs:
        return procs
    out = []
    if s.get("top_cpu_name") is not None:
        out.append({"pid": s.get("top_cpu_pid", 0), "start_time": None, "name": s["top_cpu_name"],
                    "cpu_pct": s.get("top_cpu_pct", 0) or 0})
    if s.get("top_mem_name") is not None:
        pid = s.get("top_mem_pid", 0)
        for p in out:                       # same process listed twice -> merge
            if p["pid"] == pid and p["name"] == s["top_mem_name"]:
                p["mem_pct"] = s.get("top_mem_pct", 0) or 0
                break
        else:
            out.append({"pid": pid, "start_time": None, "name": s["top_mem_name"],
                        "mem_pct": s.get("top_mem_pct", 0) or 0})
    return out


def _identity(p):
    st = p.get("start_time")
    return (p["pid"], st) if st is not None else (p["pid"], None, p.get("name"))


class ProcessTracker:
    def __init__(self, window=5):
        self.window = window
        self.samples = deque(maxlen=window)      # list of {identity: process dict}
        self.names = {}
        self._pid_start = {}
        self.pid_reuse_events = 0

    def update(self, processes):
        snap = {}
        for p in processes or []:
            try:
                ident = _identity(p)
            except KeyError:
                continue
            st = p.get("start_time")
            if st is not None:
                prev = self._pid_start.get(p["pid"])
                if prev is not None and prev != st:
                    self.pid_reuse_events += 1   # same PID, new process
                self._pid_start[p["pid"]] = st
            snap[ident] = p
            self.names[ident] = p.get("name") or "?"
        self.samples.append(snap)
        if len(self._pid_start) > 20000:         # bound memory on long runs
            self._pid_start.clear()

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def _value(p, resource):
        if resource == "cpu":
            return p.get("cpu_pct") or 0.0
        if resource == "memory":
            return p.get("mem_pct") or 0.0
        r, w = p.get("read_bps"), p.get("write_bps")
        return None if r is None and w is None else (r or 0.0) + (w or 0.0)

    def top_changes(self, resource):
        """Distinct top-ranked identities per sample, and how often the leader changed."""
        leaders = []
        for snap in self.samples:
            best = max(snap.items(), key=lambda kv: self._value(kv[1], resource) or 0.0,
                       default=None)
            if best and (self._value(best[1], resource) or 0) > 0:
                leaders.append(best[0])
        changes = sum(1 for a, b in zip(leaders, leaders[1:]) if a != b)
        return leaders, changes

    def heavy(self, resource, threshold, required):
        """Identities whose value was >= threshold in at least `required` window samples
        (a *sustained* heavy process, not merely whoever was on top in the last sample)."""
        n = len(self.samples)
        hits = {}
        for snap in self.samples:
            for ident, p in snap.items():
                v = self._value(p, resource) or 0.0
                if v >= threshold:
                    h = hits.setdefault(ident, [0, 0.0])
                    h[0] += 1
                    h[1] += v
        out = [{"pid": i[0], "name": self.names.get(i, "?"), "presence": h[0], "window": n,
                "mean_when_present": round(h[1] / h[0], 1), "weak_identity": i[1] is None}
               for i, h in hits.items() if h[0] >= required]
        return sorted(out, key=lambda c: c["mean_when_present"], reverse=True)

    def attribute(self, resource, required, min_value, system_total=None, primary_abs=None):
        """resource: 'cpu' | 'memory' | 'io'.

        system_total: mean system-wide load in the same unit (e.g. busy % of one core
        summed over cores) used for share; None -> share unknown.
        Returns a dict with verdict in {primary, partial, multiple, none, unavailable}.
        """
        n = len(self.samples)
        res = {"resource": resource, "verdict": "unavailable", "contributors": [], "window": n,
               "leader_changes": 0, "distinct_leaders": 0, "weak_identity": False,
               "pid_reuse_events": self.pid_reuse_events, "basis": _BASIS, "note": ""}
        if n == 0:
            res["note"] = "No process data collected yet."
            return res
        if resource == "io" and not any(self._value(p, "io") is not None
                                        for snap in self.samples for p in snap.values()):
            res["note"] = ("Per-process I/O counters are not readable (needs root or "
                           "same-user processes); I/O attribution is unavailable.")
            return res
        need = required            # same strict persistence rule as detection (no relaxed start-up)
        stats = {}
        for snap in self.samples:
            for ident, p in snap.items():
                v = self._value(p, resource) or 0.0
                st = stats.setdefault(ident, {"vals": [], "seen": 0, "hit": 0, "rss": []})
                st["vals"].append(v)
                st["seen"] += 1
                if v >= min_value:
                    st["hit"] += 1
                if p.get("rss_mb") is not None:
                    st["rss"].append(p["rss_mb"])
        means = {i: sum(st["vals"]) / n for i, st in stats.items()}   # absent samples = 0
        tracked_total = sum(means.values())
        total = system_total if system_total and system_total > 0 else (
            tracked_total if resource == "io" and tracked_total > 0 else None)
        contribs = []
        for ident, st in stats.items():
            if st["hit"] < need:
                continue
            share = means[ident] / total if total else None
            contribs.append({"pid": ident[0], "name": self.names.get(ident, "?"),
                             "start_time": ident[1], "presence": st["hit"], "window": n,
                             "mean": round(means[ident], 2),
                             "share": None if share is None else round(min(share, 1.0), 2),
                             "weak_identity": ident[1] is None,
                             "rss_growth_mb": (round(st["rss"][-1] - st["rss"][0], 1)
                                               if len(st["rss"]) >= 2 else None)})
        contribs.sort(key=lambda c: c["mean"], reverse=True)
        keep = [c for c in contribs if c["share"] is None or c["share"] >= CONTRIB_SHARE]
        leaders, changes = self.top_changes(resource)
        res.update(contributors=keep, leader_changes=changes, distinct_leaders=len(set(leaders)),
                   weak_identity=any(c["weak_identity"] for c in keep))
        if len(keep) == 1:
            c = keep[0]
            strong = (c["share"] >= PRIMARY_SHARE) if c["share"] is not None else (
                primary_abs is not None and c["mean"] >= primary_abs)
            res["verdict"] = "primary" if strong else "partial"
        elif len(keep) >= 2:
            res["verdict"] = "multiple"
        else:
            res["verdict"] = "none"
            names = Counter(self.names.get(i, "?") for i in set(leaders))
            res["note"] = (f"No single process was consistently responsible: the top process "
                           f"changed {changes} time(s) across {n} samples "
                           f"({', '.join(sorted(names)) or 'none'}).")
        return res


def _human(v, unit):
    if unit == "B/s":
        for div, suf in ((2**30, "GB/s"), (2**20, "MB/s"), (2**10, "KB/s")):
            if v >= div:
                return f"{v / div:.1f} {suf}"
        return f"{v:.0f} B/s"
    return f"{v:.0f}{unit}"


def describe(attr, unit):
    """One-line human text for an attribution dict (used in explanations)."""
    v = attr["verdict"]
    if v in ("unavailable", "none"):
        return attr["note"]
    parts = []
    for c in attr["contributors"][:3]:
        sh = "" if c["share"] is None else f", ~{c['share'] * 100:.0f}% of measured load"
        parts.append(f"'{c['name']}' (PID {c['pid']}) {c['mean']:.0f}{unit} avg, "
                     f"present in {c['presence']}/{c['window']} samples{sh}")
    lead = {"primary": "Dominant contributor", "partial": "Consistent contributor (explains only part of the load)",
            "multiple": "Multiple contributors"}[v]
    weak = " Identity is weak (no process start time available)." if attr["weak_identity"] else ""
    return f"{lead}: " + "; ".join(parts) + "." + weak
