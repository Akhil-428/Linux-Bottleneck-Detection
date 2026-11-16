"""MonitorEngine: collectors + baseline + detector in one object.

Used by main.py (continuous monitoring) AND by the controlled experiments, so an
experiment exercises exactly the same detection path as normal monitoring - nothing is
special-cased or forced because an experiment is running.
"""
import logging
import time
from datetime import datetime

from analyzer.baseline import Baseline
from analyzer.bottleneck_detector import BottleneckDetector
from analyzer.validation import (validate_baseline, validate_interval, validate_thresholds,
                                 validate_window)
from collectors.cpu_monitor import CpuMonitor
from collectors.disk_monitor import DiskMonitor
from collectors.memory_monitor import MemoryMonitor
from collectors.process_monitor import ProcessMonitor

log = logging.getLogger("bottleneck.engine")


class MonitorEngine:
    def __init__(self, interval=2.0, window=5, required=3, thresholds=None, baseline=None,
                 baseline_path=None, collectors=None):
        self.interval = validate_interval(interval)
        validate_window(window, required)
        validate_thresholds(thresholds)
        bcfg = validate_baseline(baseline)
        self.baseline_path = baseline_path or bcfg["path"]
        self.baseline = None
        if bcfg["enabled"]:
            self.baseline = Baseline(bcfg["window"], bcfg["min_samples"], bcfg["z"], bcfg["adopt_after"])
            if self.baseline_path and self.baseline.load(self.baseline_path):
                log.info("loaded baseline from %s", self.baseline_path)
        self.detector = BottleneckDetector(window, required, baseline=self.baseline,
                                           interval=self.interval, **(thresholds or {}))
        c = collectors or {}
        self.cpu = c.get("cpu") or CpuMonitor()
        self.mem = c.get("mem") or MemoryMonitor()
        self.disk = c.get("disk") or DiskMonitor()
        self.procs = c.get("procs") or ProcessMonitor()
        self._steps = 0

    @staticmethod
    def _safe(name, fn, *a):
        """A failing kernel interface must degrade to 'metric unavailable', never crash."""
        try:
            return fn(*a)
        except Exception as e:                       # noqa: BLE001 - deliberate: keep monitoring
            log.warning("%s collector failed (%s: %s); its metrics are unavailable this sample",
                        name, type(e).__name__, e)
            return {}

    def step(self, dt):
        """Collect one sample over the last `dt` seconds and run detection.
        Returns (sample, alerts, process_info)."""
        now = time.time()
        procs = self._safe("process", self.procs.sample_all) or {}
        s = {"ts": now, "timestamp": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
             "dt": dt, **self._safe("cpu", self.cpu.sample, dt), **self._safe("memory", self.mem.sample, dt),
             **self._safe("disk", self.disk.sample, dt),
             "processes": procs.get("processes", [])}
        top_cpu, top_mem = procs.get("top_cpu") or [], procs.get("top_mem") or []
        for pfx, lst, key in (("top_cpu", top_cpu, "cpu_pct"), ("top_mem", top_mem, "mem_pct")):
            t = lst[0] if lst else {"name": "?", "pid": 0, key: 0.0}
            s[f"{pfx}_name"], s[f"{pfx}_pid"], s[f"{pfx}_pct"] = t["name"], t["pid"], t[key]
        alerts = self.detector.update(s)
        self._steps += 1
        if self.baseline is not None and self.baseline_path and self._steps % 30 == 0:
            self.save_baseline()
        return s, alerts, procs

    def save_baseline(self):
        if self.baseline is not None and self.baseline_path:
            try:
                self.baseline.save(self.baseline_path)
            except OSError as e:
                log.warning("could not save baseline to %s: %s", self.baseline_path, e)
