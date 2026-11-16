import json
import math
import os
import shutil
import tempfile
import unittest

from helpers import sample          # noqa: F401  (adds project root to sys.path)
from analyzer import metrics as M
from collectors.cpu_monitor import CpuMonitor
from collectors.disk_monitor import DiskMonitor, device_rates
from collectors.memory_monitor import MemoryMonitor
from collectors.psi import PsiReader


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def psi_text(some_total, full_total=0, a10=1.0):
    return (f"some avg10={a10:.2f} avg60=0.50 avg300=0.10 total={some_total}\n"
            f"full avg10=0.25 avg60=0.10 avg300=0.05 total={full_total}\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def p(self, *a):
        return os.path.join(self.d, *a)


class PsiParsing(unittest.TestCase):
    def test_detail_fields(self):
        d = M.parse_psi_detail(psi_text(5_000_000, 2_000_000, 12.5))
        self.assertEqual(d["some"]["avg10"], 12.5)
        self.assertEqual(d["full"]["total"], 2_000_000)

    def test_cpu_file_without_full_line(self):
        d = M.parse_psi_detail("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        self.assertNotIn("full", d)

    def test_malformed_raises_and_reader_returns_none(self):
        with self.assertRaises(ValueError):
            M.parse_psi_detail("garbage")
        self.assertIsNone(M.read_psi_detail("cpu", "/nonexistent"))

    def test_legacy_parse_psi_unchanged(self):
        self.assertEqual(M.parse_psi("some avg10=1.50 avg60=0 avg300=0 total=1\n"
                                     "full avg10=0.75 avg60=0 avg300=0 total=1\n"), {"some": 1.5, "full": 0.75})

    def test_interval_percent_is_exact_and_robust(self):
        self.assertAlmostEqual(M.psi_interval_pct(0, 500_000, 2.0), 25.0)      # 0.5 s stalled in 2 s
        self.assertIsNone(M.psi_interval_pct(900, 100, 1.0))                     # counter reset
        self.assertIsNone(M.psi_interval_pct(0, 100, 0))                         # bad dt
        self.assertIsNone(M.psi_interval_pct(None, 100, 1.0))
        self.assertEqual(M.psi_interval_pct(0, 10**12, 1.0), 100.0)              # clamped


class PsiReaderTests(Base):
    def test_interval_from_totals_and_unavailable_handling(self):
        w(self.p("cpu"), psi_text(1_000_000, 0))
        r = PsiReader("cpu", self.d)
        w(self.p("cpu"), psi_text(1_600_000, 0))
        s = r.sample(2.0)
        self.assertAlmostEqual(s["psi_cpu_some_pct"], 30.0)
        self.assertTrue(s["psi_cpu_available"])
        self.assertEqual(s["psi_cpu"], 1.0)
        os.remove(self.p("cpu"))
        s = r.sample(2.0)
        self.assertFalse(s["psi_cpu_available"])
        self.assertIsNone(s["psi_cpu"])
        self.assertIsNone(s["psi_cpu_some_pct"])

    def test_reader_with_no_psi_at_all(self):
        s = PsiReader("memory", "/nonexistent").sample(1.0)
        self.assertFalse(s["psi_mem_available"])
        self.assertTrue(all(v in (None, False) for v in s.values()))


class FakeProc(Base):
    def stat(self, user, system, idle, ctxt=0, running=1):
        w(self.p("stat"), f"cpu  {user} 0 {system} {idle} 0 0 0 0 0 0\nctxt {ctxt}\nprocs_running {running}\nprocs_blocked 0\n")

    def sched(self, run_ns, wait_ns):
        w(self.p("schedstat"), f"version 15\ntimestamp 1\ncpu0 0 0 0 0 0 0 {run_ns} {wait_ns} 5\ncpu1 0 0 0 0 0 0 {run_ns} {wait_ns} 5\n")   # per-CPU values; totals are summed


class CpuMonitorTests(FakeProc):
    def test_schedstat_run_queue_and_ctx_rate(self):
        self.stat(100, 0, 900, ctxt=1000)
        self.sched(0, 0)
        w(self.p("loadavg"), "3.00 2.00 1.00 1/100 5\n")
        m = CpuMonitor(self.d)
        m.cores = 2
        self.stat(300, 0, 900, ctxt=5000, running=3)
        self.sched(1_000_000_000, 2_000_000_000)           # per CPU: 1 s running + 2 s waiting -> totals 2 s / 4 s
        s = m.sample(2.0)
        self.assertAlmostEqual(s["runq_wait_per_core"], 1.0)         # 4s wait / 2s / 2 cores
        self.assertAlmostEqual(s["runnable_per_core"], 1.5)
        self.assertEqual(s["runq_source"], "schedstat")
        self.assertEqual(s["ctx_switches_s"], 2000.0)
        self.assertEqual(s["load1"], 3.0)
        self.assertAlmostEqual(s["cpu_percent"], 100.0)

    def test_without_schedstat_falls_back_to_procs_running(self):
        self.stat(0, 0, 100)
        m = CpuMonitor(self.d)
        self.stat(10, 0, 190, running=4)
        s = m.sample(1.0)
        self.assertIsNone(s["runq_wait_per_core"])
        self.assertEqual(s["runq_source"], "procs_running")
        self.assertEqual(s["procs_running"], 4)
        self.assertFalse(s["psi_cpu_available"])                      # no pressure dir: graceful

    def test_ctx_counter_reset_is_unavailable_not_negative(self):
        self.stat(0, 0, 100, ctxt=9000)
        m = CpuMonitor(self.d)
        self.stat(1, 0, 101, ctxt=10)
        self.assertIsNone(m.sample(1.0)["ctx_switches_s"])


class MemoryMonitorTests(Base):
    MI = ("MemTotal: 8000000 kB\nMemFree: 500000 kB\nMemAvailable: 4000000 kB\nBuffers: 100000 kB\n"
          "Cached: 3000000 kB\nSReclaimable: 200000 kB\nSwapTotal: 1000000 kB\nSwapFree: 900000 kB\n")

    def test_available_based_percent_vs_naive_free(self):
        w(self.p("meminfo"), self.MI)
        w(self.p("vmstat"), "pswpin 0\npswpout 0\npgmajfault 10\n")
        m = MemoryMonitor(self.d)
        w(self.p("vmstat"), "pswpin 100\npswpout 250\npgmajfault 30\n")
        s = m.sample(2.0)
        self.assertAlmostEqual(s["mem_percent"], 50.0)                # (total-available)/total
        self.assertAlmostEqual(s["mem_naive_used_percent"], 93.75)    # what 'free' would claim
        self.assertAlmostEqual(s["mem_reclaimable_mb"], (3000000 + 100000 + 200000) / 1024)
        self.assertAlmostEqual(s["swap_percent"], 10.0)
        from collectors.memory_monitor import PAGE
        self.assertAlmostEqual(s["swap_in_s"], 100 * PAGE / 2.0)          # pages -> bytes per second
        self.assertAlmostEqual(s["swap_out_s"], 250 * PAGE / 2.0)
        self.assertAlmostEqual(s["major_faults_s"], 10.0)

    def test_vmstat_counter_reset_gives_none(self):
        w(self.p("meminfo"), self.MI)
        w(self.p("vmstat"), "pswpin 500\npswpout 500\npgmajfault 500\n")
        m = MemoryMonitor(self.d)
        w(self.p("vmstat"), "pswpin 1\npswpout 1\npgmajfault 1\n")
        s = m.sample(1.0)
        self.assertIsNone(s["swap_in_s"])
        self.assertIsNone(s["major_faults_s"])

    def test_missing_proc_files_fall_back_without_crash(self):
        m = MemoryMonitor(self.d)                                      # nothing exists
        s = m.sample(1.0)
        self.assertTrue(0 <= s["mem_percent"] <= 100)
        self.assertFalse(s["psi_mem_available"])


DISK0 = ("   8  0 vda 100 0 2000 50 100 0 4000 80 0 400 130\n"
         "   8  1 vda1 90 0 1800 40 90 0 3600 70 0 350 110\n"
         "   7  0 loop0 5 0 10 1 0 0 0 0 0 1 1\n"
         " 253  0 dm-0 100 0 2000 50 100 0 4000 80 0 400 130\n"
         "   8 16 sdb 0 0 0 0 0 0 0 0 0 0 0\n")


class DiskTests(Base):
    def tree(self):
        for n in ("vda", "dm-0", "sdb", "loop0"):
            os.makedirs(self.p("sys", n), exist_ok=True)
        os.makedirs(self.p("sys", "vda", "holders"), exist_ok=True)
        w(self.p("sys", "vda", "holders", "dm-0"), "")                 # dm-0 sits on top of vda
        os.makedirs(self.p("sys", "dm-0", "holders"), exist_ok=True)
        return self.p("sys")

    def test_device_rates_math(self):
        a = dict(reads=0, rd_sectors=0, rd_ms=0, writes=0, wr_sectors=0, wr_ms=0, in_flight=0, io_ticks_ms=0, weighted_ms=0)
        b = dict(reads=100, rd_sectors=2048, rd_ms=500, writes=100, wr_sectors=4096, wr_ms=1500, in_flight=3,
                 io_ticks_ms=1000, weighted_ms=6000)
        r = device_rates(a, b, 2.0)
        self.assertAlmostEqual(r["util"], 50.0)                         # 1 s busy in 2 s
        self.assertAlmostEqual(r["await_ms"], 10.0)                     # 2000 ms / 200 IOs
        self.assertAlmostEqual(r["queue"], 3.0)                         # 6000 ms / 2000 ms
        self.assertAlmostEqual(r["read_mbs"], 2048 * 512 / 2 / 2**20)
        self.assertAlmostEqual(r["iops"], 100.0)

    def test_utilisation_never_exceeds_100(self):
        a = dict(reads=0, rd_sectors=0, rd_ms=0, writes=0, wr_sectors=0, wr_ms=0, in_flight=0, io_ticks_ms=0, weighted_ms=0)
        b = dict(a, io_ticks_ms=5000)                                   # 5 s of ticks in a 2 s interval
        self.assertEqual(device_rates(a, b, 2.0)["util"], 100.0)

    def test_counter_reset_or_bad_dt_returns_none(self):
        a = dict(reads=50, rd_sectors=0, rd_ms=0, writes=0, wr_sectors=0, wr_ms=0, in_flight=0, io_ticks_ms=0, weighted_ms=0)
        self.assertIsNone(device_rates(a, dict(a, reads=10), 1.0))
        self.assertIsNone(device_rates(a, a, 0))

    def test_selection_aggregation_and_no_double_counting(self):
        w(self.p("diskstats"), DISK0)
        m = DiskMonitor(self.d, self.tree())
        w(self.p("diskstats"),
          "   8  0 vda 200 0 4000 150 200 0 8000 280 0 1400 630\n"
          "   8  1 vda1 190 0 3800 140 190 0 7600 270 0 1350 610\n"
          "   7  0 loop0 9 0 99 1 0 0 0 0 0 9 9\n"
          " 253  0 dm-0 200 0 4000 150 200 0 8000 280 0 1400 630\n"
          "   8 16 sdb 0 0 0 0 0 0 0 0 0 0 0\n")
        s = m.sample(2.0)
        names = {d["name"] for d in s["devices"]}
        self.assertEqual(names, {"vda", "dm-0", "sdb"})                # no partition, no loop
        vda = next(d for d in s["devices"] if d["name"] == "vda")
        self.assertFalse(vda["top_layer"])
        self.assertAlmostEqual(vda["util"], 50.0)                       # 1000 ms / 2000 ms
        self.assertAlmostEqual(s["disk_util"], 50.0)                    # MAX over devices
        one_layer = 4000 * 512 / 2 / 2**20                              # dm-0 only: 4000 new sectors / 2 s
        self.assertAlmostEqual(s["disk_write_mbs"], one_layer)
        self.assertLessEqual(s["disk_util"], 100)

    def test_stats_without_sysfs_skip_partitions_by_name(self):
        w(self.p("diskstats"), DISK0)
        m = DiskMonitor(self.d, self.p("no-such-sys"))
        self.assertNotIn("vda1", m._prev)
        self.assertNotIn("loop0", m._prev)

    def test_missing_diskstats_is_graceful(self):
        s = DiskMonitor(self.d, self.p("sys")).sample(1.0)
        self.assertEqual(s["devices"], [])
        self.assertIsNone(s["disk_util"])


class JsonAndCsv(unittest.TestCase):
    def test_strict_json_everywhere(self):
        obj = {"a": math.nan, "b": [math.inf, -math.inf, 1], "c": {"d": float("nan")}, "e": None, "f": (1, math.nan)}
        txt = M.dumps_json(obj)
        back = json.loads(txt, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        self.assertEqual(back, {"a": None, "b": [None, None, 1], "c": {"d": None}, "e": None, "f": [1, None]})
        with self.assertRaises(ValueError):
            json.dumps(obj, allow_nan=False)                              # shows plain json would fail

    def test_helpers(self):
        self.assertIsNone(M.safe_number(math.nan))
        self.assertEqual(M.safe_number(3), 3)
        self.assertFalse(M.is_num(True))
        self.assertEqual(M.csv_value(math.nan), "")
        self.assertEqual(M.csv_value(None), "")
        self.assertEqual(M.csv_value(0), 0)


if __name__ == "__main__":
    unittest.main()
