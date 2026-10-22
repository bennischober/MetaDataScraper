"""Resource politeness: idle-priority children and self-monitoring.

ShrinkRay is a guest on machines that do other things (a desktop today,
a small media server tomorrow). These helpers keep it a polite guest:

- long-running children (encodes, probes, VMAF measurements) run below
  normal priority so they yield instantly to interactive work
- the resident app watches its own memory so "we stay lean" stays a
  measured fact instead of an assumption

No third-party dependencies: priority is a creation flag (Windows) or
process niceness (POSIX), memory is read from psapi (Windows) or
/proc (Linux).
"""

import contextlib
import os
import subprocess
import sys

# module state: politeness is on by default; apply() can disable it
_IDLE_PRIORITY = True

# windows: priority classes are per-child creation flags; POSIX children
# inherit the process niceness set by nice_self() instead
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", None)


def apply(config) -> None:
    """Applies the resource policy from a config (Conf) object.

    Called once from load_config() so every entry point (CLI commands,
    resident app, watch loop) inherits the same policy without each call
    site having to thread it through.

    Args:
        config: The config object (reads idle_priority).
    """
    global _IDLE_PRIORITY
    _IDLE_PRIORITY = bool(getattr(config, "idle_priority", True))
    if _IDLE_PRIORITY:
        nice_self()


def spawn_kwargs() -> dict:
    """Keyword arguments for spawning long-running children politely.

    On Windows this lowers the child's scheduling priority below normal so
    it yields to everything else on the machine. On POSIX children inherit
    the process niceness (set once via apply()/nice_self), so nothing is
    needed here. Use for the LONG children only (encodes, probes,
    measurements) — short ffprobes stay normal priority. Honors the
    idle_priority config (off => empty kwargs).

    Returns:
        dict: kwargs to splat into subprocess.run / subprocess.Popen
            (empty when politeness is disabled or on POSIX).
    """
    if not _IDLE_PRIORITY:
        return {}
    if sys.platform == "win32" and _BELOW_NORMAL is not None:
        return {"creationflags": _BELOW_NORMAL}
    return {}


def nice_self(increment: int = 10) -> None:
    """Lowers this process's scheduling priority (POSIX niceness).

    Children spawned later inherit the niceness, so every child of the
    process runs politely. On Windows this is a no-op (per-child priority
    comes from spawn_kwargs instead). Failures are ignored: politeness
    must never break the actual work.

    Args:
        increment (int): The niceness increment (10 = low background work).
    """
    if sys.platform == "win32":
        return
    nice = getattr(os, "nice", None)
    if nice is None:
        return
    with contextlib.suppress(OSError):
        nice(increment)  # raising niceness is always allowed; guard anyway


def rss_mib() -> float | None:
    """Returns this process's resident memory in MiB (None if unknown).

    A monitoring number, not a promise: the resident set is what the OS
    keeps in RAM right now. Read from psapi on Windows and /proc on Linux
    (getrusage peak as the POSIX fallback, e.g. macOS).

    Returns:
        float | None: Resident set size in MiB, None when unreadable.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                """The psapi structure for GetProcessMemoryInfo."""

                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.windll.psapi
            # declare types: without argtypes the pseudo-handle (-1) is not
            # marshalled as a real HANDLE on 64-bit => error 6, no data
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = wintypes.HANDLE(-1)  # GetCurrentProcess() pseudo-handle
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / (1024 * 1024)
            return None
        # POSIX: VmRSS from /proc is the honest current number (Linux)
        try:
            with open("/proc/self/status", encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024
        except OSError:
            pass
        # fallback (e.g. macOS): getrusage peak (KiB on Linux, bytes on macOS)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return peak / (1024 * 1024)
        return peak / 1024
    except (OSError, ValueError):
        return None
