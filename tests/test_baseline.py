import json
import os
import random
import tempfile
import unittest

from helpers import by_resource, dev, ev, feed, sample
from analyzer.baseline import Baseline
from analyzer.bottleneck_detector import BottleneckDetector


def trained(n=40, **kw):
    b = Baseline(window=100, min_samples=20, z=4.0, adopt_after=kw.pop("adopt_after", 0))
    rnd = random.Random(7)
    for _ in range(n):
        b.update({"psi_io": rnd.uniform(0, 1.5), "cpu_percent": rnd.uniform(5, 15), "disk_await_ms:sda": rnd.uniform(0.5, 2)})
    return b


class BaselineStats(unittest.TestCase):
    def test_insufficient_history_never_reports_deviation(self):
        b = Baseline(min_samples=20)
        for _ in range(10):
            b.update({"psi_io": 0.5})
        r = b.deviation("psi_io", 90.0)
        self.assertFalse(r["ready"])
        self.assertFalse(r["deviated"])
        self.assertEqual(r["n"], 10)

    def test_unknown_key_and_bad_values_are_safe(self):
        b = trained()
        self.assertFalse(b.deviation("nope", 5)["ready"])
        self.assertFalse(b.deviation("psi_io", float("nan"))["ready"])
        b.update({"psi_io": float("nan"), "x": None})          # must not raise or be learned
        self.assertNotIn("x", b.values)

    def test_normal_value_not_deviant_spike_is_(self):
        b = trained()
        self.assertFalse(b.deviation("psi_io", 1.2)["deviated"])
        self.assertTrue(b.deviation("psi_io", 40.0)["deviated"])

    def test_only_upward_deviation_counts(self):
        b = trained()
        self.assertFalse(b.deviation("cpu_percent", 0.0)["deviated"])

    def test_absolute_floor_prevents_meaningless_deviation(self):
        b = Baseline(min_samples=10)
        for _ in range(30):
            b.update({"runq_run": 0.0})
        self.assertFalse(b.deviation("runq_run", 1.0)["deviated"])      # all cores busy != contention
        self.assertTrue(b.deviation("runq_run", 3.0)["deviated"])

    def test_flat_history_uses_scale_floor_not_infinite_z(self):
        b = Baseline(min_samples=10)
        for _ in range(30):
            b.update({"psi_io": 0.0})
        r = b.deviation("psi_io", 2.0)
        self.assertTrue(r["ready"])
        self.assertLess(r["z"], 10)


class BaselineAdaptation(unittest.TestCase):
    def test_abnormal_values_are_not_learned(self):
        b = trained()
        before = b.stats("psi_io")[0]
        for _ in range(50):
            b.update({"psi_io": 60.0})
        self.assertAlmostEqual(b.stats("psi_io")[0], before, places=6)

    def test_held_keys_are_not_learned_even_if_not_deviant(self):
        b = trained()
        n = len(b.values["psi_io"])
        b.update({"psi_io": 1.0}, hold={"psi_io"})
        self.assertEqual(len(b.values["psi_io"]), n)

    def test_persistent_shift_without_contention_is_adopted_eventually(self):
        b = trained(adopt_after=15)
        for _ in range(120):
            b.update({"psi_io": 60.0})
        self.assertGreater(b.stats("psi_io")[0], 30.0)

    def test_shift_with_contention_is_never_adopted(self):
        b = trained(adopt_after=15)
        for _ in range(120):
            b.update({"psi_io": 60.0}, hold={"psi_io"})
        self.assertLess(b.stats("psi_io")[0], 3.0)

    def test_frozen_baseline_learns_nothing(self):
        b = trained()
        b.frozen = True
        n = len(b.values["psi_io"])
        b.update({"psi_io": 1.0})
        self.assertEqual(len(b.values["psi_io"]), n)

    def test_window_is_rolling(self):
        b = Baseline(window=10, min_samples=5)
        for i in range(100):
            b.update({"psi_io": 0.5})
        self.assertEqual(len(b.values["psi_io"]), 10)


