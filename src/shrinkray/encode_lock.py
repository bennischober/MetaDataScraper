"""Transitional re-export shim: the encode lock moved to engine/lock.py
(issue #97 phase 3). Removed when import-linter lands (phase 6)."""

from shrinkray.engine.lock import (
    _LOCK_STALE_SECONDS as _LOCK_STALE_SECONDS,
)
from shrinkray.engine.lock import (
    EncodeBusyError as EncodeBusyError,
)
from shrinkray.engine.lock import (
    _lock_path as _lock_path,
)
from shrinkray.engine.lock import (
    _pid_alive as _pid_alive,
)
from shrinkray.engine.lock import (
    acquire_encode_slot as acquire_encode_slot,
)
from shrinkray.engine.lock import (
    guard_encodes as guard_encodes,
)
