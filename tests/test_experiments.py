import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import psutil

from helpers import read_text, sample          # noqa: F401
from analyzer.validation import ConfigError
from experiments import workloads
from experiments.evaluation import aggregate, evaluate, stress_effective
from experiments.runner import ExperimentRunner
from experiments.workloads import Workload, fs_type, plan_experiment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def alive(pids):
    out = []
    for p in pids:
        try:
            if psutil.Process(p).status() != psutil.STATUS_ZOMBIE:
                out.append(p)
        except psutil.Error:
            pass
    return out


class Safety(unittest.TestCase):
    def test_system_paths_are_refused(self):
        for path in ("/etc", "/proc", "/sys", "/dev/shm", "/usr/lib", "/"):
            with self.assertRaises(ConfigError, msg=path):
                plan_experiment("disk", disk_path=path)

    def test_unwritable_or_missing_dir_refused(self):
        with self.assertRaises(ConfigError):
            plan_experiment("disk", disk_path="/nonexistent-dir-xyz")

    def test_ram_filesystem_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(workloads, "fs_type", return_value="tmpfs"):
                with self.assertRaises(ConfigError) as cm:
                    plan_experiment("disk", disk_path=d)
        self.assertIn("RAM", str(cm.exception))

    def test_fs_type_longest_prefix_match(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("rootfs / ext4 rw 0 0\ntmpfs /tmp tmpfs rw 0 0\nx /tmp/disk xfs rw 0 0\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(fs_type("/tmp/a", f.name), "tmpfs")
        self.assertEqual(fs_type("/tmp/disk/a", f.name), "xfs")
        self.assertEqual(fs_type("/home/u", f.name), "ext4")

    def test_low_free_space_refused(self):
        with tempfile.TemporaryDirectory() as d:
            fake = shutil._ntuple_diskusage(10 * 2**30, 9 * 2**30, 2**29)       # 512 MB free
            with mock.patch.object(workloads.shutil, "disk_usage", return_value=fake):
                with self.assertRaises(ConfigError) as cm:
                    plan_experiment("disk", disk_path=d)
        self.assertIn("free space", str(cm.exception))

    def test_memory_plan_is_bounded_by_pct_and_safety_floor(self):
        vm = mock.Mock(available=2000 * 2**20, total=4000 * 2**20)
        with mock.patch.object(workloads.psutil, "virtual_memory", return_value=vm), \
                mock.patch.object(workloads, "cgroup_mem_headroom_mb", return_value=None):
            p = plan_experiment("memory", memory_pct=95)
        self.assertLessEqual(p["memory_mb"], 2000 - p["floor_mb"])
        self.assertGreaterEqual(p["floor_mb"], 256)
        self.assertTrue(any("OOM" in w or "disposable" in w for w in p["warnings"]))

    def test_memory_plan_respects_cgroup_limit(self):
        vm = mock.Mock(available=8000 * 2**20, total=16000 * 2**20)
        with mock.patch.object(workloads.psutil, "virtual_memory", return_value=vm), \
                mock.patch.object(workloads, "cgroup_mem_headroom_mb", return_value=1000.0):
            p = plan_experiment("memory")
        self.assertLessEqual(p["memory_mb"], 1000)

    def test_memory_refused_when_headroom_too_small(self):
        vm = mock.Mock(available=300 * 2**20, total=4000 * 2**20)
        with mock.patch.object(workloads.psutil, "virtual_memory", return_value=vm), \
                mock.patch.object(workloads, "cgroup_mem_headroom_mb", return_value=None):
            with self.assertRaises(ConfigError):
                plan_experiment("memory")

    def test_bounds_enforced(self):
        for kw in (dict(kind="cpu", duration=10 ** 6), dict(kind="cpu", workers=0), dict(kind="cpu", workers=10 ** 6),
                   dict(kind="memory", memory_pct=100), dict(kind="disk", disk_mb=10 ** 7), dict(kind="x")):
            with self.assertRaises(ConfigError, msg=str(kw)):
                plan_experiment(**kw)

    def test_stress_ng_requested_but_missing_gives_alternative(self):
        with mock.patch.object(workloads.shutil, "which", return_value=None):
            with self.assertRaises(ConfigError) as cm:
                plan_experiment("cpu", backend="stress-ng")
            self.assertIn("builtin", str(cm.exception))
            self.assertEqual(plan_experiment("cpu")["backend"], "builtin")      # auto falls back

    def test_control_starts_no_workload(self):
        p = plan_experiment("control")
        self.assertEqual((p["backend"], p["workers"]), ("none", 0))

    def test_benign_control_never_oversubscribes(self):
        p = plan_experiment("cpu-benign")
        self.assertEqual(p["workers"], os.cpu_count() or 1)
        self.assertGreater(plan_experiment("cpu")["workers"], os.cpu_count() or 1)


class WorkloadLifecycle(unittest.TestCase):
    def test_cpu_workload_starts_and_stop_cleans_everything(self):
        w = Workload(plan_experiment("cpu", duration=30, workers=2, backend="builtin"))
        w.start()
        time.sleep(0.5)
        pids = w.poll_pids()
        self.assertEqual(len(pids), 2)
        self.assertTrue(w.running())
        w.stop()
        self.assertFalse(w.running())
        self.assertEqual(alive(pids), [])
        self.assertEqual(w.leftover_pids(), set())
        w.stop()                                                  # idempotent

    def test_only_own_process_groups_are_signalled(self):
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))
        w = Workload(plan_experiment("cpu", duration=30, workers=1, backend="builtin"))
        w.start()
        time.sleep(0.3)
        w.stop()
        self.assertIsNone(bystander.poll())                        # unrelated process untouched

    def test_signal_helper_never_kills_foreign_group(self):
        # A child that shares OUR process group must be signalled individually, never via killpg.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.kill)
        with mock.patch.object(workloads.os, "killpg") as killpg:
            Workload._signal(child, 15)
            killpg.assert_not_called()
        child.wait(timeout=5)

    def test_orphaned_workers_die_when_supervisor_is_killed(self):
        code = ("import sys,time; sys.path.insert(0, %r)\n"
                "from experiments.workloads import *\n"
                "w=Workload(plan_experiment('cpu',duration=60,workers=2,backend='builtin')); w.start()\n"
                "print(','.join(map(str,w.poll_pids())),flush=True); time.sleep(60)\n" % ROOT)
        sup = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        pids = [int(x) for x in sup.stdout.readline().split(",")]
        time.sleep(0.5)
        self.assertEqual(len(alive(pids)), 2)
        sup.kill()
        sup.wait()
        sup.stdout.close()
        end = time.time() + 5
        while alive(pids) and time.time() < end:
            time.sleep(0.1)
        self.assertEqual(alive(pids), [])

    def test_worker_has_its_own_deadline(self):
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "experiments.worker", "cpu", "--seconds", "1"], cwd=ROOT, timeout=15)
        self.assertEqual(p.returncode, 0)
        self.assertLess(time.time() - t0, 8)

    def test_memory_worker_stops_growing_at_safety_floor(self):
        out = subprocess.run([sys.executable, "-m", "experiments.worker", "memory", "--seconds", "1", "--mb", "64",
                              "--floor-mb", "100000000"], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertIn("safety floor", out.stdout)
        self.assertIn("allocated=0MB", out.stdout)

    def test_disk_workload_writes_inside_private_dir_and_cleans_up(self):
        try:
            plan = plan_experiment("disk", duration=30, workers=1, disk_mb=16, backend="builtin")
        except ConfigError as e:
            self.skipTest(f"no suitable real-disk directory here: {e}")
        w = Workload(plan)
        w.start()
        tmp = w.tmpdir
        self.assertTrue(os.path.isdir(tmp))
        self.assertTrue(tmp.startswith(plan["disk_base"]))
        time.sleep(1.5)
        files = os.listdir(tmp)
        self.assertTrue(all(f.startswith("bd-exp-") for f in files))
        size = sum(os.path.getsize(os.path.join(tmp, f)) for f in files)
        self.assertLessEqual(size, 16 * 2**20 + 4 * 2**20)         # bounded
        w.stop()
        self.assertFalse(os.path.exists(tmp))

    def test_stress_ng_backend_command_and_lifecycle_with_fake_binary(self):
        """stress-ng is not installed here; verify our command lines and cleanup using a fake binary."""
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "stress-ng")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\necho \"$@\" > %s/args\nsleep 30\n" % d)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            with mock.patch.object(workloads.shutil, "which", return_value=fake):
                p = plan_experiment("cpu", duration=20, workers=3, backend="stress-ng")
                w = Workload(p)
                w.start()
                time.sleep(0.7)
                args = read_text(os.path.join(d, "args"))
                self.assertIn("--cpu 3", args)
                self.assertIn("--timeout 20s", args)
                pids = w.poll_pids()
                w.stop()
                self.assertEqual(alive(pids), [])


