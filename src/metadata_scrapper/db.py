import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    hash TEXT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    duration TEXT,
    duration_raw REAL,
    fps REAL,
    codec TEXT,
    bitrate REAL,
    aspect_ratio TEXT,
    width INTEGER,
    height INTEGER,
    crop INTEGER,
    hdr INTEGER,
    other_codecs TEXT,
    audio_languages TEXT,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);

CREATE TABLE IF NOT EXISTS encodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    preset TEXT NOT NULL,
    encoder TEXT NOT NULL,
    input_size INTEGER NOT NULL,
    output_size INTEGER NOT NULL,
    saved_percent REAL NOT NULL,
    chosen_crf INTEGER,
    achieved_vmaf REAL,
    encoder_preset TEXT,
    encode_seconds REAL,
    probe_count INTEGER,
    probe_seconds REAL,
    hdr INTEGER,
    source_width INTEGER,
    source_height INTEGER,
    source_codec TEXT,
    source_duration REAL,
    tool_versions TEXT,
    encoded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_encodes_file ON encodes(file_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
"""


# migration v1 -> v2: record quality-mode decisions (chosen CRF, measured
# VMAF) and skipped movies so subsequent runs do not re-probe them
# migration v2 -> v3: stored paths must be absolute (relative paths created
# phantom duplicates when runs happened from different working directories)
def job_table_migration(connection: sqlite3.Connection):
    """Migration v3 -> v4: create the jobs table for the resident app."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            state TEXT NOT NULL,
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT
        );
        """
    )


MIGRATIONS = {
    2: [
        "ALTER TABLE encodes ADD COLUMN chosen_crf INTEGER",
        "ALTER TABLE encodes ADD COLUMN achieved_vmaf REAL",
    ],
    3: ["__absolute_paths__"],  # handled specially in _migrate (needs Path)
    4: ["__job_table__"],  # handled specially in _migrate (creates a table)
    5: [
        # benchmark-grade encode records (see TODO: library-wide data runs)
        "ALTER TABLE encodes ADD COLUMN encoder_preset TEXT",
        "ALTER TABLE encodes ADD COLUMN encode_seconds REAL",
        "ALTER TABLE encodes ADD COLUMN probe_count INTEGER",
        "ALTER TABLE encodes ADD COLUMN probe_seconds REAL",
        "ALTER TABLE encodes ADD COLUMN hdr INTEGER",
        "ALTER TABLE encodes ADD COLUMN source_width INTEGER",
        "ALTER TABLE encodes ADD COLUMN source_height INTEGER",
        "ALTER TABLE encodes ADD COLUMN source_codec TEXT",
        "ALTER TABLE encodes ADD COLUMN source_duration REAL",
        "ALTER TABLE encodes ADD COLUMN tool_versions TEXT",
    ],
}


def _migrate(connection: sqlite3.Connection):
    """Applies pending schema migrations and records the new version.

    Args:
        connection: The database connection.
    """
    stored = connection.execute("SELECT version FROM schema_version").fetchone()
    if stored is None:
        return
    current = int(stored[0])
    while current < SCHEMA_VERSION:
        current += 1
        for statement in MIGRATIONS.get(current, []):
            if statement == "__absolute_paths__":
                # rewrite relative file paths to absolute ones; a relative row
                # whose absolute twin already exists is merged into it (the
                # absolute row wins, the relative one is removed)
                rows = connection.execute("SELECT id, path FROM files").fetchall()
                for row in rows:
                    path = Path(row["path"])
                    if path.is_absolute():
                        continue
                    absolute = path.resolve()
                    twin = connection.execute(
                        "SELECT id FROM files WHERE path = ?", (str(absolute),)
                    ).fetchone()
                    if twin is not None:
                        # move encodes to the twin, then drop the relative row
                        connection.execute(
                            "UPDATE encodes SET file_id = ? WHERE file_id = ?",
                            (twin["id"], row["id"]),
                        )
                        connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                    elif absolute.is_file():
                        connection.execute(
                            "UPDATE files SET path = ? WHERE id = ?",
                            (str(absolute), row["id"]),
                        )
                    else:
                        connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
            elif statement == "__job_table__":
                job_table_migration(connection)
            else:
                connection.execute(statement)
        connection.execute("DELETE FROM schema_version")
        connection.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
    connection.commit()


def compute_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Computes a fast hash of the first and last MB of a file.

    This is not a checksum of the whole file but enough to detect
    different files with the same size/mtime.

    Args:
        path (Path): The file to hash.
        chunk_size (int): The size of the chunks to read.

    Returns:
        str: The hash as hex string.
    """
    hasher = hashlib.sha256()
    size = path.stat().st_size
    with open(path, "rb") as file:
        hasher.update(file.read(min(chunk_size, size)))
        if size > chunk_size:
            file.seek(max(0, size - chunk_size))
            hasher.update(file.read(chunk_size))
    return hasher.hexdigest()


