import os
import psutil
from analyzer.metrics import memory_usage, throughput, read_meminfo, read_vmstat
from .psi import PsiReader

try:
    PAGE = os.sysconf("SC_PAGE_SIZE")
except (ValueError, OSError):
    PAGE = 4096


class MemoryMonitor:
    """Memory, reclaimable cache, swap and memory PSI.

    mem_percent is MemAvailable based: (total - available) / total.  MemAvailable already
    counts reclaimable page cache and reclaimable slab as available, so a machine full of
    cache is NOT reported as full.  mem_naive_used_percent = (total - free) / total is also
    recorded purely to show how misleading 'free'-based numbers are.
    Swap rates come from /proc/vmstat pswpin/pswpout (pages); psutil is the fallback.
    """

    def __init__(self, proc_root="/proc"):
        self.root = proc_root
        self.psi = PsiReader("memory", f"{proc_root}/pressure")
        self._prev = self._counters()

    def _counters(self):
        vm = read_vmstat(f"{self.root}/vmstat")
        if "pswpin" in vm and "pswpout" in vm:
            return {"sin": vm["pswpin"] * PAGE, "sout": vm["pswpout"] * PAGE,
                    "majflt": vm.get("pgmajfault")}
        try:
            sw = psutil.swap_memory()
            return {"sin": sw.sin, "sout": sw.sout, "majflt": vm.get("pgmajfault")}
        except (OSError, RuntimeError):
            return {"sin": None, "sout": None, "majflt": None}

    @staticmethod
    def _rate(cur, prev, dt):
        if cur is None or prev is None or cur < prev:
            return None                              # unavailable or counter reset
        return throughput(cur - prev, dt)

    def sample(self, dt):
        mi = read_meminfo(f"{self.root}/meminfo")
        if mi.get("MemTotal"):
            total, free = mi["MemTotal"] * 1024, mi.get("MemFree", 0) * 1024
            cached = (mi.get("Cached", 0) + mi.get("Buffers", 0)) * 1024
            avail = mi["MemAvailable"] * 1024 if "MemAvailable" in mi else free + cached
            reclaim = (mi.get("Cached", 0) + mi.get("Buffers", 0) + mi.get("SReclaimable", 0)) * 1024
            st, sf = mi.get("SwapTotal", 0), mi.get("SwapFree", 0)
            swap_pct = (st - sf) / st * 100 if st else 0.0
        else:                                         # /proc/meminfo unreadable: psutil
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            total, free, avail = vm.total, vm.free, vm.available
            cached = reclaim = getattr(vm, "cached", 0) + getattr(vm, "buffers", 0)
            swap_pct = sw.percent
        cur = self._counters()
        out = {
            "mem_percent": memory_usage(total, avail),
            "mem_available_mb": avail / 2**20,
            "mem_cached_mb": cached / 2**20,
            "mem_reclaimable_mb": reclaim / 2**20,
            "mem_naive_used_percent": memory_usage(total, free),
            "swap_percent": swap_pct,
            "swap_in_s": self._rate(cur["sin"], self._prev["sin"], dt),     # bytes/s
            "swap_out_s": self._rate(cur["sout"], self._prev["sout"], dt),  # bytes/s
            "major_faults_s": self._rate(cur["majflt"], self._prev["majflt"], dt),
            **self.psi.sample(dt),
        }
        self._prev = cur
        return out
