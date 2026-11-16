"""Safe, bounded workloads for controlled experiments.

plan_experiment()  validates parameters, enforces safety bounds and returns a plan
                   (raises ConfigError with an actionable message when unsafe)
Workload           starts the plan's processes, tracks their PIDs, and guarantees cleanup

Safety model
  * Only processes this module started are ever signalled (we kill the process groups we
    created, after checking the group id is our child's pid) - unrelated processes are never touched.
  * Every workload has a hard time limit enforced three ways: the supervisor, the worker's own
    deadline, and PR_SET_PDEATHSIG (kernel kills workers if the supervisor dies).
  * CPU: bounded worker count, nice 10.   Memory: at most 95% of *available* RAM and never below
    a free-memory floor; also respects cgroup limits.   Disk: one bounded file per worker, in a
    private directory, refused on tmpfs/ramfs and system paths, refused if free space is short.
  * No root needed.  (Root is only needed if you point --disk-path at a root-owned location.)
"""
import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import psutil

from analyzer.validation import ConfigError, validate_experiment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = ("/proc", "/sys", "/dev", "/boot", "/etc", "/bin", "/sbin", "/usr", "/lib", "/lib64",
             "/var/lib", "/run", "/root", "/srv")
RAM_FS = {"tmpfs", "ramfs", "devtmpfs"}
HARD_MEM_FRACTION = 95          # % of *available* RAM, absolute ceiling (floor below also applies)


def stress_ng_path():
    return shutil.which("stress-ng")


def fs_type(path, mounts="/proc/mounts"):
    """Filesystem type of the mount containing `path` (longest-prefix match)."""
    best, typ = "", None
    try:
        with open(mounts) as f:
            for line in f:
                p = line.split()
                if len(p) >= 3:
                    mp = p[1].replace("\\040", " ")
                    if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
                        best, typ = mp, p[2]
    except OSError:
        pass
    return typ


def cgroup_mem_headroom_mb():
    """Remaining memory under a cgroup limit (MB), or None when unlimited/unknown."""
    for lim, cur in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                     ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
                      "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            limit = open(lim).read().strip()
            if limit == "max" or int(limit) >= 2**60:
                continue
            return (int(limit) - int(open(cur).read().strip())) / 2**20
        except (OSError, ValueError):
            continue
    return None


