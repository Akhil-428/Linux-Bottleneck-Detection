"""Configuration validation.  Every user-supplied number goes through here so a typo
produces a readable ConfigError instead of a crash (or silent nonsense) later."""
import json
import math


class ConfigError(ValueError):
    """Raised for invalid configuration; the message is meant for end users."""


# name: (default, min, max, description)
THRESHOLD_SPEC = {
    # --- CPU ---
    "cpu": (85, 1, 100, "CPU utilisation % that counts as 'high' (utilisation evidence)"),
    "cpu_critical": (95, 1, 100, "CPU utilisation % used for CRITICAL severity"),
    "load_per_core": (1.0, 0.1, 100, "load1 / cores treated as 'queueing' (supporting evidence)"),
    "psi_cpu": (10, 0.1, 100, "CPU PSI 'some' stall % (interval or avg10) = tasks waiting for CPU"),
    "psi_cpu_critical": (40, 0.1, 100, "CPU PSI 'some' % used for CRITICAL severity"),
    "runq_wait_per_core": (0.25, 0.01, 100, "avg tasks waiting for a CPU, per core (schedstat)"),
    "runnable_per_core": (1.25, 0.1, 100, "runnable tasks per core (procs_running fallback)"),
    "ctx_per_core": (20000, 100, 10_000_000, "context switches/s per core (supporting only)"),
    # --- memory ---
    "mem": (90, 1, 100, "% memory used (MemAvailable based) that counts as 'high'"),
    "mem_critical": (95, 1, 100, "% memory used for CRITICAL severity"),
    "mem_low": (80, 1, 100, "memory % above which swapping is suspicious"),
    "swap_out_bps": (1_000_000, 1, 1e12, "bytes/s swapped out"),
    "swap_in_bps": (262_144, 1, 1e12, "bytes/s swapped in (re-faulting = working set > RAM)"),
    "psi_mem": (10, 0.1, 100, "memory PSI 'some' stall %"),
    "psi_mem_full": (2, 0.1, 100, "memory PSI 'full' stall % (all non-idle tasks stalled)"),
    "psi_mem_critical": (30, 0.1, 100, "memory PSI 'some' % used for CRITICAL severity"),
    "major_faults_ps": (200, 1, 1e9, "major page faults/s (supporting only)"),
    # --- disk ---
    "disk_util": (80, 1, 100, "per-device %util that counts as 'busy'"),
    "disk_util_critical": (95, 1, 100, "per-device %util used for CRITICAL severity"),
    "disk_await_ms": (30, 0.1, 100000, "average I/O latency (ms) that counts as 'slow'"),
    "disk_queue": (2.0, 0.1, 100000, "average I/O queue depth (aqu-sz) that counts as 'queued'"),
    "psi_io": (20, 0.1, 100, "I/O PSI 'some' stall %"),
    "psi_io_full": (5, 0.1, 100, "I/O PSI 'full' stall %"),
    "psi_io_critical": (50, 0.1, 100, "I/O PSI 'some' % used for CRITICAL severity"),
    "iowait": (20, 0.1, 100, "iowait % of CPU time (supporting evidence)"),
    # --- per process ---
    "proc_cpu": (80, 1, 100000, "single process CPU % (100 = one core)"),
    "proc_mem": (30, 0.1, 100, "single process % of RAM"),
    "attr_min_cpu": (20, 1, 100000, "min CPU % (of one core) for a process to count as a contributor"),
    "attr_min_mem": (5, 0.1, 100, "min % of RAM for a process to count as a memory contributor"),
    "attr_min_io_bps": (1_000_000, 1, 1e12, "min bytes/s for a process to count as an I/O contributor"),
}
DEFAULT_THRESHOLDS = {k: v[0] for k, v in THRESHOLD_SPEC.items()}
_PAIRS = [("cpu", "cpu_critical"), ("mem", "mem_critical"), ("disk_util", "disk_util_critical"),
          ("psi_cpu", "psi_cpu_critical"), ("psi_mem", "psi_mem_critical"),
          ("psi_io", "psi_io_critical")]

BASELINE_DEFAULTS = {"enabled": True, "window": 300, "min_samples": 60, "z": 4.0,
                     "adopt_after": 900, "path": None}


