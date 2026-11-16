import json
import os
import shutil
import tempfile
import time
import unittest

from helpers import by_resource, feed, sample          # noqa: F401
from analyzer.bottleneck_detector import BottleneckDetector
from dashboard import data as D


def latest_from(alerts, status=None):
    return {"active_alerts": alerts, "resource_status": status or {}}


class Banner(unittest.TestCase):
    def alerts(self, **kw):
        return feed(BottleneckDetector(), 5, **kw)

    def test_healthy(self):
        self.assertEqual(D.overall_banner(latest_from(self.alerts()))[0], "healthy")

    def test_utilisation_alone_never_raises_a_warning(self):
        a = self.alerts(cpu_percent=100.0, procs_running=4, cores=4)
        self.assertEqual(D.overall_banner(latest_from(a))[0], "info")

    def test_confirmed_critical(self):
        a = self.alerts(cpu_percent=99.0, psi_cpu_some_pct=60.0, procs_running=9, load1=8.0)
        lvl, text = D.overall_banner(latest_from(a))
        self.assertEqual(lvl, "critical")
        self.assertIn("CPU", text)

    def test_potential_is_warning(self):
        a = self.alerts(cpu_percent=30.0, psi_cpu_some_pct=25.0)
        self.assertEqual(D.overall_banner(latest_from(a))[0], "warning")

    def test_process_alerts_do_not_drive_the_banner(self):
        a = feed(BottleneckDetector(), 5, processes=[{"pid": 1, "name": "x", "start_time": 1.0, "cpu_pct": 99.0}])
        self.assertTrue(any(x["resource"] == "Process" for x in a))
        self.assertEqual(D.overall_banner(latest_from(a))[0], "healthy")

    def test_missing_latest(self):
        self.assertEqual(D.overall_banner(None)[0], "healthy")


class Rendering(unittest.TestCase):
    def test_states_have_five_distinct_styles(self):
        self.assertEqual(len({v[1] for v in D.STATE_STYLE.values()}), 5)
        self.assertIn("Confirmed", D.badge("CONFIRMED"))
        self.assertIn("Normal", D.badge("nonsense"))

    def test_resource_states_default_to_normal(self):
        self.assertEqual(D.resource_states({"resource_status": {"CPU": "CONFIRMED"}}),
                         {"CPU": "CONFIRMED", "Memory": "NORMAL", "Disk I/O": "NORMAL"})

    def test_diagnosis_summary_has_every_required_field(self):
        a = by_resource(feed(BottleneckDetector(), 5, cpu_percent=99.0, psi_cpu_some_pct=60.0, procs_running=9,
                             processes=[{"pid": 5, "name": "hog", "start_time": 1.0, "cpu_pct": 380.0}]), "CPU")
        labels = [k for k, _ in D.diagnosis_summary(a)]
        for need in ("Resource", "Current condition", "Severity", "Confidence", "Duration", "Supporting metrics",
                     "Likely cause", "Responsible process", "Recommended action"):
            self.assertIn(need, labels)
        self.assertTrue(D.evidence_rows(a))
        self.assertIn("hog", dict(D.diagnosis_summary(a))["Responsible process"])


class Files(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_load_latest_tolerates_missing_and_corrupt(self):
        self.assertIsNone(D.load_latest(self.d))
        with open(os.path.join(self.d, "latest.json"), "w") as f:
            f.write("{half")
        self.assertIsNone(D.load_latest(self.d))
        with open(os.path.join(self.d, "latest.json"), "w") as f:
            f.write('{"sample": {"psi": null}}')
        self.assertEqual(D.load_latest(self.d)["sample"]["psi"], None)

    def test_load_csv_missing_or_empty(self):
        self.assertTrue(D.load_csv(self.d, "nope.csv").empty)
        open(os.path.join(self.d, "e.csv"), "w").close()
        self.assertTrue(D.load_csv(self.d, "e.csv").empty)

    def _run(self, name, status, updated, verdict="PASS"):
        p = os.path.join(self.d, name)
        os.makedirs(p)
        with open(os.path.join(p, "state.json"), "w") as f:
            json.dump({"run_id": name, "kind": "cpu", "status": status, "phase": "stress", "t": 3, "updated": updated}, f)
        if status != "running":
            with open(os.path.join(p, "report.json"), "w") as f:
                json.dump({"run_id": name, "kind": "cpu", "status": status, "evaluation": {"verdict": verdict}}, f)

    def test_list_and_running_experiments(self):
        self._run("a", "completed", time.time() - 100)
        self._run("b", "running", time.time())
        self._run("c", "running", time.time() - 3600)          # stale: process died, never reported
        os.makedirs(os.path.join(self.d, "junk"))
        runs = D.list_experiments(self.d)
        self.assertEqual([r["run_id"] for r in runs], ["a"])
        self.assertEqual(D.running_experiment(self.d)["run_id"], "b")
        self.assertIsNone(D.running_experiment(os.path.join(self.d, "missing")))

    def test_cancel_request_creates_flag(self):
        D.request_cancel(self.d)
        self.assertTrue(os.path.exists(os.path.join(self.d, "CANCEL")))

    def test_launch_command_is_explicit_and_bounded(self):
        cmd = D.build_launch_cmd("memory", 30, 20, 30, memory_pct=70, out="/x")
        self.assertIn("--yes", cmd)
        self.assertEqual(cmd[cmd.index("--memory-pct") + 1], "70")
        self.assertNotIn("--workers", cmd)

    def test_launch_runs_detached_and_validates_in_child(self):
        cmd = D.build_launch_cmd("cpu", 1, 20, 30, out=self.d)               # invalid duration -> child exits 2
        p = D.launch_experiment(cmd, self.d)
        self.assertEqual(p.wait(timeout=30), 2)
        with open(os.path.join(self.d, "last_launch.log")) as f:
            self.assertIn("duration", f.read())


if __name__ == "__main__":
    unittest.main()
