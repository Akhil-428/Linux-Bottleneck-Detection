import math
import unittest

from helpers import by_resource, dev, ev, feed, proc, sample
from analyzer.bottleneck_detector import BottleneckDetector

CPU_HOG = dict(cpu_percent=98.0, psi_cpu_some_pct=45.0, procs_running=9, load1=7.0)


class NormalAndSpikes(unittest.TestCase):
    def test_normal_system_produces_no_alerts(self):
        self.assertEqual(feed(BottleneckDetector(), 20), [])

    def test_temporary_spike_is_ignored(self):
        d = BottleneckDetector()
        feed(d, 3)
        feed(d, 2, **CPU_HOG)                       # 2 hot samples < 3 required
        self.assertEqual([a for a in feed(d, 1) if a["resource"] == "CPU"], [])

    def test_persistence_is_configurable(self):
        d = BottleneckDetector(window=4, required=2)
        self.assertIsNotNone(by_resource(feed(d, 2, **CPU_HOG), "CPU"))


class CpuDiagnosis(unittest.TestCase):
    def test_sustained_cpu_pressure_is_confirmed_with_high_confidence(self):
        a = by_resource(feed(BottleneckDetector(), 5, **CPU_HOG), "CPU")
        self.assertEqual((a["state"], a["confidence"], a["severity"]), ("CONFIRMED", "HIGH", "CRITICAL"))
        self.assertEqual(ev(a, "PSI")["status"], "present")
        self.assertEqual(ev(a, "Run queue")["status"], "present")
        self.assertEqual(a["method"], "static")

    def test_high_utilisation_alone_is_not_a_bottleneck(self):
        # 100% busy but PSI quiet and no run queue: intentionally CPU-bound work (Issue 1)
        a = by_resource(feed(BottleneckDetector(), 6, cpu_percent=100.0, procs_running=4, cores=4,
                             load1=1.0), "CPU")
        self.assertEqual(a["state"], "ELEVATED")
        self.assertEqual(a["severity"], "INFO")
        self.assertIn("Not evidence of a bottleneck", a["likely_cause"])

    def test_psi_without_utilisation_is_potential_not_confirmed(self):
        a = by_resource(feed(BottleneckDetector(), 4, cpu_percent=30.0, psi_cpu_some_pct=25.0), "CPU")
        self.assertEqual(a["state"], "POTENTIAL")

    def test_missing_psi_caps_confidence_and_says_so(self):
        d = BottleneckDetector()
        a = by_resource(feed(d, 5, cpu_percent=98.0, procs_running=9, load1=7.0, psi_cpu_some_pct=None,
                             psi_cpu=None), "CPU")
        self.assertEqual(a["state"], "CONFIRMED")
        self.assertEqual(a["confidence"], "MEDIUM")
        self.assertEqual(ev(a, "PSI")["status"], "unavailable")
        self.assertTrue(any("pressure/cpu" in m for m in a["missing"]))

    def test_context_switches_are_supporting_only(self):
        a = by_resource(feed(BottleneckDetector(), 5, ctx_switches_s=400000.0), "CPU")
        self.assertIsNone(a)                         # alone: no alert at all
        a = by_resource(feed(BottleneckDetector(), 5, ctx_switches_s=400000.0, **CPU_HOG), "CPU")
        self.assertEqual(ev(a, "Context")["status"], "present")
        self.assertIn("context-switch", a["likely_cause"])

    def test_schedstat_runqueue_preferred_over_procs_running(self):
        a = by_resource(feed(BottleneckDetector(), 5, cpu_percent=99.0, psi_cpu_some_pct=30.0,
                             runq_wait_per_core=1.5, procs_running=1), "CPU")
        self.assertIn("waiting per core", ev(a, "Run queue")["detail"])

    def test_severity_and_confidence_are_independent(self):
        # critical magnitude but only one corroborating indicator -> CRITICAL severity, LOW/MEDIUM confidence
        a = by_resource(feed(BottleneckDetector(), 5, cpu_percent=30.0, psi_cpu_some_pct=60.0), "CPU")
        self.assertEqual(a["severity"], "CRITICAL")
        self.assertEqual(a["confidence"], "LOW")


