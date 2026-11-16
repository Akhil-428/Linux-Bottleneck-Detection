import csv
import json
import math
import os
import shutil
import tempfile
import unittest

from helpers import read_csv, read_text, sample          # noqa: F401
import main as app
from analyzer.bottleneck_detector import BottleneckDetector
from analyzer.validation import (ConfigError, load_config_file, validate_baseline, validate_experiment,
                                 validate_interval, validate_thresholds, validate_window)
from engine import MonitorEngine


class Validation(unittest.TestCase):
    def assertErr(self, fn, *a, text=None, **k):
        with self.assertRaises(ConfigError) as cm:
            fn(*a, **k)
        if text:
            self.assertIn(text, str(cm.exception))

    def test_thresholds(self):
        self.assertEqual(validate_thresholds({"cpu": 70})["cpu"], 70)
        self.assertErr(validate_thresholds, {"cpu": 0}, text="between")
        self.assertErr(validate_thresholds, {"cpu": 101}, text="between")
        self.assertErr(validate_thresholds, {"cpu": "high"}, text="finite number")
        self.assertErr(validate_thresholds, {"cpu": None}, text="finite number")
        self.assertErr(validate_thresholds, {"cpu": True}, text="finite number")
        self.assertErr(validate_thresholds, {"cpu": math.inf}, text="finite number")
        self.assertErr(validate_thresholds, {"typo": 1}, text="Unknown threshold")
        self.assertErr(validate_thresholds, {"cpu": 95, "cpu_critical": 90}, text="must not be lower")

    def test_window_and_persistence(self):
        self.assertEqual(validate_window(5, 3), (5, 3))
        self.assertErr(validate_window, 0, 0, text="between 1 and 1000")
        self.assertErr(validate_window, 5, 0, text="Persistence")
        self.assertErr(validate_window, 5, 6, text="Persistence")
        self.assertErr(validate_window, 5.5, 3, text="integer")
        self.assertErr(validate_window, 5, "3", text="integer")

    def test_interval(self):
        self.assertEqual(validate_interval(2), 2.0)
        for bad in (0, 0.1, -1, 301, math.nan, "2", None):
            self.assertErr(validate_interval, bad, text="Sampling")

    def test_baseline(self):
        self.assertTrue(validate_baseline()["enabled"])
        self.assertErr(validate_baseline, {"window": 5}, text="between")
        self.assertErr(validate_baseline, {"min_samples": 500, "window": 100}, text="cannot exceed")
        self.assertErr(validate_baseline, {"z": 0}, text="between")
        self.assertErr(validate_baseline, {"bogus": 1}, text="Unknown baseline")
        self.assertErr(validate_baseline, {"enabled": "yes"}, text="true or false")

    def test_experiment_parameters(self):
        validate_experiment("cpu", 30, 20, 30, workers=2)
        self.assertErr(validate_experiment, "gpu", 30, 20, 30, text="Unknown experiment")
        self.assertErr(validate_experiment, "cpu", 4, 20, 30, text="duration")
        self.assertErr(validate_experiment, "cpu", 601, 20, 30, text="duration")
        self.assertErr(validate_experiment, "cpu", 30, 1, 30, text="baseline")
        self.assertErr(validate_experiment, "cpu", 30, 20, 30, workers=0, text="workers")
        self.assertErr(validate_experiment, "cpu", 30, 20, 30, workers=1000, text="workers")
        self.assertErr(validate_experiment, "memory", 30, 20, 30, memory_pct=99, text="memory_pct")
        self.assertErr(validate_experiment, "disk", 30, 20, 30, disk_mb=100000, text="disk_mb")

    def test_detector_constructor_validates(self):
        self.assertErr(BottleneckDetector, window=0, required=0)
        self.assertErr(BottleneckDetector, window=5, required=9)
        self.assertErr(BottleneckDetector, cpuu=3)
        self.assertIsInstance(ConfigError("x"), ValueError)


class ConfigFile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def write(self, text):
        p = os.path.join(self.d, "c.json")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_valid_and_invalid_files(self):
        cfg = load_config_file(self.write('{"interval": 3, "thresholds": {"cpu": 80}}'))
        self.assertEqual(cfg["interval"], 3)
        for bad, text in (("{oops", "not valid JSON"), ("[1]", "JSON object"), ('{"nope": 1}', "Unknown config key")):
            with self.assertRaises(ConfigError) as cm:
                load_config_file(self.write(bad))
            self.assertIn(text, str(cm.exception))
        with self.assertRaises(ConfigError):
            load_config_file(os.path.join(self.d, "missing.json"))

    def test_cli_overrides_config_file(self):
        p = self.write('{"interval": 3, "window": 8, "required": 4, "thresholds": {"cpu": 70}}')
        a = app.build_parser().parse_args(["--config", p, "--window", "6"])
        cfg = app.resolve_config(a)
        self.assertEqual((cfg["interval"], cfg["window"], cfg["required"]), (3, 6, 4))
        self.assertEqual(cfg["thresholds"], {"cpu": 70})

    def test_main_reports_bad_config_with_exit_code_2(self):
        for argv in (["--interval", "0.1"], ["--window", "5", "--required", "9"], ["--duration", "-3"],
                     ["--config", "/nonexistent.json"], ["--z-threshold", "0"]):
            self.assertEqual(app.main(argv + ["--data-dir", self.d]), 2, argv)


