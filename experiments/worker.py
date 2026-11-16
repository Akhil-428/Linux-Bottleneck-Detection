"""Self-limiting workload process.  Launched only by experiments.workloads.Workload:

    python -m experiments.worker cpu     --seconds 30
    python -m experiments.worker memory  --seconds 30 --mb 512 --floor-mb 400 [--dir D --churn-mb 512]
    python -m experiments.worker disk    --seconds 30 --dir DIR --mb 256

Safety built into the worker itself (independent of the supervisor):
  * hard deadline: exits by itself after --seconds (+ small grace) even if orphaned
  * PR_SET_PDEATHSIG: the kernel kills it if the supervisor dies (Linux)
  * low priority (nice 10); memory worker sets oom_score_adj=1000 so the OOM killer picks
    it first, and stops growing if MemAvailable would fall below --floor-mb
  * disk worker rewrites ONE bounded file inside its own directory and deletes it on exit
"""
import argparse
import ctypes
import os
import signal
import sys
import time

_stop = False


def _on_term(signum, frame):
    global _stop
    _stop = True


def _harden(seconds):
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
        if os.getppid() == 1:                                               # parent already gone
            sys.exit(0)
    except (OSError, AttributeError):
        pass
    try:
        os.nice(10)
    except OSError:
        pass
    return time.monotonic() + seconds + 3.0


def _mem_available_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    return 1e9


def run_cpu(deadline):
    x = 1.0
    while not _stop and time.monotonic() < deadline:
        for _ in range(200_000):
            x = x * 1.0000001 + 1e-9
    return 0


def run_memory(deadline, mb, floor_mb, churn_dir=None, churn_mb=0):
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass
    chunks, chunk_mb, got = [], 16, 0
    while not _stop and got < mb and time.monotonic() < deadline:
        if _mem_available_mb() - chunk_mb < floor_mb:
            print(f"memory worker: stopping growth at {got} MB (safety floor {floor_mb} MB free)", flush=True)
            break
        b = bytearray(chunk_mb * 2**20)
        b[::4096] = b"\x01" * len(range(0, len(b), 4096))      # touch every page -> resident
        chunks.append(b)
        got += chunk_mb
    print(f"READY allocated={got}MB", flush=True)
    one = b"\x02"
    path = None
    if churn_dir and churn_mb > 0:
        # Anonymous memory alone causes no stall when nothing else needs RAM.  Re-reading a scratch
        # file larger than the remaining reclaimable room forces continuous reclaim + refaults, which
        # the kernel reports as memory stall (PSI).  Bounded: one file, private dir, deleted on exit.
        path = os.path.join(churn_dir, f"bd-churn-{os.getpid()}.bin")
        blk = os.urandom(2**20)
        with open(path, "wb") as fh:
            for _ in range(churn_mb):
                fh.write(blk)
            fh.flush()
            os.fsync(fh.fileno())
    n = 0
    try:
        while not _stop and time.monotonic() < deadline:
            if n % 4 == 0:
                for b in chunks:                               # keep the working set hot
                    b[::4096] = one * len(range(0, len(b), 4096))
                    if _stop or time.monotonic() >= deadline:
                        break
            n += 1
            if path:
                with open(path, "rb", buffering=0) as fh:
                    while not _stop and time.monotonic() < deadline and fh.read(2**20):
                        pass
            else:
                time.sleep(0.2)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    return 0


def run_disk(deadline, directory, mb):
    path = os.path.join(directory, f"bd-exp-{os.getpid()}.bin")
    block = os.urandom(4 * 2**20)
    nblocks = max(1, mb // 4)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        print(f"disk worker: cannot create file in {directory}: {e}", file=sys.stderr)
        return 3
    print("READY", flush=True)
    i = 0
    try:
        while not _stop and time.monotonic() < deadline:
            off = (i % nblocks) * len(block)
            os.pwrite(fd, block, off)
            os.fsync(fd)                                       # force the write to the device
            try:                                               # read an older block uncached
                rd = ((i * 7 + 3) % nblocks) * len(block)
                os.posix_fadvise(fd, rd, len(block), os.POSIX_FADV_DONTNEED)
                os.pread(fd, len(block), rd)
            except OSError:
                pass
            i += 1
    finally:
        os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["cpu", "memory", "disk"])
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--mb", type=int, default=0)
    ap.add_argument("--floor-mb", type=int, default=512)
    ap.add_argument("--dir")
    ap.add_argument("--churn-mb", type=int, default=0)
    a = ap.parse_args(argv)
    deadline = _harden(min(a.seconds, 900))
    if a.kind == "cpu":
        return run_cpu(deadline)
    if a.kind == "memory":
        return run_memory(deadline, a.mb, a.floor_mb, a.dir, a.churn_mb)
    return run_disk(deadline, a.dir, a.mb)


if __name__ == "__main__":
    sys.exit(main())
