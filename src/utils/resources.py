"""
Resource detection and configuration for low-RAM hosting.

Reads environment variables:
  MAX_CONCURRENT_ENCODINGS  – integer, or "auto" (default)
  FFMPEG_THREADS            – integer, or "auto" (default)
  LOW_MEMORY_MODE           – "true"/"false"/"auto" (default)
  MAX_RAM_MB                – integer override, or "auto" (default)
  TEMP_DIR                  – path (default: src/bin/tmp)
  PROGRESS_UPDATE_INTERVAL  – seconds between status edits (default: 4)
  MIN_FREE_DISK_MB          – MB to keep free before starting a job (default: 512)
"""

import os
import math

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _total_ram_mb() -> float:
    """Return total system RAM in MB, or 0 if unknown."""
    raw = os.getenv("MAX_RAM_MB", "auto").strip().lower()
    if raw != "auto":
        try:
            return float(raw)
        except ValueError:
            pass
    if _HAS_PSUTIL:
        return psutil.virtual_memory().total / (1024 * 1024)
    return 0.0


def _cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def detect_max_concurrent_encodings() -> int:
    """
    Return the maximum number of simultaneous FFmpeg encode processes.
    Considers both RAM and CPU.
    """
    raw = os.getenv("MAX_CONCURRENT_ENCODINGS", "auto").strip().lower()
    if raw != "auto":
        try:
            v = int(raw)
            return max(1, v)
        except ValueError:
            pass

    ram_mb = _total_ram_mb()
    cpus   = _cpu_count()

    # RAM-based ceiling
    if ram_mb > 0:
        if ram_mb <= 1200:
            ram_limit = 1
        elif ram_mb <= 2500:
            ram_limit = 1
        elif ram_mb <= 5000:
            ram_limit = 2
        else:
            ram_limit = max(2, math.floor(ram_mb / 2000))
    else:
        ram_limit = 1  # conservative when unknown

    # CPU-based ceiling: never run more encodes than half the cores
    cpu_limit = max(1, cpus // 2)

    return min(ram_limit, cpu_limit)


def detect_ffmpeg_threads() -> int:
    """
    Return the -threads value to pass to FFmpeg.
    0 = let FFmpeg decide (uses all cores) — bad on 1 GB hosts.
    """
    raw = os.getenv("FFMPEG_THREADS", "auto").strip().lower()
    if raw != "auto":
        try:
            v = int(raw)
            return max(0, v)
        except ValueError:
            pass

    ram_mb = _total_ram_mb()
    cpus   = _cpu_count()

    if ram_mb > 0 and ram_mb <= 1200:
        return min(2, cpus)
    if ram_mb > 0 and ram_mb <= 2500:
        return min(3, cpus)
    # Larger: let FFmpeg self-limit but cap at cpu_count
    return 0  # FFmpeg default (auto)


def is_low_memory_mode() -> bool:
    raw = os.getenv("LOW_MEMORY_MODE", "auto").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    ram_mb = _total_ram_mb()
    return (ram_mb > 0 and ram_mb <= 1200)


def get_temp_dir(default: str) -> str:
    return os.getenv("TEMP_DIR", default).strip() or default


def get_progress_interval() -> float:
    try:
        return float(os.getenv("PROGRESS_UPDATE_INTERVAL", "4"))
    except ValueError:
        return 4.0


def get_min_free_disk_mb() -> int:
    try:
        return int(os.getenv("MIN_FREE_DISK_MB", "512"))
    except ValueError:
        return 512


def check_disk_space(path: str, required_mb: int) -> tuple[bool, str]:
    """Return (ok, message). ok=False means not enough free space."""
    try:
        if _HAS_PSUTIL:
            usage = psutil.disk_usage(path)
            free_mb = usage.free / (1024 * 1024)
        else:
            st = os.statvfs(path)
            free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        if free_mb < required_mb:
            return False, (
                f"Insufficient disk space: {free_mb:.0f} MB free, "
                f"{required_mb} MB required."
            )
        return True, ""
    except Exception:
        return True, ""  # can't check → allow


def resource_summary() -> str:
    ram_mb  = _total_ram_mb()
    cpus    = _cpu_count()
    enc     = detect_max_concurrent_encodings()
    threads = detect_ffmpeg_threads()
    low_mem = is_low_memory_mode()
    return (
        f"RAM={ram_mb:.0f}MB CPUs={cpus} "
        f"max_enc={enc} ffmpeg_threads={threads} low_mem={low_mem}"
    )
