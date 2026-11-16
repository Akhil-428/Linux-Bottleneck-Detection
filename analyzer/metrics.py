"""Pure calculation helpers (no psutil dependency, easy to unit-test)."""


def read_proc_stat(path="/proc/stat"):
    """Return (total, idle, iowait) jiffies from the aggregate 'cpu' line.

    Fields: user nice system idle iowait irq softirq steal guest guest_nice.
    guest time is already included in user/nice, so only the first 8 are summed.
    idle time here = idle + iowait (a CPU waiting on I/O is not doing work).
    """
    with open(path) as f:
        v = [int(x) for x in f.readline().split()[1:]]
    v += [0] * (8 - len(v))
    return sum(v[:8]), v[3] + v[4], v[4]


def cpu_usage(prev, cur):
    """CPU Usage = ((dTotal - dIdle) / dTotal) * 100"""
    d_total, d_idle = cur[0] - prev[0], cur[1] - prev[1]
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (d_total - d_idle) / d_total * 100))


def iowait_usage(prev, cur):
    d_total = cur[0] - prev[0]
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, (cur[2] - prev[2]) / d_total * 100))


def memory_usage(total, available):
    """Memory Usage = ((Total - Available) / Total) * 100"""
    return 0.0 if total <= 0 else (total - available) / total * 100


def throughput(delta, dt):
    """Throughput = change in transferred data / time interval"""
    return delta / dt if dt > 0 else 0.0


def parse_psi(text):
    """Parse /proc/pressure/* text -> {'some': avg10, 'full': avg10}."""
    out = {}
    for line in text.strip().splitlines():
        kind, *fields = line.split()
        kv = dict(f.split("=") for f in fields)
        out[kind] = float(kv["avg10"])
    return out


def read_psi(resource):
    """Return PSI dict for 'cpu' | 'memory' | 'io', or None if unsupported."""
    try:
        with open(f"/proc/pressure/{resource}") as f:
            return parse_psi(f.read())
    except (OSError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# V2 additions: safe numbers / JSON, detailed PSI, /proc parsers.
# Everything above is the original module, unchanged (backward compatible).
# ---------------------------------------------------------------------------
import json as _json
import math as _math

NAN = float("nan")


def is_num(x):
    """True for a real, finite number (False for None, NaN, inf, bool, str)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and _math.isfinite(x)


def safe_number(x):
    """Finite float/int or None.  NaN/inf are *unavailable*, never serialised."""
    return x if is_num(x) else None


def sanitize(obj):
    """Recursively replace NaN/inf by None so the result is strict JSON."""
    if isinstance(obj, float):
        return obj if _math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]
    return obj


def dumps_json(obj, **kw):
    """Strict JSON (RFC 8259).  Unavailable metrics are written as null."""
    return _json.dumps(sanitize(obj), allow_nan=False, **kw)


def csv_value(x):
    """CSV cell: unavailable (None/NaN/inf) -> empty string."""
    if x is None or (isinstance(x, float) and not _math.isfinite(x)):
        return ""
    return x


def parse_psi_detail(text):
    """Parse /proc/pressure/* into
    {'some': {'avg10','avg60','avg300','total'}, 'full': {...}}.

    avg10/60/300 are the kernel's exponentially-weighted stall percentages;
    total is the cumulative stall time in microseconds since boot.
    Raises ValueError on malformed input.
    """
    out = {}
    try:
        for line in text.strip().splitlines():
            kind, *fields = line.split()
            kv = dict(f.split("=", 1) for f in fields)
            out[kind] = {"avg10": float(kv["avg10"]), "avg60": float(kv["avg60"]),
                         "avg300": float(kv["avg300"]), "total": int(kv["total"])}
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"malformed PSI line: {e!r}")
    if "some" not in out:
        raise ValueError("PSI text has no 'some' line")
    return out


def read_psi_detail(resource, root="/proc/pressure"):
    """Detailed PSI dict, or None when the interface is missing/disabled/unreadable.

    Missing: kernel < 4.20 or CONFIG_PSI=n.  Present-but-unreadable: booted with
    psi=0 (EOPNOTSUPP), container without access, or malformed content.
    """
    try:
        with open(f"{root}/{resource}") as f:
            return parse_psi_detail(f.read())
    except (OSError, ValueError):
        return None


def psi_interval_pct(prev_total_us, cur_total_us, dt):
    """Stall percentage over *our* sampling interval from cumulative totals.

    Exact for the interval (no kernel smoothing lag).  None if a counter went
    backwards (reset) or dt is not positive.
    """
    if prev_total_us is None or cur_total_us is None or dt <= 0 or cur_total_us < prev_total_us:
        return None
    return min(100.0, (cur_total_us - prev_total_us) / (dt * 1e6) * 100.0)


def read_proc_stat_extra(path="/proc/stat"):
    """Return {'procs_running', 'procs_blocked', 'ctxt'} from /proc/stat (None if absent).

    procs_running is the instantaneous number of runnable tasks and includes the
    monitoring process itself, so callers subtract 1.
    """
    out = {"procs_running": None, "procs_blocked": None, "ctxt": None}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.partition(" ")
                if k in out:
                    out[k] = int(v)
    except (OSError, ValueError):
        pass
    return out


def read_schedstat(path="/proc/schedstat"):
    """Return (run_ns, wait_ns) summed over CPUs, or None if unsupported.

    Needs CONFIG_SCHEDSTATS (and kernel.sched_schedstats=1 on newer kernels).
    cpuN line fields 7/8 (after the name) are cumulative ns running / waiting.
    """
    try:
        run = wait = 0
        seen = False
        with open(path) as f:
            for line in f:
                if line.startswith("cpu"):
                    p = line.split()
                    run += int(p[7])
                    wait += int(p[8])
                    seen = True
        return (run, wait) if seen else None
    except (OSError, ValueError, IndexError):
        return None


def read_meminfo(path="/proc/meminfo"):
    """Return /proc/meminfo as {key: kB}.  Empty dict if unreadable."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    out[k] = int(parts[0])
    except (OSError, ValueError):
        return {}
    return out


def read_vmstat(path="/proc/vmstat"):
    """Return /proc/vmstat counters as {name: int}.  Empty dict if unreadable."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.partition(" ")
                out[k] = int(v)
    except (OSError, ValueError):
        return {}
    return out


def parse_diskstats(text):
    """Parse /proc/diskstats -> {device: {reads, rd_ms, rd_sectors, writes, wr_ms,
    wr_sectors, in_flight, io_ticks_ms, weighted_ms}}.  Sectors are always 512 B."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 14:                       # pre-2.6 formats are not supported
            continue
        try:
            out[p[2]] = {"reads": int(p[3]), "rd_sectors": int(p[5]), "rd_ms": int(p[6]),
                         "writes": int(p[7]), "wr_sectors": int(p[9]), "wr_ms": int(p[10]),
                         "in_flight": int(p[11]), "io_ticks_ms": int(p[12]),
                         "weighted_ms": int(p[13])}
        except ValueError:
            continue
    return out