def rec(t, phase, diag=None, **metrics):
    return {"t": t, "phase": phase, "metrics": metrics, "status": {}, "diagnoses": diag or [],
            "workload_running": phase == "stress"}


def D(resource="CPU", state="CONFIRMED", conf="HIGH", pids=(), verdict="primary"):
    return {"resource": resource, "device": None, "state": state, "severity": "WARNING", "confidence": conf,
            "method": "static", "duration_s": 1, "headline": "h", "likely_cause": "c", "recommendation": "r",
            "responsible_verdict": verdict if pids else "none", "responsible_text": "", "responsible_pids": list(pids),
            "evidence": [], "contradicting": [], "missing": []}


def report(kind, base, stress, recov, pids=(100,), status="completed", leftovers=()):
    tl = [rec(i, "baseline", d, cpu_percent=1.0, mem_percent=5.0, disk_util=0.0) for i, d in enumerate(base)]
    s0 = len(base)
    tl += [rec(s0 + i, "stress", d, cpu_percent=99.0, psi_cpu_some_pct=50.0, mem_percent=70.0, disk_util=90.0)
           for i, d in enumerate(stress)]
    e0 = s0 + len(stress)
    tl += [rec(e0 + i, "recovery", d, cpu_percent=1.0 if i >= 1 else 80.0, mem_percent=5.0, disk_util=0.0)
           for i, d in enumerate(recov)]
    return {"run_id": "t", "kind": kind, "status": status, "timeline": tl, "workload_pids": list(pids),
            "leftover_pids": list(leftovers), "marks": {"stress_start": s0, "stress_end": e0, "end": e0 + len(recov)}}


