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

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shrinkray.config import Conf

# module state: politeness is on by default; apply() can disable it
_IDLE_PRIORITY = True

# windows: priority classes are per-child creation flags; POSIX children
# inherit the process niceness set by nice_self() instead
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", None)


def session_kwargs() -> dict:
    """Keyword arguments for spawning cancellable children in their own
    session.

    POSIX: `start_new_session=True` puts the child (and everything it
    spawns) into a new process group, so `terminate_tree` can kill the
    whole subtree with one killpg — the documented upgrade path for
    tree-children. Production children (HandBrakeCLI, ffmpeg) are leaf
    processes today, but the test stubs (`sh` that runs `sleep`) are not:
    a group-kill is the only reliable way to end them AND keep the pipes
    EOF-clean (an orphaned grandchild holds the write-end, the drainer
    never sees EOF, the caller hangs - observed in CI on Linux).
    Windows: taskkill /T /F already walks the tree; nothing needed here.

    Returns:
        dict: kwargs to splat into subprocess.Popen (empty on Windows).
    """
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


def terminate_tree(popen: subprocess.Popen) -> None:
    """Terminates a child process INCLUDING all of its descendants.

    Why the tree matters: killing only the direct child leaves grandchildren
    alive, and they hold the inherited pipe write-handles - no EOF, the
    drain threads block forever, the caller hangs even though the kill
    "worked" (observed live: cmd.exe died at 1.0s, the orphaned ping kept
    the pipe open and the function returned 58s later when ping happened to
    finish). Windows: taskkill /T /F walks the descendant tree (terminate()
    is TerminateProcess there anyway - force-kill is the only option, and
    the terminate->kill escalation in the classic path is POSIX semantics).
    POSIX: the child runs in its own session (session_kwargs at spawn, the
    process group IS the tree) => killpg SIGTERM -> SIGKILL escalation on
    the whole group; every member dies and the pipes reach EOF. The group
    is only killed when the child actually sits in its own group (verified
    via getpgid against ours) - a child spawned without a session shares
    OUR group, and killpg there would terminate the caller itself. Such
    children (and unreadable ones) take the classic direct-child
    escalation below.

    Args:
        popen (subprocess.Popen): The child to stop (all of it).
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(popen.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass  # taskkill missing/blocked: classic escalation below
    # POSIX: killpg the child's group, but only when it differs from ours
    # (start_new_session makes the group the tree; a shared group must take
    # the classic path - killpg on it would hit the caller too). Platform
    # guard, not getattr-dancing: mypy narrows sys.platform, so the Windows
    # pass never type-checks this block (its stubs lack getpgid/killpg).
    if sys.platform != "win32":
        child_pgid: int | None = None
        own_pgid: int | None = None
        try:
            child_pgid = os.getpgid(popen.pid)
            own_pgid = os.getpgid(0)
        except OSError:
            pass  # child already gone / unreadable: classic path
        if child_pgid is not None and own_pgid is not None and child_pgid != own_pgid:
            try:
                os.killpg(child_pgid, signal.SIGTERM)
            except OSError:
                pass  # group already gone between getpgid and killpg
            else:
                try:
                    popen.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child_pgid, signal.SIGKILL)
                    popen.wait(timeout=5)
                return
    # classic escalation fallback (Windows non-taskkill path, or a POSIX
    # child in our own group / already gone)
    popen.terminate()
    try:
        popen.wait(timeout=5)
    except subprocess.TimeoutExpired:
        popen.kill()
        popen.wait(timeout=5)


def apply(config: Conf) -> None:
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
                return float(counters.WorkingSetSize) / (1024 * 1024)
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


def cpu_count() -> int:
    """Returns the CPUs this process may actually use (at least 1).

    os.cpu_count() reports the machine's cores, not the process's share. In a
    container with --cpus=4 on a 24-core host it returns 24, so a plan would
    start 24 parallel encodes inside a 4-CPU quota: they thrash and the
    watchdog's timing assumptions stop holding.

    Measured, not assumed: inside a container limited to 2 CPUs,
    os.cpu_count() AND os.process_cpu_count() both still report the host's 24,
    so the quota has to be read from the cgroup directly. cpu.max carries it on
    v2 ("<quota> <period>", "max" = unlimited) and cpu.cfs_quota_us /
    cpu.cfs_period_us on v1. Where neither is readable (bare metal, macOS,
    Windows) the machine's core count is the right answer.

    Returns:
        int: The usable CPU count, never less than 1.
    """
    quota = _cgroup_cpu_quota()
    if quota is not None:
        return max(1, quota)
    return max(1, os.cpu_count() or 1)


def _cgroup_cpu_quota() -> int | None:
    """Returns how many whole CPUs a cgroup quota permits.

    Returns:
        int | None: The permitted whole CPUs, or None when unlimited or
        unreadable.
    """
    try:
        parts = Path("/sys/fs/cgroup/cpu.max").read_text().split()
    except OSError:
        parts = []
    if len(parts) == 2 and parts[0] != "max":
        try:
            return int(parts[0]) // int(parts[1])
        except (ValueError, ZeroDivisionError):
            return None
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
    except (OSError, ValueError):
        return None
    if quota <= 0:
        return None
    return quota // period
