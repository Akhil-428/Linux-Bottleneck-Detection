"""Evidence-based, explainable bottleneck diagnosis with persistence logic.

Pipeline for every sample (see docs/DETECTION_MODEL.md):
  1. Evaluate each *indicator* (utilisation, PSI stall, run queue, latency, ...) for the new
     sample against (a) static thresholds and (b) the historical baseline.
  2. An indicator is *present* when it holds in at least `required` of the last `window`
     samples (the original 3-of-5 persistence rule, now applied per indicator).
  3. Present indicators are grouped by class:
        stall        kernel-reported waiting (PSI)           -> tasks are actually delayed
        saturation   queues / latency / swapping             -> demand exceeds capacity
        utilization  how busy / how full                     -> NOT a problem by itself
        supporting   context switches, load, throughput ...  -> never decide, only explain
  4. State rules (identical for every resource):
        stall + (saturation or utilization)                       -> CONFIRMED
        stall alone                                               -> POTENTIAL
        PSI unavailable: saturation + utilization                 -> CONFIRMED (confidence capped)
        PSI unavailable: saturation alone                         -> POTENTIAL
        PSI available but quiet: saturation + utilization         -> POTENTIAL (contradicted)
        utilization alone (or anything else)                      -> ELEVATED
  5. Severity (magnitude) and confidence (agreement of indicators) are computed separately.
"""
from collections import deque

from .attribution import ProcessTracker, extract_processes, describe
from .diagnosis import Diagnosis, Evidence
from .metrics import is_num
from .validation import DEFAULT_THRESHOLDS, validate_thresholds, validate_window

__all__ = ["BottleneckDetector", "DEFAULT_THRESHOLDS"]

_SYS_DISK = {"I/O PSI (some)", "I/O PSI (full)", "iowait"}     # system-wide, not per device
_RANK = {"ELEVATED": 1, "POTENTIAL": 2, "CONFIRMED": 3}
_SEV_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _f(x):
    return float(x) if is_num(x) else None


def _pick(s, *keys):
    for k in keys:
        v = _f(s.get(k))
        if v is not None:
            return v
    return None


def _ge(v, th):
    return None if v is None else v >= th


def _txt(val, fmt):
    return fmt % val if val is not None else ""


def _short(detail):
    """Evidence text without thresholds / baseline / persistence suffixes (for headlines)."""
    return detail.split(" - exceeded")[0].split(" (threshold")[0].split("; baseline")[0]


def _classify(evs):
    """Apply the state rules.  Returns (state, confidence, ratio, contradictions) or None."""
    pres = lambda c: [e for e in evs if e.cls == c and e.status == "present"]
    stall_all = [e for e in evs if e.cls == "stall"]
    stall_p, sat_p, util_p = pres("stall"), pres("saturation"), pres("utilization")
    if not (stall_p or sat_p or util_p):
        return None
    stall_avail = any(e.status != "unavailable" for e in stall_all)
    if stall_p:
        state = "CONFIRMED" if (sat_p or util_p) else "POTENTIAL"
    elif not stall_avail:
        state = "CONFIRMED" if (sat_p and util_p) else ("POTENTIAL" if sat_p else "ELEVATED")
    else:
        state = "POTENTIAL" if (sat_p and util_p) else "ELEVATED"
    agree = sum(e.status == "present" for e in evs)
    avail = sum(e.status != "unavailable" for e in evs)
    contra = [e for e in evs if e.cls in ("stall", "saturation") and e.status == "absent"]
    if state == "CONFIRMED":
        conf = "HIGH" if (stall_p and agree >= 3 and not contra) else "MEDIUM"
    elif state == "POTENTIAL":
        conf = "MEDIUM" if (agree >= 3 and not contra) else "LOW"
    else:
        conf = "LOW"
    return state, conf, f"{agree}/{avail}", contra