class Evaluation(unittest.TestCase):
    def test_detection_latency_recovery_and_attribution(self):
        none = []
        r = report("cpu", [none] * 5,
                   [none, none, [D(state="POTENTIAL", conf="LOW")], [D(pids=[100])], [D(pids=[100])], [D(pids=[999])], [D(pids=[])]],
                   [[D(pids=[100])], none, none])
        e = evaluate(r)
        self.assertEqual(e["verdict"], "PASS")
        self.assertEqual(e["detection"]["latency_potential_s"], 2)
        self.assertEqual(e["detection"]["latency_confirmed_s"], 3)
        self.assertEqual(e["attribution"], {"correct": 2, "wrong": 1, "abstained": 2, "reliability": 0.667,
                                            "workload_pids_known": True})
        self.assertEqual(e["recovery"]["detector_clear_s"], 1)
        self.assertEqual(e["false_positives_baseline"]["positive_samples"], 0)
        self.assertTrue(e["cleanup"]["ok"])

    def test_missed_detection_with_effective_stress_is_fail(self):
        e = evaluate(report("cpu", [[]] * 5, [[]] * 6, [[]] * 3))
        self.assertEqual(e["verdict"], "FAIL")
        self.assertTrue(e["stress_effective"]["effective"])

    def test_missed_detection_without_real_stress_is_not_a_detector_failure(self):
        r = report("memory", [[]] * 5, [[]] * 6, [[]] * 3)
        for x in r["timeline"]:
            x["metrics"]["psi_mem_some_pct"] = 0.0
            x["metrics"]["swap_out_s"] = 0.0
        self.assertEqual(evaluate(r)["verdict"], "INEFFECTIVE_STRESS")

    def test_potential_only_is_partial(self):
        e = evaluate(report("cpu", [[]] * 5, [[D(state="POTENTIAL", conf="LOW")]] * 6, [[]] * 3))
        self.assertEqual(e["verdict"], "PARTIAL")

    def test_wrong_resource_does_not_count_as_target_detection(self):
        e = evaluate(report("cpu", [[]] * 5, [[D(resource="Disk I/O")]] * 6, [[]] * 3))
        self.assertEqual(e["verdict"], "FAIL")
        self.assertEqual(e["resources_confirmed_during_stress"], ["Disk I/O"])

    def test_other_confirmed_resources_are_listed_not_hidden(self):
        e = evaluate(report("cpu", [[]] * 5, [[D(), D(resource="Memory")]] * 6, [[]] * 3))
        self.assertFalse(e["diagnostic_reliability"]["only_target_confirmed"])
        self.assertEqual(e["diagnostic_reliability"]["other_resources_confirmed"], ["Memory"])

    def test_baseline_false_positives_are_counted(self):
        e = evaluate(report("cpu", [[D(state="POTENTIAL")], [], [], []], [[D()]] * 5, [[]] * 3))
        self.assertEqual(e["false_positives_baseline"], {"samples": 4, "positive_samples": 1, "rate": 0.25})

    def test_elevated_is_never_a_positive(self):
        e = evaluate(report("cpu-benign", [[]] * 5, [[D(state="ELEVATED")]] * 8, [[]] * 3))
        self.assertEqual(e["verdict"], "PASS")
        self.assertEqual(e["negative_control"]["samples_flagged_elevated_only"], 8)

    def test_benign_control_fails_when_contention_is_claimed(self):
        e = evaluate(report("cpu-benign", [[]] * 5, [[D()]] * 8, [[]] * 3))
        self.assertEqual(e["verdict"], "FAIL")
        self.assertEqual(e["negative_control"]["false_positive_rate"], 1.0)

    def test_idle_control(self):
        self.assertEqual(evaluate(report("control", [[]] * 5, [[]] * 8, [[]]))["verdict"], "PASS")
        self.assertEqual(evaluate(report("control", [[]] * 5, [[D(state="POTENTIAL")]] * 8, [[]]))["verdict"], "FAIL")

    def test_leftover_workload_processes_are_reported(self):
        e = evaluate(report("cpu", [[]] * 5, [[D()]] * 6, [[]] * 3, leftovers=[4242]))
        self.assertFalse(e["cleanup"]["ok"])

    def test_cancelled_run_is_inconclusive(self):
        self.assertTrue(evaluate(report("cpu", [[]] * 5, [[D()]] * 2, [[]] * 3, status="cancelled"))["verdict"]
                        .startswith("INCONCLUSIVE"))

    def test_recovery_not_applicable_when_nothing_was_detected(self):
        e = evaluate(report("cpu", [[]] * 5, [[]] * 6, [[]] * 3))
        self.assertFalse(e["recovery"]["applicable"])
        self.assertIsNone(e["recovery"]["detector_clear_s"])

    def test_stress_effective_rules(self):
        s = [rec(i, "stress", None, cpu_percent=99.0, psi_cpu_some_pct=60.0) for i in range(5)]
        self.assertTrue(stress_effective("cpu", s)[0])
        s = [rec(i, "stress", None, cpu_percent=99.0, psi_cpu_some_pct=1.0) for i in range(5)]
        self.assertFalse(stress_effective("cpu", s)[0])
        self.assertFalse(stress_effective("disk", [])[0])

    def test_aggregate_pools_rates_and_calibration(self):
        a = report("cpu", [[]] * 5, [[D(pids=[100])]] * 6, [[]] * 3)
        a["evaluation"] = evaluate(a)
        b = report("cpu", [[]] * 5, [[]] * 6, [[]] * 3)
        b["evaluation"] = evaluate(b)
        c = report("control", [[D(state="POTENTIAL", conf="LOW")], [], [], []], [[]] * 5, [[]])
        c["evaluation"] = evaluate(c)
        g = aggregate([a, b, c])
        self.assertEqual(g["by_kind"]["cpu"]["detection_rate_confirmed"], 0.5)
        self.assertEqual(g["false_positives"]["positive_samples"], 1)
        self.assertEqual(g["calibration"]["CONFIRMED/HIGH"]["precision"], 1.0)
        self.assertEqual(g["calibration"]["POTENTIAL/LOW"]["precision"], 0.0)