class MemoryDiagnosis(unittest.TestCase):
    def test_high_ram_use_alone_is_not_memory_pressure(self):
        a = by_resource(feed(BottleneckDetector(), 6, mem_percent=93.0, mem_available_mb=280.0,
                             mem_cached_mb=2500.0), "Memory")
        self.assertEqual(a["state"], "ELEVATED")
        self.assertIn("not memory pressure", a["likely_cause"])
        self.assertIn("reclaimable", a["likely_cause"])

    def test_sustained_memory_pressure_with_swap_and_psi(self):
        a = by_resource(feed(BottleneckDetector(), 5, mem_percent=96.0, swap_in_s=3e6, swap_out_s=8e6,
                             psi_mem_some_pct=35.0, psi_mem_full_pct=12.0, major_faults_s=900.0), "Memory")
        self.assertEqual((a["state"], a["confidence"]), ("CONFIRMED", "HIGH"))
        self.assertEqual(ev(a, "Swap")["status"], "present")
        self.assertIn("swapped", a["likely_cause"])

    def test_memory_psi_full_distinguished_from_some(self):
        a = by_resource(feed(BottleneckDetector(), 4, mem_percent=92.0, psi_mem_some_pct=3.0,
                             psi_mem_full_pct=6.0), "Memory")
        self.assertEqual(ev(a, "(full)")["status"], "present")
        self.assertEqual(ev(a, "(some)")["status"], "absent")
        self.assertEqual(a["state"], "CONFIRMED")

    def test_swap_at_low_ram_use_is_ignored(self):
        self.assertIsNone(by_resource(feed(BottleneckDetector(), 5, mem_percent=35.0, swap_out_s=9e6), "Memory"))

    def test_swap_in_activity_counts(self):
        a = by_resource(feed(BottleneckDetector(), 4, mem_percent=88.0, swap_in_s=2e6), "Memory")
        self.assertEqual(ev(a, "Swap")["status"], "present")


class DiskDiagnosis(unittest.TestCase):
    SAT = dict(devices=[dev("sda", util=99.0, await_ms=80.0, queue=9.0, iops=500, read_mbs=40, write_mbs=60)],
               psi_io_some_pct=55.0, psi_io_full_pct=30.0)

    def test_device_saturation_confirmed(self):
        a = by_resource(feed(BottleneckDetector(), 5, **self.SAT), "Disk I/O", "sda")
        self.assertEqual((a["state"], a["severity"]), ("CONFIRMED", "CRITICAL"))
        self.assertIn("Device-level saturation", a["likely_cause"])

    def test_high_throughput_alone_is_not_a_problem(self):
        d = [dev("nvme0n1", util=20.0, await_ms=0.2, read_mbs=1500, write_mbs=1500)]
        self.assertIsNone(by_resource(feed(BottleneckDetector(), 8, devices=d), "Disk I/O"))

    def test_busy_device_without_stall_is_only_elevated(self):
        d = [dev("sda", util=97.0, await_ms=2.0, queue=0.5, iops=900)]
        a = by_resource(feed(BottleneckDetector(), 5, devices=d), "Disk I/O")
        self.assertEqual(a["state"], "ELEVATED")
        self.assertIn("not a problem", a["likely_cause"])

    def test_application_level_io_wait_is_distinguished_from_device_saturation(self):
        a = by_resource(feed(BottleneckDetector(), 5, psi_io_some_pct=40.0, iowait_percent=35.0), "Disk I/O")
        self.assertEqual(a["state"], "CONFIRMED")          # PSI stall + iowait, but no device saturated
        self.assertEqual(ev(a, "sda latency")["status"], "absent")
        self.assertIn("application-level", a["likely_cause"])
        self.assertNotIn("Device-level saturation", a["likely_cause"])

    def test_per_device_results_do_not_blame_the_idle_device(self):
        devs = [dev("sda", util=99.0, await_ms=90.0, queue=8.0), dev("sdb", util=2.0, await_ms=0.5)]
        al = feed(BottleneckDetector(), 5, devices=devs, psi_io_some_pct=60.0)
        self.assertIsNotNone(by_resource(al, "Disk I/O", "sda"))
        self.assertIsNone(by_resource(al, "Disk I/O", "sdb"))

    def test_two_busy_devices_get_two_diagnoses(self):
        devs = [dev("sda", util=99.0, await_ms=90.0, queue=8.0), dev("sdb", util=98.0, await_ms=70.0, queue=5.0)]
        al = feed(BottleneckDetector(), 5, devices=devs, psi_io_some_pct=60.0)
        self.assertEqual({a["device"] for a in al if a["resource"] == "Disk I/O"}, {"sda", "sdb"})

    def test_legacy_flat_disk_fields_still_work(self):
        d = BottleneckDetector()
        for i in range(4):
            al = d.update({"cpu_percent": 5, "cores": 4, "disk_util": 92, "disk_await_ms": 3})
        self.assertEqual(by_resource(al, "Disk I/O")["state"], "ELEVATED")


