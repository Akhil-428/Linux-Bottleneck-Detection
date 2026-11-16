import psutil


class ProcessMonitor:
    """Per-process CPU, memory and I/O with a stable identity (pid + start time).

    cpu_percent is 'top-style': 100 = one full core.  I/O rates come from
    /proc/<pid>/io (psutil io_counters): read_bytes = bytes fetched from the storage layer,
    write_bytes = bytes the process caused to be written (page-cache writeback timing is
    decoupled, so write attribution is approximate).  Reading another user's /proc/<pid>/io
    needs root; such processes report None ("unavailable"), never 0.
    """

    def __init__(self, top_n=5, extra_n=10):
        self.top_n, self.extra_n = top_n, extra_n
        self._io_prev = {}
        self._t_prev = None
        self._known = set()
        self._cores = psutil.cpu_count() or 1
        for p in psutil.process_iter():          # prime cpu_percent counters
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass

    def sample_all(self, now=None):
        import time
        now = time.monotonic() if now is None else now
        dt = None if self._t_prev is None else now - self._t_prev
        rows, io_cur = [], {}
        wall = time.time()
        known_now = set()
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent",
                                      "create_time", "memory_info", "cpu_times"]):
            i = p.info
            if i["pid"] == 0:
                continue
            ident = (i["pid"], i["create_time"])
            known_now.add(ident)
            cpu = i["cpu_percent"] or 0.0
            if self._t_prev is not None and ident not in self._known and i.get("cpu_times"):
                # psutil returns 0.0 on a process's first reading.  A process that appeared since the
                # last sample is young, so its lifetime-average CPU is a fair first estimate.
                ct = i["cpu_times"]
                life = max(wall - (i["create_time"] or wall), 0.05)
                cpu = min((ct.user + ct.system) / life * 100.0, 100.0 * self._cores)
            r = w = None
            try:
                io = p.io_counters()
                io_cur[ident] = (io.read_bytes, io.write_bytes)
                prev = self._io_prev.get(ident)            # same pid AND start time only
                if prev and dt and dt > 0 and io.read_bytes >= prev[0] and io.write_bytes >= prev[1]:
                    r, w = (io.read_bytes - prev[0]) / dt, (io.write_bytes - prev[1]) / dt
            except (psutil.Error, AttributeError, OSError):
                pass
            mi = i.get("memory_info")
            rows.append({"pid": i["pid"], "start_time": i["create_time"], "name": i["name"] or "?",
                         "cpu_pct": round(cpu, 1),
                         "mem_pct": round(i["memory_percent"] or 0.0, 2),
                         "rss_mb": round(mi.rss / 2**20, 1) if mi else None,
                         "read_bps": r, "write_bps": w})
        self._io_prev, self._t_prev, self._known = io_cur, now, known_now
        by = lambda key: sorted(rows, key=key, reverse=True)
        io_key = lambda x: (x["read_bps"] or 0) + (x["write_bps"] or 0)
        top_cpu = by(lambda x: x["cpu_pct"])[:self.top_n]
        top_mem = by(lambda x: x["mem_pct"])[:self.top_n]
        top_io = [x for x in by(io_key)[:self.top_n] if io_key(x) > 0]
        # candidates handed to attribution: union of the leaders of each resource
        keep = {}
        for lst in (by(lambda x: x["cpu_pct"])[:self.extra_n], by(lambda x: x["mem_pct"])[:self.extra_n],
                    by(io_key)[:self.extra_n]):
            for x in lst:
                keep[(x["pid"], x["start_time"])] = x
        return {"processes": list(keep.values()), "top_cpu": top_cpu, "top_mem": top_mem, "top_io": top_io}

    def sample(self):
        """Backward-compatible: (top_cpu, top_mem) lists of dicts with legacy keys."""
        a = self.sample_all()
        conv = lambda L: [{**x, "cpu_percent": x["cpu_pct"], "mem_percent": x["mem_pct"]} for x in L]
        return conv(a["top_cpu"]), conv(a["top_mem"])
