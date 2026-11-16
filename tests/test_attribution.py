import unittest

from helpers import by_resource, feed, proc, sample
from analyzer.attribution import ProcessTracker, extract_processes, describe
from analyzer.bottleneck_detector import BottleneckDetector


def run(procs_per_sample, window=5, resource="cpu", required=3, total=100.0, min_value=20):
    t = ProcessTracker(window)
    for ps in procs_per_sample:
        t.update(ps)
    return t, t.attribute(resource, required, min_value, system_total=total, primary_abs=80)


class Attribution(unittest.TestCase):
    def test_steady_culprit_is_primary(self):
        t, a = run([[proc(10, "hog", cpu=95), proc(11, "x", cpu=3)]] * 5)
        self.assertEqual(a["verdict"], "primary")
        self.assertEqual(a["contributors"][0]["pid"], 10)
        self.assertIn("Correlation", a["basis"])

    def test_changing_top_process_is_not_blamed(self):
        seq = [[proc(i, n, cpu=90)] for i, n in enumerate("ABCDE", 1)]
        t, a = run(seq)
        self.assertEqual(a["verdict"], "none")
        self.assertEqual(a["leader_changes"], 4)
        self.assertIn("No single process was consistently responsible", a["note"])

    def test_two_steady_processes_are_reported_as_multiple(self):
        t, a = run([[proc(1, "a", cpu=50), proc(2, "b", cpu=45)]] * 5)
        self.assertEqual(a["verdict"], "multiple")
        self.assertEqual({c["pid"] for c in a["contributors"]}, {1, 2})

    def test_short_lived_burst_below_persistence_is_ignored(self):
        t, a = run([[proc(1, "burst", cpu=99)], [proc(1, "burst", cpu=99)], [], [], []])
        self.assertEqual(a["verdict"], "none")

    def test_pid_reuse_does_not_inherit_history(self):
        t = ProcessTracker(5)
        for _ in range(3):
            t.update([proc(10, "oldproc", cpu=95, start=111.0)])
        for _ in range(2):                                    # same PID, different start time
            t.update([proc(10, "newproc", cpu=95, start=999.0)])
        a = t.attribute("cpu", 3, 20, system_total=100)
        names = {c["name"]: c["presence"] for c in a["contributors"]}
        self.assertEqual(names, {"oldproc": 3})               # newproc only 2/5 -> not yet persistent
        self.assertEqual(t.pid_reuse_events, 1)

    def test_pid_reuse_new_process_alone_gets_credit_after_persisting(self):
        t = ProcessTracker(5)
        for _ in range(2):
            t.update([proc(10, "oldproc", cpu=95, start=1.0)])
        for _ in range(3):
            t.update([proc(10, "newproc", cpu=95, start=2.0)])
        a = t.attribute("cpu", 3, 20, system_total=100)
        self.assertEqual([c["name"] for c in a["contributors"]], ["newproc"])

    def test_process_termination_removes_it_from_attribution(self):
        t = ProcessTracker(5)
        for _ in range(5):
            t.update([proc(7, "dying", cpu=90)])
        for _ in range(3):
            t.update([])
        a = t.attribute("cpu", 3, 20, system_total=100)
        self.assertNotEqual(a["verdict"], "primary")

    def test_missing_start_time_is_flagged_weak_identity(self):
        t, a = run([[proc(5, "legacy", cpu=90, start=None)]] * 5)
        self.assertTrue(a["weak_identity"])
        self.assertIn("weak", describe(a, "%").lower())

    def test_memory_attribution_reports_growth(self):
        t = ProcessTracker(5)
        for i in range(5):
            t.update([proc(3, "leaky", mem=30 + i, rss=1000 + 100 * i)])
        a = t.attribute("memory", 3, 5, system_total=40, primary_abs=30)
        self.assertEqual(a["verdict"], "primary")
        self.assertEqual(a["contributors"][0]["rss_growth_mb"], 400.0)

    def test_io_attribution_unavailable_is_reported_not_invented(self):
        t = ProcessTracker(5)
        for _ in range(5):
            t.update([proc(1, "a", cpu=50, rbps=None, wbps=None)])
        a = t.attribute("io", 3, 1e6)
        self.assertEqual(a["verdict"], "unavailable")
        self.assertIn("not readable", a["note"])

    def test_io_attribution_names_heavy_writer(self):
        t = ProcessTracker(5)
        for _ in range(5):
            t.update([proc(1, "writer", wbps=80e6, rbps=0.0), proc(2, "quiet", wbps=1e3, rbps=0.0)])
        a = t.attribute("io", 3, 1e6)
        self.assertEqual(a["verdict"], "primary")
        self.assertEqual(a["contributors"][0]["name"], "writer")

    def test_legacy_top_fields_are_converted(self):
        procs = extract_processes({"top_cpu_name": "x", "top_cpu_pid": 4, "top_cpu_pct": 80,
                                   "top_mem_name": "x", "top_mem_pid": 4, "top_mem_pct": 10})
        self.assertEqual(len(procs), 1)
        self.assertEqual((procs[0]["cpu_pct"], procs[0]["mem_pct"]), (80, 10))


class AttributionInDiagnosis(unittest.TestCase):
    HOG = dict(cpu_percent=98.0, psi_cpu_some_pct=45.0, procs_running=9, load1=7.0)

    def test_culprit_named_only_when_persistent(self):
        d = BottleneckDetector()
        al = feed(d, 5, processes=[proc(42, "stress-ng", cpu=380.0)], **self.HOG)
        a = by_resource(al, "CPU")
        self.assertEqual(a["responsible"]["verdict"], "primary")
        self.assertIn("stress-ng", a["recommendation"])

    def test_rotating_top_processes_yield_honest_non_attribution(self):
        d = BottleneckDetector()
        for i, name in enumerate(["A", "B", "C", "D", "E"]):
            al = d.update(sample(ts=1000 + 2 * i, processes=[proc(i + 1, name, cpu=95.0)], **self.HOG))
        a = by_resource(al, "CPU")
        self.assertEqual(a["responsible"]["verdict"], "none")
        self.assertIn("No consistent process", a["recommendation"])

    def test_sustained_heavy_process_alert_needs_persistence(self):
        d = BottleneckDetector()
        al = feed(d, 2, processes=[proc(9, "busy", cpu=99.0)])
        self.assertIsNone(by_resource(al, "Process"))
        al = feed(d, 2, processes=[proc(9, "busy", cpu=99.0)])
        self.assertIsNotNone(by_resource(al, "Process"))

    def test_memory_diagnosis_flags_possible_leak(self):
        d = BottleneckDetector()
        for i in range(5):
            al = d.update(sample(ts=1000 + 2 * i, mem_percent=96.0, psi_mem_some_pct=30.0, psi_mem_full_pct=10.0,
                                 swap_out_s=5e6, processes=[proc(8, "leaker", mem=60.0 + i, rss=2000 + 200 * i)]))
        self.assertIn("possible leak", by_resource(al, "Memory")["recommendation"])


if __name__ == "__main__":
    unittest.main()