def _num(name, v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ConfigError(f"{what} '{name}' must be a finite number, got {v!r}")
    if not lo <= v <= hi:
        raise ConfigError(f"{what} '{name}' must be between {lo} and {hi}, got {v}")
    return v


def validate_thresholds(overrides=None):
    """Return the merged thresholds, or raise ConfigError with the exact problem."""
    overrides = overrides or {}
    unknown = sorted(set(overrides) - set(THRESHOLD_SPEC))
    if unknown:
        raise ConfigError(f"Unknown threshold(s): {', '.join(unknown)}. "
                          f"Valid names: {', '.join(sorted(THRESHOLD_SPEC))}")
    merged = dict(DEFAULT_THRESHOLDS)
    for k, v in overrides.items():
        _, lo, hi, _d = THRESHOLD_SPEC[k]
        merged[k] = _num(k, v, lo, hi, "Threshold")
    for lo_k, hi_k in _PAIRS:
        if merged[hi_k] < merged[lo_k]:
            raise ConfigError(f"Threshold '{hi_k}' ({merged[hi_k]}) must not be lower than "
                              f"'{lo_k}' ({merged[lo_k]})")
    return merged


def validate_window(window, required):
    for n, v in (("window", window), ("required", required)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError(f"Detection {n} must be an integer, got {v!r}")
    if not 1 <= window <= 1000:
        raise ConfigError(f"Detection window must be between 1 and 1000 samples, got {window}")
    if not 1 <= required <= window:
        raise ConfigError(f"Persistence requirement must be between 1 and the window size "
                          f"({window}), got {required}")
    return window, required


def validate_interval(interval):
    _num("interval", interval, 0.5, 300, "Sampling")
    return float(interval)


def validate_baseline(cfg=None):
    cfg = {**BASELINE_DEFAULTS, **(cfg or {})}
    unknown = sorted(set(cfg) - set(BASELINE_DEFAULTS))
    if unknown:
        raise ConfigError(f"Unknown baseline option(s): {', '.join(unknown)}")
    if not isinstance(cfg["enabled"], bool):
        raise ConfigError("Baseline 'enabled' must be true or false")
    for k, lo, hi in (("window", 10, 100000), ("min_samples", 5, 100000),
                      ("z", 1.0, 100), ("adopt_after", 0, 10_000_000)):
        _num(k, cfg[k], lo, hi, "Baseline")
    if cfg["min_samples"] > cfg["window"]:
        raise ConfigError(f"Baseline min_samples ({cfg['min_samples']}) cannot exceed the "
                          f"baseline window ({cfg['window']})")
    return cfg


def validate_experiment(kind, duration, baseline_s, recovery_s, workers=None, memory_pct=None,
                        disk_mb=None, max_workers=256):
    """Bounds for controlled experiments (see experiments/ for the safety rationale)."""
    if kind not in ("cpu", "memory", "disk", "cpu-benign", "control"):
        raise ConfigError(f"Unknown experiment '{kind}'. Choose cpu, memory, disk, "
                          "cpu-benign or control")
    _num("duration", duration, 5, 600, "Experiment")
    _num("baseline", baseline_s, 5, 600, "Experiment")
    _num("recovery", recovery_s, 5, 900, "Experiment")
    if workers is not None:
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= max_workers:
            raise ConfigError(f"Experiment workers must be an integer from 1 to {max_workers}, got {workers!r}")
    if memory_pct is not None:
        _num("memory_pct", memory_pct, 5, 95, "Experiment (percent of *available* RAM)")
    if disk_mb is not None:
        _num("disk_mb", disk_mb, 16, 4096, "Experiment")


def load_config_file(path):
    """Load an optional JSON config: {"interval":2,"window":5,"required":3,
    "thresholds":{...},"baseline":{...}}.  Unknown top-level keys are rejected."""
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e.strerror or e}")
    except ValueError as e:
        raise ConfigError(f"Config file {path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ConfigError("Config file must contain a JSON object")
    unknown = sorted(set(data) - {"interval", "window", "required", "thresholds", "baseline"})
    if unknown:
        raise ConfigError(f"Unknown config key(s): {', '.join(unknown)}")
    return data
