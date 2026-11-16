import os
import shutil
import tempfile
import unittest

from helpers import sample          # noqa: F401
from reports.report_generator import generate


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def w(self, name, text):
        with open(os.path.join(self.d, name), "w") as f:
            f.write(text)

    def test_v1_files_still_produce_a_report(self):
        self.w("system_logs.csv", "timestamp,cpu_percent,iowait_percent,load1,mem_percent,swap_percent,"
               "disk_read_mbs,disk_write_mbs,disk_util,disk_await_ms\n2026-01-01 00:00:00,10,0,0.1,20,0,0,0,1,1\n"
               "2026-01-01 00:00:02,30,0,0.2,22,0,0,0,2,1\n")
        self.w("alerts.csv", "timestamp,resource,severity,diagnosis,recommendation\n2026-01-01 00:00:02,CPU,WARNING,x,y\n")
        out = os.path.join(self.d, "r.md")
        generate(self.d, out)
        with open(out) as f:
            txt = f.read()
        self.assertIn("| cpu_percent | 20.0 | 30.0 |", txt)
        self.assertIn("UNSPECIFIED", txt)

    def test_v2_unavailable_metrics_shown_as_na_and_elevated_not_counted_as_contention(self):
        self.w("system_logs.csv", "timestamp,cpu_percent,psi_cpu_some_pct,cpu_state\n"
               "2026-01-01 00:00:00,10,,NORMAL\n2026-01-01 00:00:02,99,,ELEVATED\n")
        self.w("alerts.csv", "timestamp,resource,severity,diagnosis,recommendation,state,confidence,method,device,"
               "duration_s,likely_cause,responsible,evidence\n"
               "2026-01-01 00:00:02,CPU,INFO,busy,none,ELEVATED,LOW,static,,2,,,\n")
        out = os.path.join(self.d, "r.md")
        generate(self.d, out)
        with open(out) as f:
            txt = f.read()
        self.assertIn("| psi_cpu_some_pct | n/a | n/a |", txt)
        self.assertIn("No contention (POTENTIAL/CONFIRMED) was diagnosed", txt)
        self.assertIn("ELEVATED 50%", txt)


if __name__ == "__main__":
    unittest.main()