class BaselinePersistence(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            b = trained()
            b.save(p)
            with open(p) as f:
                json.load(f)                                    # valid JSON
            b2 = Baseline(window=100)
            self.assertTrue(b2.load(p))
            self.assertEqual(len(b2.values["psi_io"]), len(b.values["psi_io"]))

    def test_corrupt_or_missing_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertFalse(Baseline().load(p))
            self.assertFalse(Baseline().load(os.path.join(d, "missing.json")))
            with open(p, "w") as f:
                f.write('{"values": {"a": "oops"}}')       # wrong shape -> nothing usable
            b = Baseline()
            self.assertFalse(b.load(p))
            self.assertEqual(b.values, {})


class DetectorWithBaseline(unittest.TestCase):
    def _warm(self, det, n=25):
        feed(det, n)

    def test_baseline_catches_machine_specific_anomaly_below_static_threshold(self):
        # Static I/O PSI threshold is 20%; this machine normally sits near 0.1%.
        det = BottleneckDetector(baseline=Baseline(min_samples=20, window=200, adopt_after=0))
        self._warm(det)
        d = [dev("sda", util=85.0, await_ms=25.0, queue=1.0, iops=400)]   # below static await/queue thresholds
        a = by_resource(feed(det, 5, devices=d, psi_io_some_pct=12.0), "Disk I/O", "sda")
        self.assertIn(a["method"], ("baseline", "static+baseline"))
        self.assertEqual(ev(a, "I/O PSI (some)")["status"], "present")

    def test_static_only_misses_that_same_anomaly(self):
        det = BottleneckDetector()
        self._warm(det)
        d = [dev("sda", util=70.0, await_ms=10.0, queue=1.0, iops=400)]
        self.assertIsNone(by_resource(feed(det, 5, devices=d, psi_io_some_pct=12.0), "Disk I/O"))

    def test_method_is_reported_as_static_when_only_static_fires(self):
        det = BottleneckDetector(baseline=Baseline(min_samples=500))      # never ready
        a = by_resource(feed(det, 5, cpu_percent=98.0, psi_cpu_some_pct=45.0, procs_running=9), "CPU")
        self.assertEqual(a["method"], "static")
        self.assertTrue(any("warming up" in m for m in a["missing"]))

    def test_both_method_when_static_and_baseline_agree(self):
        det = BottleneckDetector(baseline=Baseline(min_samples=20, adopt_after=0))
        self._warm(det)
        a = by_resource(feed(det, 5, cpu_percent=98.0, psi_cpu_some_pct=60.0, procs_running=9), "CPU")
        self.assertEqual(a["method"], "static+baseline")

    def test_busy_but_uncontended_cpu_is_not_flagged_by_baseline(self):
        det = BottleneckDetector(baseline=Baseline(min_samples=20, adopt_after=0))
        self._warm(det)
        a = by_resource(feed(det, 8, cpu_percent=100.0, procs_running=5, cores=4), "CPU")   # 4 hogs on 4 cores
        self.assertEqual(a["state"], "ELEVATED")

    def test_temporary_spike_does_not_become_baseline_or_alert(self):
        b = Baseline(min_samples=20, adopt_after=0)
        det = BottleneckDetector(baseline=b)
        self._warm(det)
        feed(det, 2, cpu_percent=99.0, psi_cpu_some_pct=70.0, procs_running=9)
        feed(det, 3)
        self.assertLess(b.stats("cpu_percent")[0], 20)

    def test_stress_is_not_learned_during_a_long_incident(self):
        b = Baseline(min_samples=20, adopt_after=0)
        det = BottleneckDetector(baseline=b)
        self._warm(det)
        feed(det, 60, cpu_percent=99.0, psi_cpu_some_pct=70.0, procs_running=9)
        self.assertLess(b.stats("cpu_percent")[0], 20)
        self.assertLess(b.stats("psi_cpu")[0], 3)


if __name__ == "__main__":
    unittest.main()
