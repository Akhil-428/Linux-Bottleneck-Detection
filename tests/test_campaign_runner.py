"""Regression tests for campaign_runner.py.

Validates:
1. Normal CLI invocation does not start experiments.
2. --dry-run does not start real workloads.
3. --test executes only the harmless control test.
4. --run invokes actual campaign execution (mocked, no real stress).
5. A cancelled experiment is not marked COMPLETED.
6. Cancellation prevents subsequent tasks from starting.
7. Completed tasks with valid reports are skipped.
8. Failed and cancelled tasks can be retried.
9. Existing manifest data is preserved.
10. Single-instance protection lock behavior.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import campaign_runner
from campaign_runner import CampaignLock, CampaignManifest, CampaignRunner


class TestCampaignRunnerCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_campaign_cli_")
        self.addCleanup(shutil.rmtree, self.test_dir, True)

    def test_normal_cli_invocation_does_not_start_experiments(self):
        """Normal CLI without flags should only display plan and exit 0 without running experiments."""
        with mock.patch.object(CampaignRunner, "execute_campaign") as mock_exec, \
             mock.patch.object(CampaignRunner, "run_single_task") as mock_task:
            code = campaign_runner.main(["--campaign-dir", self.test_dir])
            self.assertEqual(code, 0)
            mock_exec.assert_not_called()
            mock_task.assert_not_called()

        manifest_path = Path(self.test_dir) / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = CampaignManifest(self.test_dir)
        self.assertEqual(manifest.data["summary"]["total"], 15)
        self.assertEqual(manifest.data["summary"]["pending"], 15)
        self.assertEqual(manifest.data["summary"]["completed"], 0)

    def test_dry_run_does_not_start_real_workloads(self):
        """--dry-run should simulate execution and update manifest without spawning real workloads."""
        with mock.patch("campaign_runner.ExperimentRunner") as mock_runner_cls:
            code = campaign_runner.main(["--dry-run", "--campaign-dir", self.test_dir])
            self.assertEqual(code, 0)
            # ExperimentRunner must never be instantiated during dry-run
            mock_runner_cls.assert_not_called()

        manifest = CampaignManifest(self.test_dir)
        self.assertEqual(manifest.data["summary"]["total"], 15)
        self.assertEqual(manifest.data["summary"]["completed"], 15)
        self.assertEqual(manifest.data["summary"]["pending"], 0)
        for t in manifest.data["tasks"]:
            self.assertEqual(t["status"], "COMPLETED")
            self.assertEqual(t["verdict"], "PASS")
            self.assertTrue(t["run_id"].startswith("dryrun-"))

    def test_test_flag_executes_only_harmless_control(self):
        """--test should execute only the single harmless control test task."""
        with mock.patch.object(CampaignRunner, "run_single_task", return_value="PASS") as mock_single:
            code = campaign_runner.main(["--test", "--campaign-dir", self.test_dir])
            self.assertEqual(code, 0)
            mock_single.assert_called_once()
            called_task = mock_single.call_args[0][0]
            self.assertEqual(called_task["category"], "control")
            self.assertEqual(called_task["parameters"]["duration"], 5.0)
            self.assertIsNone(called_task["parameters"]["workers"])

    def test_run_flag_invokes_actual_execution(self):
        """--run should call execute_campaign."""
        with mock.patch.object(CampaignRunner, "execute_campaign") as mock_exec:
            code = campaign_runner.main(["--run", "--campaign-dir", self.test_dir])
            self.assertEqual(code, 0)
            mock_exec.assert_called_once()

    def test_flags_are_mutually_exclusive(self):
        """--run, --dry-run, and --test must be mutually exclusive."""
        for flag_combo in (["--run", "--dry-run"], ["--run", "--test"], ["--dry-run", "--test"]):
            with self.assertRaises(SystemExit) as cm:
                campaign_runner.main(flag_combo + ["--campaign-dir", self.test_dir])
            self.assertEqual(cm.exception.code, 2)


class TestCancellationAndRecovery(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_campaign_cancel_")
        self.addCleanup(shutil.rmtree, self.test_dir, True)
        self.runner = CampaignRunner(campaign_dir=self.test_dir, repeats=3, dry_run=False)
        self.runner.initialize_campaign()

    def test_cancelled_experiment_is_not_marked_completed(self):
        """A cancelled experiment must be marked CANCELLED, not COMPLETED, and verdict must not be PASS."""
        task = self.runner.manifest.data["tasks"][0]

        fake_cancelled_report = {
            "run_id": "test-cancelled-run",
            "status": "cancelled",
            "evaluation": {"verdict": "PASS"}  # Even if evaluation evaluated partial timeline
        }

        with mock.patch("campaign_runner.ExperimentRunner") as mock_runner_cls:
            mock_runner = mock.MagicMock()
            mock_runner.run_id = "test-cancelled-run"
            mock_runner.out_dir = str(Path(self.test_dir) / "test-cancelled-run")
            mock_runner.run.return_value = fake_cancelled_report
            mock_runner._cancel = True
            mock_runner_cls.return_value = mock_runner

            res = self.runner.run_single_task(task)

            self.assertEqual(res, "CANCELLED")
            self.assertTrue(self.runner.interrupted)

        # Verify manifest state
        saved_task = self.runner.manifest.get_task(task["id"])
        self.assertEqual(saved_task["status"], "CANCELLED")
        self.assertIsNone(saved_task["verdict"])
        self.assertIn("cancelled", saved_task["error"].lower())

        summary = self.runner.manifest.data["summary"]
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["cancelled"], 1)

    def test_cancellation_prevents_subsequent_tasks(self):
        """When an experiment is cancelled, campaign execution must halt immediately without starting next task."""
        tasks = self.runner.manifest.data["tasks"]

        with mock.patch.object(self.runner, "run_single_task") as mock_single:
            # First task returns CANCELLED
            def side_effect(t):
                if t["id"] == tasks[0]["id"]:
                    self.runner.manifest.mark_cancelled(t["id"], "Cancelled by user")
                    return "CANCELLED"
                return "PASS"

            mock_single.side_effect = side_effect
            self.runner.execute_campaign()

            # Task 0 was executed, but tasks 1..14 were never started
            self.assertEqual(mock_single.call_count, 1)

        self.assertEqual(self.runner.manifest.get_task(tasks[0]["id"])["status"], "CANCELLED")
        self.assertEqual(self.runner.manifest.get_task(tasks[1]["id"])["status"], "PENDING")

    def test_completed_tasks_with_valid_reports_are_skipped(self):
        """A task marked COMPLETED with a valid report.json on disk must be skipped."""
        task = self.runner.manifest.data["tasks"][0]
        run_dir = Path(self.test_dir) / "valid_run_dir"
        run_dir.mkdir(parents=True, exist_ok=True)
        report_file = run_dir / "report.json"
        valid_report = {
            "run_id": "valid_run_dir",
            "status": "completed",
            "evaluation": {"verdict": "PASS"},
            "timeline": [{"t": 1.0}]
        }
        with open(report_file, "w") as f:
            json.dump(valid_report, f)

        self.runner.manifest.mark_started(task["id"], "valid_run_dir", run_dir)
        self.runner.manifest.mark_completed(task["id"], "PASS", report_file)

        # Now calling run_single_task should skip it
        with mock.patch("campaign_runner.ExperimentRunner") as mock_runner_cls:
            res = self.runner.run_single_task(task)
            self.assertEqual(res, "PASS")
            mock_runner_cls.assert_not_called()

    def test_partial_or_cancelled_report_is_not_considered_completed(self):
        """If report.json is cancelled or incomplete, is_task_effectively_completed must return False."""
        task = self.runner.manifest.data["tasks"][0]
        run_dir = Path(self.test_dir) / "incomplete_run_dir"
        run_dir.mkdir(parents=True, exist_ok=True)
        report_file = run_dir / "report.json"

        # 1. Cancelled report
        with open(report_file, "w") as f:
            json.dump({"status": "cancelled", "evaluation": {"verdict": "PASS"}}, f)
        task["status"] = "COMPLETED"
        task["run_dir"] = str(run_dir)
        self.assertFalse(self.runner.is_task_effectively_completed(task))

        # 2. Corrupt report
        with open(report_file, "w") as f:
            f.write("{corrupt json")
        self.assertFalse(self.runner.is_task_effectively_completed(task))

        # 3. Missing report file
        report_file.unlink()
        self.assertFalse(self.runner.is_task_effectively_completed(task))

    def test_failed_and_cancelled_tasks_can_be_retried(self):
        """Tasks marked FAILED or CANCELLED must be eligible for retry."""
        task = self.runner.manifest.data["tasks"][0]
        task["status"] = "FAILED"
        self.assertFalse(self.runner.is_task_effectively_completed(task))

        task["status"] = "CANCELLED"
        self.assertFalse(self.runner.is_task_effectively_completed(task))

    def test_existing_manifest_data_is_preserved(self):
        """Re-instantiating CampaignManifest preserves existing task states and summaries."""
        fresh_dir = tempfile.mkdtemp(prefix="test_manifest_preservation_")
        self.addCleanup(shutil.rmtree, fresh_dir, True)
        manifest1 = CampaignManifest(fresh_dir)
        manifest1.init_tasks(["control", "cpu"], 1, {"control": {}, "cpu": {}})
        task0_id = manifest1.data["tasks"][0]["id"]
        manifest1.mark_completed(task0_id, "PASS")

        # Reload in a new instance
        manifest2 = CampaignManifest(fresh_dir)
        self.assertEqual(manifest2.data["tasks"][0]["status"], "COMPLETED")
        self.assertEqual(manifest2.data["tasks"][0]["verdict"], "PASS")
        self.assertEqual(manifest2.data["tasks"][1]["status"], "PENDING")
        self.assertEqual(manifest2.data["summary"]["completed"], 1)
        self.assertEqual(manifest2.data["summary"]["pending"], 1)
        self.assertEqual(manifest2.data["summary"]["total"], 2)


class TestSingleInstanceLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_campaign_lock_")
        self.addCleanup(shutil.rmtree, self.test_dir, True)
        self.lock_file = Path(self.test_dir) / "campaign.lock"

    def test_acquire_and_release(self):
        lock = CampaignLock(self.lock_file)
        with lock:
            self.assertTrue(self.lock_file.exists())
            self.assertTrue(lock.acquired)
        self.assertFalse(self.lock_file.exists())

    def test_concurrent_instance_rejected(self):
        lock1 = CampaignLock(self.lock_file)
        lock1.acquire()

        lock2 = CampaignLock(self.lock_file)
        with self.assertRaises(RuntimeError) as cm:
            lock2.acquire()
        self.assertIn("Another campaign instance is currently running", str(cm.exception))

        lock1.release()

    def test_stale_lock_is_reclaimed(self):
        # Create a lockfile with a non-existent PID
        with open(self.lock_file, "w") as f:
            json.dump({"pid": 99999999, "started_at": "2026-01-01T00:00:00"}, f)

        lock = CampaignLock(self.lock_file)
        # Should detect stale lock and acquire cleanly
        lock.acquire()
        self.assertTrue(lock.acquired)
        lock.release()


if __name__ == "__main__":
    unittest.main()
