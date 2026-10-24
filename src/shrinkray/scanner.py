"""Transitional re-export shim: the scanner moved to
engine/scan.py (issue #97 phase 3). Removed when import-linter
lands (phase 6)."""

from shrinkray.engine.scan import (
    UNCATEGORIZED as UNCATEGORIZED,
)
from shrinkray.engine.scan import (
    find_movies as find_movies,
)
from shrinkray.engine.scan import (
    get_category as get_category,
)
from shrinkray.engine.scan import (
    get_name as get_name,
)
from shrinkray.engine.scan import (
    log_prune_result as log_prune_result,
)
from shrinkray.engine.scan import (
    movie_record_from_data as movie_record_from_data,
)
from shrinkray.engine.scan import (
    process_movie as process_movie,
)
from shrinkray.engine.scan import (
    record_from_cache as record_from_cache,
)
from shrinkray.engine.scan import (
    scan_movies as scan_movies,
)