@dataclass
class MovieRecord:
    """A movie record as stored in the database.

    Attributes:
        path (Path): Path of the movie file.
        mtime (float): Modification time as unix timestamp.
        size (int): Size in bytes.
        hash (str | None): Fast content hash (first/last MB), if computed.
        name (str): Movie name.
        category (str): Category name.
        duration (str | None): Duration as 'HH:MM:SS' string.
        duration_raw (float | None): Duration in seconds.
        fps (float | None): Frames per second.
        codec (str | None): Video codec name.
        bitrate (float | None): Bitrate in MBit/s.
        aspect_ratio (str | None): Aspect ratio as 'WxH' string.
        width (int | None): Width in pixels.
        height (int | None): Height in pixels.
        crop (int): 1 if black bars were detected.
        hdr (int): 1 if the video is HDR.
        other_codecs (str): Comma separated other codec names.
        audio_languages (str): Comma separated audio languages.
        scanned_at (str | None): ISO timestamp of the last scan.
    """

    path: Path
    mtime: float
    size: int
    name: str
    category: str
    hash: str | None = None
    duration: str | None = None
    duration_raw: float | None = None
    fps: float | None = None
    codec: str | None = None
    bitrate: float | None = None
    aspect_ratio: str | None = None
    width: int | None = None
    height: int | None = None
    crop: int = 0
    hdr: int = 0
    other_codecs: str = ""
    audio_languages: str = ""
    scanned_at: str | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    """Opens a connection to the database and ensures the schema.

    The connection is usable from any thread (check_same_thread=False) and
    the journal mode is WAL so that the resident app and CLI processes can
    access the database concurrently. Writes stay short (single statements
    with commit) so readers never block meaningfully.

    Args:
        db_path (Path): Path of the sqlite database file.

    Returns:
        sqlite3.Connection: The connection (row_factory set to sqlite3.Row).
    """
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    # ensure the version row exists
    if connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    _migrate(connection)
    connection.commit()
    return connection


def is_unchanged(row: sqlite3.Row, path: Path) -> bool:
    """Checks if a stored file entry is still valid (mtime and size match).

    Args:
        row (sqlite3.Row): The stored file row.
        path (Path): The current file on disk.

    Returns:
        bool: True if the file did not change since the last scan.
    """
    stat = path.stat()
    return row["mtime"] == stat.st_mtime and row["size"] == stat.st_size


