import os
import re
from analyzer.metrics import parse_diskstats
from .psi import PsiReader

SECTOR = 512                       # /proc/diskstats sectors are always 512 bytes
_SKIP = re.compile(r"^(loop|ram|zram|fd|sr|nbd)\d*")
_PART = re.compile(r"^(?:(?:sd|vd|xvd|hd)[a-z]+\d+|nvme\d+n\d+p\d+|mmcblk\d+p\d+)$")


def device_rates(prev, cur, dt):
    """Per-device rates from two /proc/diskstats snapshots.  Returns None if dt <= 0 or any
    counter went backwards (device reset / wrap), so a bogus interval is never reported.

    util  = d(io_ticks)/dt          fraction of time the device had I/O in flight (clamped 0-100)
    await = d(io time)/d(ios)       average latency per completed I/O (ms)
    queue = d(weighted_ms)/dt       average queue depth (iostat aqu-sz)
    """
    if dt <= 0:
        return None
    d = {k: cur[k] - prev[k] for k in cur if k != "in_flight"}
    if any(v < 0 for v in d.values()):
        return None
    ops = d["reads"] + d["writes"]
    ms = dt * 1000.0
    return {
        "read_mbs": d["rd_sectors"] * SECTOR / dt / 2**20,
        "write_mbs": d["wr_sectors"] * SECTOR / dt / 2**20,
        "iops": ops / dt,
        "util": max(0.0, min(100.0, d["io_ticks_ms"] / ms * 100)),
        "await_ms": (d["rd_ms"] + d["wr_ms"]) / ops if ops else 0.0,
        "read_await_ms": d["rd_ms"] / d["reads"] if d["reads"] else 0.0,
        "write_await_ms": d["wr_ms"] / d["writes"] if d["writes"] else 0.0,
        "queue": d["weighted_ms"] / ms,
        "in_flight": cur["in_flight"],
        "_ops": ops, "_ms": d["rd_ms"] + d["wr_ms"],
    }


class DiskMonitor:
    """Per-device disk statistics from /proc/diskstats (Issues 4 and 8).

    Devices: whole block devices only (partitions, loop, ram, zram, optical are skipped).
    Aggregates exposed for the CSV / dashboard (documented, never summed utilisation):
      disk_util      MAX utilisation over devices   -> 'the busiest device', always <= 100
      disk_util_mean mean utilisation over devices
      disk_read_mbs / disk_write_mbs   sum over *top-layer* devices (no holders), so a
                     device-mapper volume and the disk beneath it are not double counted
      disk_await_ms  I/O-weighted mean latency over top-layer devices
    """

    def __init__(self, proc_root="/proc", sys_block="/sys/block"):
        self.root, self.sys_block = proc_root, sys_block
        self.psi = PsiReader("io", f"{proc_root}/pressure")
        self._prev = self._read()

    def _read(self):
        try:
            with open(f"{self.root}/diskstats") as f:
                stats = parse_diskstats(f.read())
        except OSError:
            return None
        try:
            whole = set(os.listdir(self.sys_block))
        except OSError:
            whole = None
        sel = {}
        for name, v in stats.items():
            if _SKIP.match(name):
                continue
            if whole is not None:
                if name not in whole:
                    continue
            elif _PART.match(name):
                continue
            sel[name] = v
        return sel

    def _has_holders(self, name):
        try:
            return bool(os.listdir(f"{self.sys_block}/{name}/holders"))
        except OSError:
            return False

    def sample(self, dt):
        cur = self._read()
        psi = self.psi.sample(dt)
        out = {"disk_read_mbs": None, "disk_write_mbs": None, "disk_util": None,
               "disk_util_mean": None, "disk_await_ms": None, "disk_queue": None,
               "devices": [], **psi}
        if cur is None or self._prev is None:
            self._prev = cur
            return out
        devices = []
        for name, c in cur.items():
            p = self._prev.get(name)
            r = device_rates(p, c, dt) if p else None
            if r is None:
                continue
            r["name"], r["top_layer"] = name, not self._has_holders(name)
            devices.append(r)
        self._prev = cur
        if not devices:
            return out
        top = [d for d in devices if d["top_layer"]] or devices
        ops = sum(d["_ops"] for d in top)
        out.update(
            disk_read_mbs=sum(d["read_mbs"] for d in top),
            disk_write_mbs=sum(d["write_mbs"] for d in top),
            disk_util=max(d["util"] for d in devices),
            disk_util_mean=sum(d["util"] for d in devices) / len(devices),
            disk_await_ms=(sum(d["_ms"] for d in top) / ops) if ops else 0.0,
            disk_queue=max(d["queue"] for d in devices),
            devices=[{k: v for k, v in d.items() if not k.startswith("_")} for d in devices])
        return out
