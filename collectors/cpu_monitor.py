import os
from analyzer.metrics import (read_proc_stat, cpu_usage, iowait_usage, throughput,
                              read_proc_stat_extra, read_schedstat)
from .psi import PsiReader


class CpuMonitor:
    """CPU utilisation, iowait, load, context switches, run-queue evidence and CPU PSI.

    Run-queue evidence (best source first):
      * /proc/schedstat: cumulative ns tasks spent waiting for a CPU -> average number of
        runnable-but-waiting tasks per core over the interval (exact; needs CONFIG_SCHEDSTATS)
      * /proc/stat procs_running: instantaneous runnable count (noisy; always available)
    """

    def __init__(self, proc_root="/proc"):
        self.root = proc_root
        self.cores = os.cpu_count() or 1
        self._stat = f"{proc_root}/stat"
        self._sched = f"{proc_root}/schedstat"
        self._prev = read_proc_stat(self._stat)
        self._prev_ctx = read_proc_stat_extra(self._stat)["ctxt"]
        self._prev_sched = read_schedstat(self._sched)
        self.psi = PsiReader("cpu", f"{proc_root}/pressure")
        self.schedstat_available = self._prev_sched is not None

    def _loadavg(self):
        try:
            with open(f"{self.root}/loadavg") as f:
                a = f.read().split()
            return float(a[0]), float(a[1]), float(a[2])
        except (OSError, ValueError, IndexError):
            try:
                return os.getloadavg()
            except OSError:
                return None, None, None

    def sample(self, dt):
        cur = read_proc_stat(self._stat)
        extra = read_proc_stat_extra(self._stat)
        sched = read_schedstat(self._sched)
        load1, load5, load15 = self._loadavg()
        ctx_rate = None
        if extra["ctxt"] is not None and self._prev_ctx is not None and extra["ctxt"] >= self._prev_ctx:
            ctx_rate = throughput(extra["ctxt"] - self._prev_ctx, dt)
        wait_pc = runnable_pc = None
        if sched and self._prev_sched and dt > 0 and sched[1] >= self._prev_sched[1]:
            wait_pc = (sched[1] - self._prev_sched[1]) / (dt * 1e9) / self.cores
            runnable_pc = ((sched[0] - self._prev_sched[0]) + (sched[1] - self._prev_sched[1])) / (dt * 1e9) / self.cores
        out = {
            "cpu_percent": cpu_usage(self._prev, cur),
            "iowait_percent": iowait_usage(self._prev, cur),
            "load1": load1, "load5": load5, "load15": load15,
            "cores": self.cores,
            "ctx_switches_s": ctx_rate,
            "procs_running": extra["procs_running"], "procs_blocked": extra["procs_blocked"],
            "runq_wait_per_core": wait_pc,          # None when /proc/schedstat is unsupported
            "runnable_per_core": runnable_pc,
            "runq_source": "schedstat" if wait_pc is not None else
                           ("procs_running" if extra["procs_running"] is not None else None),
            **self.psi.sample(dt),
        }
        self._prev, self._prev_ctx, self._prev_sched = cur, extra["ctxt"], sched
        return out
