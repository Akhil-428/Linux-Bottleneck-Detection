import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyzer.bottleneck_detector import BottleneckDetector
from analyzer import metrics


def sample(**kw):
    base = dict(cpu_percent=10, iowait_percent=0, load1=0.3, cores=4, mem_percent=40,
                mem_available_mb=4000, swap_percent=0, swap_out_s=0, disk_util=5,
                disk_await_ms=1, psi_io=float("nan"), top_cpu_name="bash", top_cpu_pid=1,
                top_cpu_pct=2, top_mem_name="firefox", top_mem_pid=2, top_mem_pct=5)
    base.update(kw)
    return base


def feed(det, n, **kw):
    out = []
    for _ in range(n):
        out = det.update(sample(**kw))
    return out


def resources(alerts):
    return {a["resource"] for a in alerts}


class MetricsTest(unittest.TestCase):
    def test_cpu_formula(self):
        self.assertAlmostEqual(metrics.cpu_usage((1000, 800, 0), (1100, 820, 0)), 80.0)

    def test_memory_formula(self):
        self.assertAlmostEqual(metrics.memory_usage(1000, 100), 90.0)

    def test_throughput(self):
        self.assertEqual(metrics.throughput(100, 2), 50)
        self.assertEqual(metrics.throughput(100, 0), 0.0)

    def test_psi_parse(self):
        txt = "some avg10=1.50 avg60=0.50 avg300=0.10 total=123\nfull avg10=0.75 avg60=0 avg300=0 total=9"
        self.assertEqual(metrics.parse_psi(txt), {"some": 1.5, "full": 0.75})


class DetectorTest(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(feed(BottleneckDetector(), 6), [])

    def test_single_spike_ignored(self):
        d = BottleneckDetector()
        feed(d, 3)
        d.update(sample(cpu_percent=99))
        self.assertEqual(feed(d, 2), [])

    # V2: the two CPU tests below originally asserted that utilisation alone gives a
    # WARNING/CRITICAL alert.  That is exactly the "high utilisation != bottleneck" flaw
    # (Issue 1), so they now assert the corrected behaviour; the culprit is still named.
    def test_sustained_cpu_with_culprit(self):
        a = feed(BottleneckDetector(), 3, cpu_percent=90, top_cpu_pct=88, top_cpu_name="stress-ng")
        self.assertIn("CPU", resources(a))
        cpu = next(x for x in a if x["resource"] == "CPU")
        self.assertIn("stress-ng", cpu["recommendation"])
        self.assertEqual(cpu["state"], "ELEVATED")          # busy, but nothing is waiting
        self.assertEqual(cpu["severity"], "INFO")

    def test_cpu_critical_needs_full_window(self):
        a = feed(BottleneckDetector(), 5, cpu_percent=99, psi_cpu=60.0)
        self.assertEqual(next(x for x in a if x["resource"] == "CPU")["severity"], "CRITICAL")
        a = feed(BottleneckDetector(), 4, cpu_percent=99, psi_cpu=60.0)
        self.assertEqual(next(x for x in a if x["resource"] == "CPU")["severity"], "WARNING")

    def test_memory(self):
        self.assertIn("Memory", resources(feed(BottleneckDetector(), 3, mem_percent=93)))

    # V2: swap is now evidence inside the Memory diagnosis (Issue 9), not a separate resource.
    def test_swap_requires_low_ram(self):
        self.assertEqual(feed(BottleneckDetector(), 4, swap_out_s=5e6, mem_percent=40), [])
        a = feed(BottleneckDetector(), 4, swap_out_s=5e6, mem_percent=88)
        mem = next(x for x in a if x["resource"] == "Memory")
        self.assertEqual(mem["state"], "POTENTIAL")

    def test_disk_by_util_and_psi(self):
        self.assertIn("Disk I/O", resources(feed(BottleneckDetector(), 3, disk_util=90)))
        self.assertIn("Disk I/O", resources(feed(BottleneckDetector(), 3, psi_io=35.0)))

    def test_alert_clears_after_recovery(self):
        d = BottleneckDetector()
        feed(d, 4, cpu_percent=95)
        self.assertEqual(feed(d, 4), [])


if __name__ == "__main__":
    unittest.main()
