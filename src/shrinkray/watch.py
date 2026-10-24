"""Transitional re-export shim: the inbox watchdog moved to
engine/watch.py (issue #97 phase 3). Removed when import-linter
lands (phase 6)."""

from shrinkray.engine.watch import (
    MAX_POLL_MULTIPLIER as MAX_POLL_MULTIPLIER,
)
from shrinkray.engine.watch import (
    POLL_INTERVAL as POLL_INTERVAL,
)
from shrinkray.engine.watch import (
    STABLE_CHECK_INTERVAL as STABLE_CHECK_INTERVAL,
)
from shrinkray.engine.watch import (
    STABLE_MIN_AGE as STABLE_MIN_AGE,
)
from shrinkray.engine.watch import (
    acquire_lock as acquire_lock,
)
from shrinkray.engine.watch import (
    decide as decide,
)
from shrinkray.engine.watch import (
    find_new_movies as find_new_movies,
)
from shrinkray.engine.watch import (
    is_stable as is_stable,
)
from shrinkray.engine.watch import (
    next_poll_interval as next_poll_interval,
)
from shrinkray.engine.watch import (
    release_lock as release_lock,
)
from shrinkray.engine.watch import (
    watch as watch,
)