class MissingAndInvalidMetrics(unittest.TestCase):
    def test_empty_sample_does_not_crash(self):
        d = BottleneckDetector()
        for _ in range(6):
            self.assertEqual(d.update({}), [])

    def test_nan_inf_and_none_are_unavailable_not_errors(self):
        d = BottleneckDetector()
        for _ in range(6):
            al = d.update(sample(cpu_percent=math.nan, psi_cpu_some_pct=math.inf, mem_percent=None,
                                 psi_io_some_pct=-math.inf, devices=[dev(util=math.nan, await_ms=None)]))
        self.assertEqual(al, [])

    def test_strings_and_bools_are_rejected_as_values(self):
        d = BottleneckDetector()
        for _ in range(6):
            al = d.update(sample(cpu_percent="98", psi_cpu_some_pct=True))
        self.assertIsNone(by_resource(al, "CPU"))

    def test_alert_dicts_are_strict_json(self):
        from analyzer.metrics import dumps_json
        import json
        al = feed(BottleneckDetector(), 5, **CPU_HOG, swap_in_s=math.nan)
        json.loads(dumps_json(al), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


class RecoveryAndDuration(unittest.TestCase):
    def test_alert_clears_and_recovery_event_is_recorded(self):
        d = BottleneckDetector()
        feed(d, 5, **CPU_HOG)
        self.assertEqual(d.status["CPU"], "CONFIRMED")
        al = feed(d, 5)
        self.assertIsNone(by_resource(al, "CPU"))
        self.assertEqual(len(d.recoveries), 1)
        r = d.recoveries[0]
        self.assertEqual((r["resource"], r["peak_state"]), ("CPU", "CONFIRMED"))
        self.assertGreater(r["duration_s"], 0)

    def test_status_passes_through_recovered_then_normal(self):
        d = BottleneckDetector()
        feed(d, 5, **CPU_HOG)
        seen = []
        for _ in range(12):
            feed(d, 1)
            seen.append(d.status["CPU"])
        self.assertIn("RECOVERED", seen)
        self.assertEqual(seen[-1], "NORMAL")

    def test_duration_grows_while_condition_persists(self):
        d = BottleneckDetector()
        first = by_resource(feed(d, 3, **CPU_HOG), "CPU")["duration_s"]
        later = by_resource(feed(d, 5, **CPU_HOG), "CPU")["duration_s"]
        self.assertGreater(later, first)

    def test_elevated_only_period_is_not_reported_as_recovered_bottleneck(self):
        d = BottleneckDetector()
        feed(d, 5, cpu_percent=100.0)
        feed(d, 6)
        self.assertEqual(len(d.recoveries), 0)


if __name__ == "__main__":
    unittest.main()