class BottleneckDetector:
    """Feed one sample dict per interval to `update()`; it returns the active diagnoses as
    alert dicts (legacy keys resource/severity/diagnosis/recommendation + explainable fields).

    baseline: optional analyzer.baseline.Baseline.  None = static thresholds only.
    Missing keys in a sample mean "metric unavailable" and are handled gracefully.
    """

    def __init__(self, window=5, required=3, baseline=None, interval=2.0, **overrides):
        self.window, self.required = validate_window(window, required)
        self.th = validate_thresholds(overrides)
        self.baseline = baseline
        self.interval = interval
        self.history = deque(maxlen=self.window)
        self.tracker = ProcessTracker(self.window)
        self.attribution = {}
        self.recoveries = deque(maxlen=20)
        self.status = {}                # resource -> NORMAL|ELEVATED|POTENTIAL|CONFIRMED|RECOVERED
        self._episodes = {}
        self._recovered_ttl = {}
        self._n = 0
        self._ts = 0.0

    # ------------------------------------------------------------------ view
    def _view(self, s):
        t = self.th
        cores = max(int(_f(s.get("cores")) or 1), 1)
        v = {"cores": cores, "cpu": _f(s.get("cpu_percent")), "iowait": _f(s.get("iowait_percent")),
             "psi_cpu": _pick(s, "psi_cpu_some_pct", "psi_cpu"),
             "mem": _f(s.get("mem_percent")),
             "psi_mem": _pick(s, "psi_mem_some_pct", "psi_mem"),
             "psi_mem_full": _pick(s, "psi_mem_full_pct", "psi_mem_full"),
             "swap_in": _f(s.get("swap_in_s")), "swap_out": _f(s.get("swap_out_s")),
             "majflt": _f(s.get("major_faults_s")),
             "psi_io": _pick(s, "psi_io_some_pct", "psi_io"),
             "psi_io_full": _pick(s, "psi_io_full_pct", "psi_io_full")}
        load1 = _f(s.get("load1"))
        v["load_pc"] = None if load1 is None else load1 / cores
        ctx = _f(s.get("ctx_switches_s"))
        v["ctx_pc"] = None if ctx is None else ctx / cores
        wait, running = _f(s.get("runq_wait_per_core")), _f(s.get("procs_running"))
        if wait is not None:
            v["runq"], v["runq_kind"] = wait, "wait"
        elif running is not None:
            v["runq"], v["runq_kind"] = max(running - 1, 0) / cores, "runnable"
        else:
            v["runq"], v["runq_kind"] = None, None
        devs = []
        for d in s.get("devices") or []:
            rd, wr = _f(d.get("read_mbs")), _f(d.get("write_mbs"))
            devs.append({"name": d.get("name", "?"), "util": _f(d.get("util")),
                         "await": _f(d.get("await_ms")), "queue": _f(d.get("queue")),
                         "iops": _f(d.get("iops")),
                         "tput": None if rd is None and wr is None else (rd or 0) + (wr or 0)})
        if not devs and _f(s.get("disk_util")) is not None:        # legacy flat sample
            devs.append({"name": "(aggregate)", "util": _f(s.get("disk_util")),
                         "await": _f(s.get("disk_await_ms")), "queue": None, "iops": None,
                         "tput": (_f(s.get("disk_read_mbs")) or 0) + (_f(s.get("disk_write_mbs")) or 0)})
        v["devices"] = devs
        v["ctx"] = {"mem_cached_mb": _f(s.get("mem_cached_mb")),
                    "mem_available_mb": _f(s.get("mem_available_mb")),
                    "mem_reclaimable_mb": _f(s.get("mem_reclaimable_mb")),
                    "mem_naive_used_percent": _f(s.get("mem_naive_used_percent")),
                    "psi_cpu_avg300": _f(s.get("psi_cpu_avg300")),
                    "psi_mem_avg300": _f(s.get("psi_mem_avg300")),
                    "psi_io_avg300": _f(s.get("psi_io_avg300"))}
        return v

    # -------------------------------------------------------------- evaluate
    def _evaluate(self, v, bvals, bres, flags, static_keys):
        t, bl = self.th, self.baseline

        def ind(resource, static, bkey, value, text, gate=True):
            base = dev = None
            if bl is not None and bkey and value is not None:
                bvals[bkey], bres[bkey] = value, resource
                dev = bl.deviation(bkey, value)
                flags[bkey] = dev
                base = (dev["deviated"] and gate) if dev["ready"] else None
            if static and bkey:
                static_keys.add(bkey)
            if static is None and base is None:
                return None
            if dev and dev["ready"]:
                text += f"; baseline median {dev['median']:.1f}, z={dev['z']:.1f}"
            return {"static": static, "base": base, "value": value, "text": text}

        r = {}
        # CPU
        r["cpu_util"] = ind("CPU", _ge(v["cpu"], t["cpu"]), "cpu_percent", v["cpu"],
                            _txt(v["cpu"], "CPU utilisation %.0f%%") + f" (threshold {t['cpu']}%)")
        r["cpu_psi"] = ind("CPU", _ge(v["psi_cpu"], t["psi_cpu"]), "psi_cpu", v["psi_cpu"],
                           _txt(v["psi_cpu"], "CPU PSI some %.1f%%") + f" (threshold {t['psi_cpu']}%)")
        if v["runq_kind"] == "wait":
            r["cpu_runq"] = ind("CPU", _ge(v["runq"], t["runq_wait_per_core"]), "runq_wait", v["runq"],
                                _txt(v["runq"], "%.2f runnable tasks waiting per core")
                                + f" (threshold {t['runq_wait_per_core']})")
        elif v["runq_kind"] == "runnable":
            r["cpu_runq"] = ind("CPU", _ge(v["runq"], t["runnable_per_core"]), "runq_run", v["runq"],
                                _txt(v["runq"], "%.2f runnable tasks per core (procs_running)")
                                + f" (threshold {t['runnable_per_core']})")
        else:
            r["cpu_runq"] = None
        r["cpu_load"] = ind("CPU", _ge(v["load_pc"], t["load_per_core"]), None, v["load_pc"],
                            _txt(v["load_pc"], "load1/core %.2f") + f" (threshold {t['load_per_core']}; "
                            "includes uninterruptible I/O waiters)")
        r["cpu_ctx"] = ind("CPU", _ge(v["ctx_pc"], t["ctx_per_core"]), "ctx_per_core", v["ctx_pc"],
                           _txt(v["ctx_pc"], "context switches %.0f/s per core"))
        # Memory
        r["mem_util"] = ind("Memory", _ge(v["mem"], t["mem"]), "mem_percent", v["mem"],
                            _txt(v["mem"], "memory used %.0f%% (MemAvailable based)")
                            + f" (threshold {t['mem']}%)")
        r["mem_psi"] = ind("Memory", _ge(v["psi_mem"], t["psi_mem"]), "psi_mem", v["psi_mem"],
                           _txt(v["psi_mem"], "memory PSI some %.1f%%") + f" (threshold {t['psi_mem']}%)")
        r["mem_psi_full"] = ind("Memory", _ge(v["psi_mem_full"], t["psi_mem_full"]), None,
                                v["psi_mem_full"], _txt(v["psi_mem_full"], "memory PSI full %.1f%%")
                                + f" (threshold {t['psi_mem_full']}%; all non-idle tasks stalled)")
        ram_tight = v["mem"] is not None and v["mem"] >= t["mem_low"]
        swap_static = None
        if v["mem"] is not None and (v["swap_out"] is not None or v["swap_in"] is not None):
            swap_static = ram_tight and ((v["swap_out"] or 0) >= t["swap_out_bps"]
                                         or (v["swap_in"] or 0) >= t["swap_in_bps"])
        r["mem_swap"] = ind("Memory", swap_static, "swap_out_s", v["swap_out"],
                            f"swapping out {(v['swap_out'] or 0) / 2**20:.2f} MB/s, in "
                            f"{(v['swap_in'] or 0) / 2**20:.2f} MB/s while RAM {_txt(v['mem'], '%.0f')}% used"
                            f" (needs RAM >= {t['mem_low']}%)", gate=ram_tight)
        r["mem_majflt"] = ind("Memory", _ge(v["majflt"], t["major_faults_ps"]), None, v["majflt"],
                              _txt(v["majflt"], "major page faults %.0f/s"))
        # Disk
        r["io_psi"] = ind("Disk I/O", _ge(v["psi_io"], t["psi_io"]), "psi_io", v["psi_io"],
                          _txt(v["psi_io"], "I/O PSI some %.1f%%") + f" (threshold {t['psi_io']}%)")
        r["io_psi_full"] = ind("Disk I/O", _ge(v["psi_io_full"], t["psi_io_full"]), None, v["psi_io_full"],
                               _txt(v["psi_io_full"], "I/O PSI full %.1f%%")
                               + f" (threshold {t['psi_io_full']}%; all non-idle tasks stalled)")
        r["io_wait"] = ind("Disk I/O", _ge(v["iowait"], t["iowait"]), "iowait", v["iowait"],
                           _txt(v["iowait"], "iowait %.1f%% of CPU time") + f" (threshold {t['iowait']}%)")
        devs = {}
        for d in v["devices"]:
            n = d["name"]
            active = d["iops"] is None or d["iops"] > 0.5
            await_static = None if d["await"] is None else (d["await"] >= t["disk_await_ms"] and active)
            devs[n] = {
                "util": ind("Disk I/O", _ge(d["util"], t["disk_util"]), f"disk_util:{n}", d["util"],
                            _txt(d["util"], f"{n}: busy %.0f%%") + f" (threshold {t['disk_util']}%)"),
                "await": ind("Disk I/O", await_static, f"disk_await_ms:{n}", d["await"],
                             _txt(d["await"], f"{n}: avg latency %.1f ms")
                             + f" (threshold {t['disk_await_ms']} ms)"),
                "queue": ind("Disk I/O", _ge(d["queue"], t["disk_queue"]), None, d["queue"],
                             _txt(d["queue"], f"{n}: avg queue depth %.2f") + f" (threshold {t['disk_queue']})"),
                "tput": ind("Disk I/O", None, f"disk_mbs:{n}", d["tput"],
                            _txt(d["tput"], f"{n}: throughput %.1f MB/s")),
            }
        r["devs"] = devs
        return r

    # -------------------------------------------------------------- evidence
    def _evidence(self, name, cls, getter, unavailable):
        hs = hb = he = avail = 0
        last = None
        for e in self.history:
            x = getter(e["res"])
            if x is None:
                continue
            avail += 1
            last = x
            hs += bool(x["static"])
            hb += bool(x["base"])
            he += bool(x["static"] or x["base"])
        n = len(self.history)
        if avail == 0:
            return Evidence(name, cls, "unavailable", unavailable)
        present = he >= self.required
        method = None
        if present:
            method = ("both" if hs >= self.required and hb >= self.required else
                      "static" if hs >= self.required else
                      "baseline" if hb >= self.required else "both")
        return Evidence(name, cls, "present" if present else "absent",
                        f"{last['text']} - exceeded in {he}/{n} samples", method, last["value"])

    def _persistent_critical(self, pred):
        return len(self.history) == self.window and all(pred(e["view"]) for e in self.history)

    def _method(self, evs):
        ms = {e.method for e in evs if e.status == "present"}
        if "both" in ms or ("static" in ms and "baseline" in ms):
            return "static+baseline"
        return "baseline" if ms == {"baseline"} else "static"

    def _missing(self, evs):
        miss = [f"{e.name}: {e.detail}" for e in evs if e.status == "unavailable"]
        if self.baseline is not None and self.baseline.readiness() < self.baseline.min_samples:
            miss.append(f"baseline: history warming up ({self.baseline.readiness()}/"
                        f"{self.baseline.min_samples} samples); only static thresholds used")
        if self.baseline is None:
            miss.append("baseline: disabled; only static thresholds used")
        return miss

    def _finish(self, resource, evs, crit, cause_fn, rec_fn, attr, unit, device=None, headline_name=None):
        c = _classify(evs)
        if c is None:
            return None
        state, conf, ratio, contra = c
        sev = "INFO"
        if state != "ELEVATED":
            sev = "CRITICAL" if self._persistent_critical(crit) else "WARNING"
        present = [e for e in evs if e.status == "present"]
        name = headline_name or resource
        word = {"CONFIRMED": "contention confirmed", "POTENTIAL": "potential pressure",
                "ELEVATED": "high utilisation, no contention evidence"}[state]
        where = f" [{device}]" if device else ""
        headline = (f"{name}{where} {word}: " + "; ".join(_short(e.detail) for e in present[:4]))
        resp_text = describe(attr, unit) if attr else ""
        return Diagnosis(resource=resource, device=device, state=state, severity=sev, confidence=conf,
                         evidence_ratio=ratio, headline=headline, likely_cause=cause_fn(state, present),
                         recommendation=rec_fn(state, attr), method=self._method(evs), evidence=evs,
                         contradicting=[e.detail for e in contra], missing=self._missing(evs),
                         responsible=attr, responsible_text=resp_text)

    # ------------------------------------------------------------ attribution
    def _attribute(self):
        t, req = self.th, self.required
        hist = list(self.history)
        busy = [e["view"]["cpu"] * e["view"]["cores"] for e in hist if e["view"]["cpu"] is not None]
        mem = [e["view"]["mem"] for e in hist if e["view"]["mem"] is not None]
        self.attribution = {
            "cpu": self.tracker.attribute("cpu", req, t["attr_min_cpu"],
                                          sum(busy) / len(busy) if busy else None, t["proc_cpu"]),
            "memory": self.tracker.attribute("memory", req, t["attr_min_mem"],
                                             sum(mem) / len(mem) if mem else None, t["proc_mem"]),
            "io": self.tracker.attribute("io", req, t["attr_min_io_bps"]),
        }

    @staticmethod
    def _who(attr, action_one, action_many, action_none):
        v = attr["verdict"]
        if v in ("primary", "partial"):
            c = attr["contributors"][0]
            return action_one.format(name=c["name"], pid=c["pid"])
        if v == "multiple":
            names = ", ".join(f"'{c['name']}' (PID {c['pid']})" for c in attr["contributors"][:3])
            return action_many.format(names=names)
        return action_none

    # -------------------------------------------------------------- resources
    def _cpu(self):
        E = self._evidence
        evs = [E("CPU utilisation", "utilization", lambda r: r["cpu_util"], "CPU utilisation unavailable"),
               E("CPU PSI (some)", "stall", lambda r: r["cpu_psi"],
                 "/proc/pressure/cpu unavailable (kernel < 4.20 or PSI disabled)"),
               E("Run queue", "saturation", lambda r: r["cpu_runq"],
                 "run-queue data unavailable (/proc/schedstat and procs_running)"),
               E("Load average", "supporting", lambda r: r["cpu_load"], "load average unavailable"),
               E("Context switches", "supporting", lambda r: r["cpu_ctx"], "context-switch rate unavailable")]
        t = self.th
        crit = lambda v: (v["cpu"] or 0) >= t["cpu_critical"] or (v["psi_cpu"] or 0) >= t["psi_cpu_critical"]
        ctx_on = any(e.name == "Context switches" and e.status == "present" for e in evs)

        def cause(state, present):
            names = {e.name for e in present}
            if state == "ELEVATED":
                return ("CPU is busy but no task is observed waiting for it: consistent with an "
                        "intentionally CPU-bound workload. Not evidence of a bottleneck.")
            txt = ("Sustained CPU demand exceeding available processing capacity: tasks are "
                   "delayed waiting for a CPU." if "Run queue" in names and "CPU PSI (some)" in names else
                   "Tasks are delayed waiting for CPU time, but run-queue evidence does not fully "
                   "corroborate it; short bursts or CPU throttling (cgroup quota) are possible."
                   if "CPU PSI (some)" in names else
                   "More runnable tasks than CPUs (run queue), but the kernel does not report "
                   "stalls; PSI is unavailable or quiet, so the impact is uncertain.")
            if ctx_on:
                txt += (" The elevated context-switch rate supports scheduler churn from many "
                        "runnable threads (supporting evidence only).")
            return txt

        def rec(state, a):
            if state == "ELEVATED":
                busy = (" Busiest sustained process: " + describe(a, "% of a core")
                        if a["verdict"] in ("primary", "partial", "multiple") else "")
                return ("No action needed if this work is expected (e.g. a deliberate CPU-bound job). "
                        "Investigate only if users report latency; then check run-queue length and PSI."
                        + busy)
            who = self._who(a, "Investigate '{name}' (PID {pid}): profile it (perf, py-spy) or limit it "
                               "with nice/cgroups.", "Several processes contribute ({names}); reduce "
                               "their concurrency or priority.",
                            "No consistent process was identified; reduce overall concurrency, tune "
                            "priorities (nice/renice) or add CPU capacity.")
            return who
        return self._finish("CPU", evs, crit, cause, rec, self.attribution["cpu"], "% of a core",
                            headline_name="CPU")

    def _memory(self):
        E, t = self._evidence, self.th
        evs = [E("Memory utilisation", "utilization", lambda r: r["mem_util"], "memory data unavailable"),
               E("Memory PSI (some)", "stall", lambda r: r["mem_psi"],
                 "/proc/pressure/memory unavailable (kernel < 4.20 or PSI disabled)"),
               E("Memory PSI (full)", "stall", lambda r: r["mem_psi_full"],
                 "/proc/pressure/memory 'full' unavailable"),
               E("Swap activity", "saturation", lambda r: r["mem_swap"], "swap counters unavailable"),
               E("Major page faults", "supporting", lambda r: r["mem_majflt"],
                 "/proc/vmstat pgmajfault unavailable")]
        last = self.history[-1]["view"]
        crit = lambda v: ((v["mem"] or 0) >= t["mem_critical"] or (v["psi_mem"] or 0) >= t["psi_mem_critical"]
                          or (v["psi_mem_full"] or 0) >= 3 * t["psi_mem_full"])
        cx = last["ctx"]
        cache = (f" About {cx['mem_available_mb']:.0f} MB is still counted as available"
                 + (f" (page cache {cx['mem_cached_mb']:.0f} MB is reclaimable)" if cx["mem_cached_mb"] is not None else "")
                 + ".") if cx["mem_available_mb"] is not None else ""

        def cause(state, present):
            names = {e.name for e in present}
            if state == "ELEVATED":
                return ("RAM is mostly in use, but no stalls or swapping were observed: this looks "
                        "like normal cache/working-set use, not memory pressure." + cache)
            if "Swap activity" in names:
                return ("The working set appears to exceed available RAM: pages are being swapped "
                        "while RAM is nearly full (thrashing risk; each fault costs a disk access)." + cache)
            return ("Tasks are stalling in memory reclaim, though swapping was not observed; "
                    "memory is tight." + cache)

        def rec(state, a):
            if state == "ELEVATED":
                return "No action needed unless swap activity or memory PSI appear."
            c = a["contributors"][0] if a["contributors"] else None
            grow = ""
            if c and c.get("rss_growth_mb") and c["rss_growth_mb"] > 0:
                grow = f" It grew by {c['rss_growth_mb']:.0f} MB during the window (possible leak)."
            who = self._who(a, "Largest sustained holder: '{name}' (PID {pid})." + grow +
                            " Check for leaks or reduce its working set; otherwise add RAM.",
                            "Several processes hold much memory ({names}); reduce their footprint or add RAM.",
                            "No single process dominates memory; reduce total demand, tune caches or add RAM.")
            return who
        return self._finish("Memory", evs, crit, cause, rec, self.attribution["memory"], "% of RAM")

    def _disk(self):
        E, t = self._evidence, self.th
        last = self.history[-1]
        sysev = [E("I/O PSI (some)", "stall", lambda r: r["io_psi"],
                   "/proc/pressure/io unavailable (kernel < 4.20 or PSI disabled)"),
                 E("I/O PSI (full)", "stall", lambda r: r["io_psi_full"], "/proc/pressure/io 'full' unavailable"),
                 E("iowait", "saturation", lambda r: r["io_wait"], "iowait unavailable")]
        crit = lambda v: (any((d["util"] or 0) >= t["disk_util_critical"] for d in v["devices"])
                          or (v["psi_io"] or 0) >= t["psi_io_critical"]
                          or (v["psi_io_full"] or 0) >= 3 * t["psi_io_full"])
        out = []
        names = list(last["res"]["devs"])
        per_dev = {}
        for n in names:
            g = lambda k, n=n: (lambda r: r["devs"].get(n, {}).get(k))
            per_dev[n] = [E(f"{n} utilisation", "utilization", g("util"), f"{n}: utilisation unavailable"),
                          E(f"{n} latency", "saturation", g("await"), f"{n}: latency unavailable"),
                          E(f"{n} queue depth", "saturation", g("queue"), f"{n}: queue depth unavailable"),
                          E(f"{n} throughput", "supporting", g("tput"), f"{n}: throughput unavailable")]
        has_dev = [n for n in names if any(e.status == "present" and e.cls != "supporting" for e in per_dev[n])]

        def cause_factory(dev_evidence):
            def cause(state, present):
                nm = {e.cls for e in present}
                dev_sat = any(e.cls in ("saturation", "utilization") and e.name not in _SYS_DISK
                              for e in present)
                if state == "ELEVATED":
                    return ("The device is busy but tasks are not stalled and latency is normal: heavy "
                            "but healthy I/O (high utilisation or throughput alone is not a problem; "
                            "on SSD/NVMe %util can reach 100% without saturation).")
                if "stall" in nm and dev_sat:
                    return ("Device-level saturation with tasks actually waiting on I/O: demand exceeds "
                            "what this device can serve.")
                if "stall" in nm:
                    return ("Tasks are waiting on I/O but no single device shows saturation: "
                            "application-level I/O waiting (synchronous writes/fsync, network or overlay "
                            "filesystem, cgroup I/O throttling, or a device not visible here).")
                return ("The device is slow or queued, but the kernel reports no task stalls: likely "
                        "background I/O (writeback) not yet hurting foreground work.")
            return cause

        def rec(state, a):
            if state == "ELEVATED":
                return "No action needed unless I/O PSI or latency rise."
            return self._who(a, "Process '{name}' (PID {pid}) generates the most I/O (per-process counters; "
                                "write attribution is approximate because of page-cache writeback). Batch or "
                                "cache its writes, or move it to faster storage.",
                             "Several processes generate I/O ({names}); stagger them or use faster storage.",
                             a["note"] or "No consistent I/O-heavy process identified; use iotop/pidstat -d "
                                          "for a closer look or move the workload to faster storage.")
        if has_dev:
            for n in has_dev:
                d = self._finish("Disk I/O", per_dev[n] + sysev, crit, cause_factory(None), rec,
                                 self.attribution["io"], "B/s", device=n)
                if d:
                    out.append(d)
        else:
            util_of = {d["name"]: d["util"] or 0 for d in last["view"]["devices"]}
            busiest = max(names, key=lambda n: util_of.get(n, 0), default=None)
            ctx_evs = per_dev[busiest] if busiest else []
            d = self._finish("Disk I/O", ctx_evs + sysev, crit, cause_factory(None), rec,
                             self.attribution["io"], "B/s", device=None if len(names) != 1 else busiest)
            if d:
                out.append(d)
        return out

    def _process(self):
        t, out = self.th, []
        for c in self.tracker.heavy("cpu", t["proc_cpu"], self.required):
            out.append(Diagnosis(
                resource="Process", device=f"cpu:{c['pid']}", state="ELEVATED", severity="INFO",
                confidence="LOW", evidence_ratio=f"{c['presence']}/{c['window']}",
                headline=f"CPU-intensive process: '{c['name']}' (PID {c['pid']}) at "
                         f"{c['mean_when_present']:.0f}% of a core in {c['presence']}/{c['window']} samples",
                likely_cause="A single process is consistently busy. That is a statement about the process, "
                             "not proof of system-wide contention.",
                recommendation="Verify the process is expected; profile it or limit it with nice/cgroups."))
        for c in self.tracker.heavy("memory", t["proc_mem"], self.required):
            out.append(Diagnosis(
                resource="Process", device=f"mem:{c['pid']}", state="ELEVATED", severity="INFO",
                confidence="LOW", evidence_ratio=f"{c['presence']}/{c['window']}",
                headline=f"Memory-heavy process: '{c['name']}' (PID {c['pid']}) holds "
                         f"{c['mean_when_present']:.0f}% of RAM in {c['presence']}/{c['window']} samples",
                likely_cause="A single process holds a large share of RAM; not by itself memory pressure.",
                recommendation="Check for a leak or oversized cache; restart or cap with cgroups if needed."))
        return out

    # ----------------------------------------------------------------- update
    def update(self, s):
        """Feed one sample dict; returns the list of currently active alerts (dicts)."""
        self._n += 1
        ts = _f(s.get("ts"))
        ts = ts if ts is not None else self._n * self.interval
        self._ts = ts
        bvals, bres, flags, static_keys = {}, {}, {}, set()
        v = self._view(s)
        res = self._evaluate(v, bvals, bres, flags, static_keys)
        hit = lambda *ks: any(res[k] and (res[k]["static"] or res[k]["base"]) for k in ks)
        flag = {"CPU": hit("cpu_util", "cpu_psi", "cpu_runq"),
                "Memory": hit("mem_util", "mem_psi", "mem_psi_full", "mem_swap"),
                "Disk I/O": hit("io_psi", "io_psi_full", "io_wait") or any(
                    x and (x["static"] or x["base"]) for d in res["devs"].values()
                    for x in (d["util"], d["await"], d["queue"]))}
        self.history.append({"ts": ts, "view": v, "res": res, "flag": flag})
        self.tracker.update(extract_processes(s))
        self._attribute()
        diags = []
        for fn in (self._cpu, self._memory, self._disk, self._process):
            got = fn()
            diags += got if isinstance(got, list) else ([got] if got else [])
        self._track(diags, ts)
        if self.baseline is not None:
            active = {d.resource for d in diags if d.state in ("POTENTIAL", "CONFIRMED")}
            hold = {k for k, rsc in bres.items() if rsc in active} | static_keys
            self.baseline.update(bvals, hold=hold, flags=flags)
        return [d.to_alert() for d in diags]

    # -------------------------------------------------------- episodes/recovery
    def _track(self, diags, ts):
        live = {}
        for d in diags:
            key = d.key
            ep = self._episodes.get(key)
            if ep is None:
                first = next((e["ts"] for e in self.history if e["flag"].get(d.resource, True)), ts)
                ep = self._episodes[key] = {"first": first, "peak": d.state}
            if _RANK[d.state] > _RANK[ep["peak"]]:
                ep["peak"] = d.state
            d.duration_s = round(ts - ep["first"], 1)
            live[key] = ep
        for key in [k for k in self._episodes if k not in live]:
            ep = self._episodes.pop(key)
            if _RANK[ep["peak"]] >= _RANK["POTENTIAL"]:
                self.recoveries.append({"resource": key[0], "device": key[1], "peak_state": ep["peak"],
                                        "duration_s": round(ts - ep["first"], 1), "ended_ts": ts})
                self._recovered_ttl[key[0]] = self.window
        status = {r: "NORMAL" for r in ("CPU", "Memory", "Disk I/O")}
        for d in diags:
            if d.resource in status and _RANK[d.state] > _RANK.get(status[d.resource], 0):
                status[d.resource] = d.state
        for r in list(self._recovered_ttl):
            if status.get(r) == "NORMAL" and self._recovered_ttl[r] > 0:
                status[r] = "RECOVERED"
                self._recovered_ttl[r] -= 1
            elif status.get(r) != "NORMAL":
                self._recovered_ttl.pop(r)
        self.status = status