class Engine(unittest.TestCase):
    class Boom:
        def sample(self, *a, **k):
            raise OSError("kernel interface vanished")

        def sample_all(self, *a, **k):
            raise RuntimeError("psutil exploded")

    class Fixed:
        def __init__(self, d):
            self.d = d

        def sample(self, dt):
            return dict(self.d)

        def sample_all(self, *a, **k):
            return {"processes": [], "top_cpu": [], "top_mem": [], "top_io": []}

    def test_failing_collectors_degrade_to_unavailable_metrics(self):
        e = MonitorEngine(collectors={"cpu": self.Boom(), "mem": self.Boom(), "disk": self.Boom(), "procs": self.Boom()})
        with self.assertLogs("bottleneck.engine", "WARNING"):
            s, alerts, _ = e.step(1.0)
        self.assertEqual(alerts, [])
        self.assertNotIn("cpu_percent", s)

    def test_one_failing_collector_does_not_stop_the_others(self):
        e = MonitorEngine(collectors={"cpu": self.Fixed({"cpu_percent": 12.0, "cores": 2}), "mem": self.Boom(),
                                      "disk": self.Fixed({}), "procs": self.Fixed({})})
        with self.assertLogs("bottleneck.engine", "WARNING"):
            s, _, _ = e.step(1.0)
        self.assertEqual(s["cpu_percent"], 12.0)

    def test_engine_validates_its_arguments(self):
        for kw in ({"interval": 0}, {"window": 2, "required": 5}, {"thresholds": {"nope": 1}},
                   {"baseline": {"z": -1}}):
            with self.assertRaises(ConfigError):
                MonitorEngine(**kw)


class FilesAndSerialization(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_latest_json_is_strict_json_even_with_nan(self):
        p = os.path.join(self.d, "latest.json")
        app.write_latest(p, {"sample": {"psi": math.nan, "x": math.inf, "ok": 1.5}, "list": [math.nan]})
        with open(p) as f:
            txt = f.read()
        for tok in ("NaN", "Infinity"):
            self.assertNotIn(tok, txt)
        data = json.loads(txt, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        self.assertIsNone(data["sample"]["psi"])

    def test_csv_blank_for_unavailable_and_header_written_once(self):
        p = os.path.join(self.d, "log.csv")
        app.append_row(p, ["a", "b"], {"a": 1, "b": math.nan})
        app.append_row(p, ["a", "b"], {"a": 2, "b": None})
        rows = read_csv(p)[1:]
        self.assertEqual([r[1] for r in rows], ["", ""])
        self.assertEqual(len(rows), 2)

    def test_header_change_rotates_old_file_instead_of_misaligning(self):
        p = os.path.join(self.d, "log.csv")
        app.append_row(p, ["a", "b"], {"a": 1, "b": 2})
        app.append_row(p, ["a", "b", "c"], {"a": 1, "b": 2, "c": 3})
        self.assertTrue(any(f.endswith(".old") for f in os.listdir(self.d)))
        self.assertEqual(read_csv(p)[0], ["a", "b", "c"])

    def test_legacy_fields_keep_their_original_order(self):
        legacy = ["timestamp", "cpu_percent", "iowait_percent", "load1", "load5", "load15", "cores", "ctx_switches_s",
                  "mem_percent", "mem_available_mb", "swap_percent", "swap_in_s", "swap_out_s", "disk_read_mbs",
                  "disk_write_mbs", "disk_util", "disk_await_ms", "psi_cpu", "psi_mem", "psi_io", "top_cpu_name",
                  "top_cpu_pid", "top_cpu_pct", "top_mem_name", "top_mem_pid", "top_mem_pct"]
        self.assertEqual(app.FIELDS[:len(legacy)], legacy)

    def test_live_smoke_run_writes_valid_files(self):
        """Integration smoke test against the real machine (asserts structure only)."""
        self.assertEqual(app.main(["--interval", "0.5", "--duration", "1.5", "--data-dir", self.d]), 0)
        data = json.loads(read_text(os.path.join(self.d, "latest.json")), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        self.assertEqual(data["schema"], 2)
        self.assertIn("resource_status", data)
        self.assertGreaterEqual(len(read_csv(os.path.join(self.d, "system_logs.csv"))) - 1, 2)


if __name__ == "__main__":
    unittest.main()