def upsert_file(connection: sqlite3.Connection, record: MovieRecord) -> int:
    """Inserts or updates a movie record and returns the file id.

    Args:
        connection (sqlite3.Connection): The database connection.
        record (MovieRecord): The record to store.

    Returns:
        int: The file id.
    """
    cursor = connection.execute(
        """
        INSERT INTO files (
            path, mtime, size, hash, name, category, duration, duration_raw, fps,
            codec, bitrate, aspect_ratio, width, height, crop, hdr,
            other_codecs, audio_languages, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(path) DO UPDATE SET
            mtime = excluded.mtime,
            size = excluded.size,
            hash = excluded.hash,
            name = excluded.name,
            category = excluded.category,
            duration = excluded.duration,
            duration_raw = excluded.duration_raw,
            fps = excluded.fps,
            codec = excluded.codec,
            bitrate = excluded.bitrate,
            aspect_ratio = excluded.aspect_ratio,
            width = excluded.width,
            height = excluded.height,
            crop = excluded.crop,
            hdr = excluded.hdr,
            other_codecs = excluded.other_codecs,
            audio_languages = excluded.audio_languages,
            scanned_at = datetime('now')
        """,
        (
            str(record.path),
            record.mtime,
            record.size,
            record.hash,
            record.name,
            record.category,
            record.duration,
            record.duration_raw,
            record.fps,
            record.codec,
            record.bitrate,
            record.aspect_ratio,
            record.width,
            record.height,
            record.crop,
            record.hdr,
            record.other_codecs,
            record.audio_languages,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid or 0)


def get_file_by_path(connection: sqlite3.Connection, path: Path) -> sqlite3.Row | None:
    """Fetches a file row by path.

    Args:
        connection (sqlite3.Connection): The database connection.
        path (Path): The movie file path.

    Returns:
        sqlite3.Row | None: The row, or None if not stored.
    """
    return connection.execute("SELECT * FROM files WHERE path = ?", (str(path),)).fetchone()


def get_cached_records(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Returns all stored file rows keyed by their path.

    Args:
        connection (sqlite3.Connection): The database connection.

    Returns:
        dict: Mapping of path string to file row.
    """
    rows = connection.execute("SELECT * FROM files").fetchall()
    return {row["path"]: row for row in rows}


def add_encode(
    connection: sqlite3.Connection,
    file_id: int,
    preset: str,
    encoder: str,
    input_size: int,
    output_size: int,
    saved_percent: float,
    chosen_crf: int | None = None,
    achieved_vmaf: float | None = None,
    encoder_preset: str | None = None,
    encode_seconds: float | None = None,
    probe_count: int | None = None,
    probe_seconds: float | None = None,
    hdr: bool | None = None,
    source_width: int | None = None,
    source_height: int | None = None,
    source_codec: str | None = None,
    source_duration: float | None = None,
    tool_versions: str | None = None,
) -> int:
    """Records an encode in the history table.

    Args:
        connection (sqlite3.Connection): The database connection.
        file_id (int): The file id of the input movie.
        preset (str): The HandBrake preset used (or the quality target label).
        encoder (str): The encoder used.
        input_size (int): Input size in bytes.
        output_size (int): Output size in bytes.
        saved_percent (float): Saved percentage.
        chosen_crf (int | None): The CRF chosen by the quality search, if any.
        achieved_vmaf (float | None): The VMAF measured at the chosen CRF.

    Returns:
        int: The encode id.
    """
    cursor = connection.execute(
        """
        INSERT INTO encodes (
            file_id, preset, encoder, input_size, output_size, saved_percent,
            chosen_crf, achieved_vmaf, encoder_preset, encode_seconds,
            probe_count, probe_seconds, hdr, source_width, source_height,
            source_codec, source_duration, tool_versions
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_id,
            preset,
            encoder,
            input_size,
            output_size,
            saved_percent,
            chosen_crf,
            achieved_vmaf,
            encoder_preset,
            encode_seconds,
            probe_count,
            probe_seconds,
            hdr,
            source_width,
            source_height,
            source_codec,
            source_duration,
            tool_versions,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid or 0)


def add_skip(
    connection: sqlite3.Connection,
    file_id: int,
    reason: str,
    chosen_crf: int | None = None,
    achieved_vmaf: float | None = None,
) -> int:
    """Records a skipped movie in the encode history.

    A skip is stored as an encode row with zero sizes and a 'skipped (...)'
    preset label so subsequent runs can recognize it (and e.g. not re-probe
    a movie known not to shrink).

    Args:
        connection (sqlite3.Connection): The database connection.
        file_id (int): The file id of the input movie.
        reason (str): Why the movie was skipped, e.g. 'no size gain'.
        chosen_crf (int | None): The CRF used in the probe, if any.
        achieved_vmaf (float | None): The VMAF measured in the probe, if any.

    Returns:
        int: The encode row id.
    """
    return add_encode(
        connection,
        file_id,
        f"skipped ({reason})",
        "none",
        0,
        0,
        0.0,
        chosen_crf=chosen_crf,
        achieved_vmaf=achieved_vmaf,
    )


def get_latest_encode(connection: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    """Returns the most recent encode (or skip) row of a file.

    Args:
        connection (sqlite3.Connection): The database connection.
        file_id (int): The file id.

    Returns:
        sqlite3.Row | None: The newest row, or None if never encoded.
    """
    rows = get_encodes_for_file(connection, file_id)
    return rows[0] if rows else None


def has_output_for_latest_encode(
    connection: sqlite3.Connection,
    file_id: int,
    output_file: Path,
) -> bool:
    """Checks whether the latest encode entry produced a still-existing output.

    Used for batch resume: a movie whose encode was recorded AND whose output
    file still exists is considered finished; anything else (no record,
    deleted output) must be (re-)encoded.

    Args:
        connection (sqlite3.Connection): The database connection.
        file_id (int): The file id.
        output_file (Path): The output path this run would write.

    Returns:
        bool: True if the movie can be considered already encoded.
    """
    latest = get_latest_encode(connection, file_id)
    if latest is None:
        return False
    if latest["output_size"] <= 0:
        return False  # a skip marker, not a real encode
    return output_file.is_file() and output_file.stat().st_size > 0


def get_encodes_for_file(connection: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    """Returns all encodes for a file, newest first.

    Args:
        connection (sqlite3.Connection): The database connection.
        file_id (int): The file id.

    Returns:
        list: The encode rows.
    """
    return connection.execute(
        "SELECT * FROM encodes WHERE file_id = ? ORDER BY id DESC",
        (file_id,),
    ).fetchall()


def prune_missing(
    connection: sqlite3.Connection,
    existing_paths: set[str],
    scope: str | None = None,
) -> int:
    """Deletes file rows (and their encodes) that are no longer on disk.

    Without a scope, only rows inside the given scope directory are
    considered: a scan of one folder must never delete the history of files
    stored elsewhere (e.g. a scan of testdata/movies_two would otherwise
    wipe the records of testdata/movies).

    Args:
        connection (sqlite3.Connection): The database connection.
        existing_paths (set[str]): Paths that are still on disk.
        scope (str | None): Only prune rows under this directory path.

    Returns:
        int: Number of deleted rows.
    """
    rows = connection.execute("SELECT id, path FROM files").fetchall()
    scope_normalized = str(Path(scope)) if scope is not None else None
    deleted = 0
    for row in rows:
        path = row["path"]
        if scope_normalized is not None and not path.startswith(scope_normalized):
            continue
        if path not in existing_paths:
            connection.execute("DELETE FROM encodes WHERE file_id = ?", (row["id"],))
            connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
            deleted += 1
    connection.commit()
    return deleted
