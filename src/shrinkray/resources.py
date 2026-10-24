"""TRANSITIONAL RE-EXPORT SHIM (issue #97 phase 2, allowed until phase 6).

resources.py moved verbatim to tools/process.py (issue #97: the tools/
package is the subprocess boundary). Everything here re-exports the moved
names so in-flight branches and existing imports keep working. Import from
shrinkray.tools.process directly in NEW code; this shim is removed when the
import-linter contract lands (phase 6).
"""

from __future__ import annotations

from shrinkray.tools.process import (
    _BELOW_NORMAL,
    _IDLE_PRIORITY,
    _cgroup_cpu_quota,
    apply,
    cpu_count,
    nice_self,
    rss_mib,
    session_kwargs,
    spawn_kwargs,
    terminate_tree,
)

__all__ = [
    "_BELOW_NORMAL",
    "_IDLE_PRIORITY",
    "_cgroup_cpu_quota",
    "apply",
    "cpu_count",
    "nice_self",
    "rss_mib",
    "session_kwargs",
    "spawn_kwargs",
    "terminate_tree",
]
