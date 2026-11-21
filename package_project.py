"""Project Packaging and Integrity Verification Script for Linux Bottleneck Detective V2.

Verifies all components, checks file integrity, preserves experiment results,
and builds a clean distribution archive with SHA256 checksums.
"""
import hashlib
import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"

ESSENTIAL_FILES = [
    "GUIDE.md",
    "README.md",
    "main.py",
    "engine.py",
    "experiment.py",
    "campaign_runner.py",
    "requirements.txt",
    "config.example.json",
    "collectors/cpu_monitor.py",
    "collectors/disk_monitor.py",
    "collectors/memory_monitor.py",
    "collectors/process_monitor.py",
    "collectors/psi.py",
    "analyzer/bottleneck_detector.py",
    "analyzer/baseline.py",
    "analyzer/attribution.py",
    "analyzer/diagnosis.py",
    "analyzer/metrics.py",
    "analyzer/validation.py",
    "dashboard/app.py",
    "dashboard/data.py",
    "experiments/runner.py",
    "experiments/workloads.py",
    "experiments/worker.py",
    "experiments/evaluation.py",
    "experiments/report.py",
    "reports/report_generator.py",
    "reports/summary.md",
    "reports/COMPREHENSIVE_REPORT.md",
    "reports/PRESENTATION_SLIDES.md",
    "reports/VIVA_DEFENSE_GUIDE.md",
    "docs/DETECTION_MODEL.md",
    "docs/VIVA.md",
    "data/experiments/suite.md",
    "data/experiments/suite.json",
    "tests/test_campaign_runner.py"
]

EXCLUDE_PATTERNS = [
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".automation_state",
    "dist"
]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_integrity():
    print("=" * 70)
    print(" VERIFYING ESSENTIAL PROJECT FILES AND ARTIFACTS")
    print("=" * 70)
    missing = []
    for rel in ESSENTIAL_FILES:
        p = ROOT / rel
        if not p.exists():
            print(f"  [MISSING] {rel}")
            missing.append(rel)
        else:
            sz = p.stat().st_size
            print(f"  [OK] {rel:<45} ({sz:,} bytes)")

    if missing:
        print(f"\nERROR: {len(missing)} essential file(s) are missing!")
        return False

    print("\nAll essential project files verified successfully.")
    return True


def should_exclude(tarinfo):
    name = tarinfo.name
    for pat in EXCLUDE_PATTERNS:
        parts = Path(name).parts
        if pat in parts or any(p.startswith(pat) for p in parts if p != ".gitignore"):
            return None
        if name.endswith(".pyc") or name.endswith(".tmp"):
            return None
    return tarinfo


def build_package():
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"linux-bottleneck-detective-v2-{stamp}.tar.gz"
    archive_path = DIST_DIR / archive_name

    print("\n" + "=" * 70)
    print(f" BUILDING DISTRIBUTION ARCHIVE: {archive_path.name}")
    print("=" * 70)

    with tarfile.open(archive_path, "w:gz") as tar:
        for item in sorted(ROOT.iterdir()):
            if item.name in EXCLUDE_PATTERNS or (item.name.startswith(".") and item.name != ".gitignore"):
                continue
            print(f"  Adding: {item.name}/" if item.is_dir() else f"  Adding: {item.name}")
            tar.add(item, arcname=f"Linux-Bottleneck-Detective-V2/{item.name}", filter=should_exclude)

    arch_size = archive_path.stat().st_size
    arch_hash = sha256_file(archive_path)

    checksum_file = DIST_DIR / "SHA256SUMS"
    lines = []
    for arc in sorted(DIST_DIR.glob("*.tar.gz")):
        lines.append(f"{sha256_file(arc)}  {arc.name}\n")
    with open(checksum_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"\nArchive generated successfully!")
    print(f"  Location : {archive_path}")
    print(f"  Size     : {arch_size:,} bytes ({arch_size / 2**20:.2f} MB)")
    print(f"  SHA256   : {arch_hash}")
    print(f"  Checksums: {checksum_file}")
    return archive_path


def main():
    if not verify_integrity():
        sys.exit(1)
    arch = build_package()
    print("\nProject packaging complete and ready for submission/evaluation.")
    return 0


if __name__ == "__main__":
    main()