def _check_disk_dir(path, size_mb, workers):
    real = os.path.realpath(path)
    if real == "/" or any(real == p or real.startswith(p + "/") for p in PROTECTED):
        raise ConfigError(f"Refusing to write experiment data under system path {real}. "
                          "Choose a scratch directory such as /var/tmp or your home directory.")
    if not os.path.isdir(real):
        raise ConfigError(f"Disk experiment directory {real} does not exist")
    if not os.access(real, os.W_OK):
        raise ConfigError(f"Disk experiment directory {real} is not writable by this user "
                          "(run as a user that owns it, or choose another --disk-path)")
    ft = fs_type(real)
    if ft in RAM_FS:
        raise ConfigError(f"{real} is on {ft} (RAM). Writing there would stress memory, not disk. "
                          "Use --disk-path on a real disk, e.g. /var/tmp.")
    st = shutil.disk_usage(real)
    need = size_mb * workers * 2**20
    reserve = max(2**30, st.free // 4)           # never use more than ~75% of the free space
    if st.free - need < reserve:
        raise ConfigError(f"Not enough free space on {real}: {st.free / 2**30:.1f} GiB free, "
                          f"experiment needs {need / 2**30:.2f} GiB and a {reserve / 2**30:.1f} GiB "
                          "reserve must remain. Lower --disk-mb/--workers or choose another disk.")
    return real, ft


def plan_experiment(kind, duration=30, workers=None, memory_pct=None, disk_mb=None, disk_path=None,
                    backend="auto", baseline_s=20, recovery_s=30):
    """Validate and resolve an experiment into a concrete, bounded plan."""
    validate_experiment(kind, duration, baseline_s, recovery_s, workers, memory_pct, disk_mb)
    if backend not in ("auto", "stress-ng", "builtin"):
        raise ConfigError("--backend must be auto, stress-ng or builtin")
    cores = os.cpu_count() or 1
    plan = {"kind": kind, "duration": duration, "cores": cores, "warnings": [], "notes": []}
    ng = stress_ng_path()
    if backend == "stress-ng" and not ng:
        raise ConfigError("stress-ng was requested but is not installed (sudo apt install stress-ng); "
                          "use --backend builtin (the default 'auto' falls back to it).")
    use_ng = (backend == "stress-ng") or (backend == "auto" and ng)
    if kind == "control":
        plan.update(backend="none", workers=0)
        plan["notes"].append("Control run: NO workload is started; measures false positives on a quiet system.")
        return plan
    plan["backend"] = "stress-ng" if use_ng else "builtin"
    if not ng and backend == "auto":
        plan["notes"].append("stress-ng not found: using the built-in Python workload generator.")
    if kind in ("cpu", "cpu-benign"):
        default = cores if kind == "cpu-benign" else min(2 * cores, 128)
        plan["workers"] = workers or default
        if kind == "cpu":
            plan["notes"].append(f"{plan['workers']} CPU-bound workers on {cores} core(s): demand "
                                 + ("exceeds" if plan["workers"] > cores else "does NOT exceed") + " CPU capacity.")
        else:
            plan["notes"].append("Negative control: one CPU-bound worker per core keeps utilisation ~100% "
                                 "WITHOUT oversubscription. A correct engine reports at most ELEVATED here.")
    elif kind == "memory":
        vm = psutil.virtual_memory()
        avail = vm.available / 2**20
        head = cgroup_mem_headroom_mb()
        if head is not None and head < avail:
            avail = head
            plan["notes"].append(f"cgroup memory limit detected: only {head:.0f} MB headroom.")
        pct = memory_pct or 80
        floor = max(256, int(vm.total / 2**20 * 0.05))
        target = int(min(avail * pct / 100, avail * HARD_MEM_FRACTION / 100, avail - floor))
        if target < 64:
            raise ConfigError(f"Only {avail:.0f} MB available (safety floor {floor} MB): too little headroom "
                              "for a memory experiment on this machine.")
        plan.update(workers=1, memory_mb=target, floor_mb=floor, available_mb=int(avail), churn_mb=0)
        churn = 512
        try:                                                 # cache churn needs a safe real-disk directory
            base, _ft = _check_disk_dir(disk_path or tempfile.gettempdir(), churn, 1)
            plan.update(churn_mb=churn, disk_base=base)
            plan["notes"].append(f"Memory stress adds a bounded {churn} MB file-cache churn in {base} so that "
                                 "reclaim stalls are actually produced (private file, deleted afterwards).")
        except ConfigError as e:
            plan["notes"].append(f"File-cache churn skipped ({e}); the stress may only raise utilisation.")
        plan["warnings"].append(f"Will allocate up to {target} MB ({pct}% of {avail:.0f} MB available), stopping "
                                f"if free memory would drop below {floor} MB. Swapping may occur; use a "
                                "non-production machine or VM.")
        if pct > 90:
            plan["warnings"].append("Above 90% the machine may become sluggish and the OOM killer could act "
                                    "(the worker is its preferred victim). Only do this on a disposable system.")
        plan["notes"].append("On a machine with ample RAM and no swap, 80% may raise utilisation WITHOUT any "
                             "kernel-reported stall; that is correct engine behaviour and the report will say "
                             "the stress was ineffective rather than blame the detector.")
        if ng is None or not use_ng:
            plan["notes"].append("Memory worker is marked first-to-kill for the OOM killer (oom_score_adj=1000).")
    elif kind == "disk":
        w = workers or 2
        if w > 8:
            raise ConfigError("Disk experiments allow at most 8 workers")
        mb = int(disk_mb or 256)
        base = disk_path or tempfile.gettempdir()
        real, ft = _check_disk_dir(base, mb, w)
        plan.update(workers=w, disk_mb=mb, disk_base=real, disk_fs=ft)
        plan["warnings"].append(f"Will repeatedly write/fsync up to {mb} MB x {w} worker(s) inside a private "
                                f"directory under {real} ({ft}); files are deleted afterwards.")
    return plan


class Workload:
    """Supervises the processes of a plan.  stop() is idempotent and always safe to call."""

    def __init__(self, plan):
        self.plan = plan
        self.procs = []
        self.seen_pids = set()
        self.tmpdir = None
        self.started_at = None
        self._atexit = False

    # ---- command lines ---------------------------------------------------
    def _commands(self):
        p, d = self.plan, self.plan["duration"]
        kind = p["kind"]
        if p["backend"] == "stress-ng":
            ng = stress_ng_path()
            t = ["--timeout", f"{int(d)}s"]
            if kind in ("cpu", "cpu-benign"):
                return [[ng, "--cpu", str(p["workers"])] + t]
            if kind == "memory":
                return [[ng, "--vm", "1", "--vm-bytes", f"{p['memory_mb']}M", "--vm-keep"] + t]
            return [[ng, "--hdd", str(p["workers"]), "--hdd-bytes", f"{p['disk_mb']}M",
                     "--temp-path", self.tmpdir] + t]
        py = [sys.executable, "-m", "experiments.worker"]
        if kind in ("cpu", "cpu-benign"):
            return [py + ["cpu", "--seconds", str(d)] for _ in range(p["workers"])]
        if kind == "memory":
            cmd = py + ["memory", "--seconds", str(d), "--mb", str(p["memory_mb"]), "--floor-mb", str(p["floor_mb"])]
            if p.get("churn_mb") and self.tmpdir:
                cmd += ["--dir", self.tmpdir, "--churn-mb", str(p["churn_mb"])]
            return [cmd]
        return [py + ["disk", "--seconds", str(d), "--dir", self.tmpdir, "--mb", str(p["disk_mb"])]
                for _ in range(p["workers"])]

    def start(self):
        if self.procs:
            raise RuntimeError("workload already started")
        if not self._atexit:
            atexit.register(self.stop)            # last line of defence on interpreter exit
            self._atexit = True
        if self.plan["kind"] == "disk" or (self.plan["kind"] == "memory" and self.plan.get("churn_mb")):
            self.tmpdir = tempfile.mkdtemp(prefix="bd-exp-", dir=self.plan["disk_base"])
        try:
            for cmd in self._commands():
                kw = dict(cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
                if self.plan["backend"] == "stress-ng":
                    kw["preexec_fn"] = lambda: os.nice(10)
                self.procs.append(subprocess.Popen(cmd, **kw))
        except Exception:
            self.stop()
            raise
        self.started_at = time.time()
        self.poll_pids()

    def poll_pids(self):
        """All PIDs belonging to our workload (children included), accumulated over the run."""
        for pr in self.procs:
            try:
                parent = psutil.Process(pr.pid)
                self.seen_pids.add(pr.pid)
                for c in parent.children(recursive=True):
                    self.seen_pids.add(c.pid)
            except psutil.Error:
                pass
        return set(self.seen_pids)

    def running(self):
        return any(pr.poll() is None for pr in self.procs)

    def stop(self, grace=3.0):
        """SIGTERM our process groups, wait, SIGKILL leftovers, remove temp files."""
        self.poll_pids()
        for pr in self.procs:
            self._signal(pr, signal.SIGTERM)
        end = time.monotonic() + grace
        while time.monotonic() < end and (self.running() or self.leftover_pids()):
            time.sleep(0.05)
        for pr in self.procs:                         # stragglers (incl. grandchildren in our group)
            self._signal(pr, signal.SIGKILL)
        for pr in self.procs:
            try:
                pr.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        end = time.monotonic() + 2.0                  # SIGKILL is asynchronous: wait until really gone
        while time.monotonic() < end and self.leftover_pids():
            time.sleep(0.02)
        if self.tmpdir and os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.tmpdir = None

    @staticmethod
    def _signal(pr, sig):
        """Signal the process group we created for `pr` - verified to be its own group."""
        try:
            if os.getpgid(pr.pid) == pr.pid:
                os.killpg(pr.pid, sig)
            else:
                pr.send_signal(sig)               # never signal a group we did not create
        except (ProcessLookupError, PermissionError):
            pass

    def leftover_pids(self):
        """PIDs from this workload that are still alive (should be empty after stop())."""
        alive = set()
        for pid in self.seen_pids:
            try:
                p = psutil.Process(pid)
                if p.status() != psutil.STATUS_ZOMBIE:
                    alive.add(pid)
            except psutil.Error:
                pass
        return alive