class FakeDetector:
    status = {"CPU": "NORMAL"}


class FakeEngine:
    """Deterministic engine: never reads the real system."""
    baseline = None

    def __init__(self):
        self.detector = FakeDetector()
        self.n = 0

    def step(self, dt):
        self.n += 1
        s = {"cpu_percent": 5.0, "mem_percent": 10.0}
        return s, [], {}


class Runner(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def make(self, kind="control", **kw):
        return ExperimentRunner(kind, self.d, duration=5, baseline_s=5, recovery_s=5, interval=0.5,
                                engine_factory=FakeEngine, backend="builtin", **kw)

    def test_full_cycle_writes_reports_and_cleans_workload(self):
        r = self.make("cpu", workers=1)
        rep = r.run()
        phases = [x["phase"] for x in rep["timeline"]]
        self.assertEqual(phases[0], "baseline")
        self.assertIn("stress", phases)
        self.assertEqual(phases[-1], "recovery")
        self.assertEqual(rep["status"], "completed")
        self.assertEqual(rep["leftover_pids"], [])
        self.assertTrue(rep["workload_pids"])
        self.assertEqual(alive(rep["workload_pids"]), [])
        for f in ("report.json", "report.md", "timeline.png"):
            self.assertTrue(os.path.exists(os.path.join(r.out_dir, f)), f)
        json.loads(read_text(os.path.join(r.out_dir, "report.json")),
                   parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

    def test_runner_never_forces_a_diagnosis(self):
        rep = self.make("control").run()
        self.assertTrue(all(x["diagnoses"] == [] for x in rep["timeline"]))
        self.assertEqual(rep["evaluation"]["verdict"], "PASS")

    def test_cancel_file_stops_workload_and_marks_run_cancelled(self):
        r = self.make("cpu", workers=1)
        threading.Timer(2.0, lambda: open(r.cancel_file, "w").close()).start()
        rep = r.run()
        self.assertEqual(rep["status"], "cancelled")
        self.assertEqual(alive(rep["workload_pids"]), [])
        self.assertFalse(os.path.exists(r.cancel_file))
        self.assertTrue(rep["evaluation"]["verdict"].startswith("INCONCLUSIVE"))

    def test_signal_handler_requests_clean_cancel(self):
        r = self.make("control")
        r._on_signal(2, None)
        self.assertTrue(r._cancel)

    def test_stale_cancel_file_from_previous_run_is_ignored(self):
        r = self.make("control")
        open(r.cancel_file, "w").close()
        self.assertEqual(r.run()["status"], "completed")

    def test_invalid_parameters_fail_before_anything_starts(self):
        with self.assertRaises(ConfigError):
            ExperimentRunner("cpu", self.d, duration=1, engine_factory=FakeEngine)


if __name__ == "__main__":
    unittest.main()
