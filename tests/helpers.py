"""Shared builders for deterministic, machine-independent tests (no real /proc access)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CALM = dict(cpu_percent=10.0, iowait_percent=0.0, load1=0.3, cores=4, ctx_switches_s=4000.0,
            procs_running=1, psi_cpu_some_pct=0.2, psi_mem_some_pct=0.0, psi_mem_full_pct=0.0,
            psi_io_some_pct=0.1, psi_io_full_pct=0.0, mem_percent=40.0, mem_available_mb=4000.0,
            mem_cached_mb=800.0, swap_in_s=0.0, swap_out_s=0.0, major_faults_s=2.0,
            devices=[{"name": "sda", "util": 5.0, "await_ms": 1.0, "queue": 0.05, "iops": 30.0,
                      "read_mbs": 1.0, "write_mbs": 1.0}])


def proc(pid, name, cpu=0.0, mem=1.0, start=1000.0, rss=100.0, rbps=None, wbps=None):
    return {"pid": pid, "name": name, "start_time": start, "cpu_pct": cpu, "mem_pct": mem,
            "rss_mb": rss, "read_bps": rbps, "write_bps": wbps}


def dev(name="sda", util=5.0, await_ms=1.0, queue=0.05, iops=30.0, read_mbs=1.0, write_mbs=1.0):
    return {"name": name, "util": util, "await_ms": await_ms, "queue": queue, "iops": iops,
            "read_mbs": read_mbs, "write_mbs": write_mbs}


def sample(**kw):
    s = dict(CALM)
    s["devices"] = [dict(d) for d in CALM["devices"]]
    s["processes"] = [proc(500, "idle-proc", cpu=2.0)]
    s.update(kw)
    return s


def feed(det, n, start_ts=1000.0, step=2.0, **kw):
    """Feed n identical samples with consecutive timestamps; return the last alerts."""
    out = []
    t0 = getattr(det, "_test_t", start_ts)
    for i in range(n):
        out = det.update(sample(ts=t0 + i * step, **kw))
    det._test_t = t0 + n * step
    return out


def by_resource(alerts, resource, device=None):
    for a in alerts:
        if a["resource"] == resource and (device is None or a["device"] == device):
            return a
    return None


def ev(alert, name_part):
    return next(e for e in alert["evidence"] if name_part.lower() in e["name"].lower())


def read_text(path):
    with open(path) as f:
        return f.read()


def read_csv(path):
    import csv
    with open(path, newline="") as f:
        return list(csv.reader(f))
