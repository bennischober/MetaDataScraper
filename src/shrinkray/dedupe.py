"""Duplicate detection across the scanned library.

Groups movies that are (a) byte-identical (matching fast content hash and
similar size) or (b) the same title with a different encode (normalized
name match + similar duration). Detection is read-only: resolving a group
(delete/replace) is always an explicit user action, never automatic.
"""

import re
import sqlite3
from dataclasses import dataclass, field

import rapidfuzz

# duration tolerance in seconds for the same-title match (different encodes
# of the same movie differ by a few seconds of container/padding differences)
DURATION_TOLERANCE = 5.0

# minimum rapidfuzz token_set_ratio for the 'similar title' grouping; high
# enough to not group Alien with Aliens, low enough for typos/variants
FUZZY_THRESHOLD = 92

# release-tag noise stripped from names before matching
_NAME_NOISE = re.compile(
    r"[\.\-_]+"  # separators
    r"|\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd)\b"  # resolutions
    r"|\b(x264|x265|h\.?264|h\.?265|hevc|av1|avc|mpeg2|xvid|divx)\b"  # codecs
    r"|\b(blu-?ray|bdrip|brrip|web[.\-_]?dl|webrip|hdtv|dvdrip|dvd|remux|hdr10|hdr|dv|sdr)\b"  # sources
    r"|\b(german|english|ger|eng|dubbed|subbed)\b"  # languages
    r"|\b(ac3|dts|aac|truehd|atmos|ddp?|dd\+)[\.\-_ ]?(5\.1|7\.1|2\.0)?\b"  # audio
    r"|\b(proper|repack|extended|uncut|uncensored|remastered|director.?s.?cut|dc|imax)\b"
    r"|\b(\d{4})\b",  # year
    re.IGNORECASE,
)


@dataclass
class DuplicateFile:
    """One file inside a duplicate group.

    Attributes:
        path (str): The absolute file path.
        name (str): The movie name.
        category (str): The category (folder) it lives in.
        size (int): File size in bytes.
        duration (float | None): Duration in seconds.
        codec (str | None): Video codec.
        hash (str | None): Fast content hash (first/last MB), when computed.
        width (int | None): Frame width.
        height (int | None): Frame height.
    """

    path: str
    name: str
    category: str
    size: int
    duration: float | None = None
    codec: str | None = None
    hash: str | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict:
        """Returns the file as a JSON-able dict.

        Returns:
            dict: The file data.
        """
        return {
            "path": self.path,
            "name": self.name,
            "category": self.category,
            "size": self.size,
            "duration": self.duration,
            "codec": self.codec,
            "hash": self.hash,
            "resolution": [self.width, self.height] if self.width and self.height else None,
        }


@dataclass
class DuplicateGroup:
    """A group of files detected as (probably) the same content.

    Attributes:
        reason (str): Why these files are grouped ('identical' or 'same title').
        files (list[DuplicateFile]): The group members (best candidate first).
    """

    reason: str
    files: list[DuplicateFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Returns the group as a JSON-able dict.

        Returns:
            dict: The group data.
        """
        return {
            "reason": self.reason,
            "files": [f.to_dict() for f in self.files],
            "wasted_bytes": self.wasted_bytes,
        }

    @property
    def wasted_bytes(self) -> int:
        """The bytes recoverable by keeping only the best file."""
        if len(self.files) <= 1:
            return 0
        return sum(f.size for f in self.files[1:])


def normalize_name(name: str) -> str:
    """Normalizes a movie name for same-title matching.

    Strips separators, release tags (resolution, codec, source, audio),
    years and lowercases; 'The.Mandalorian.S01E02.1080p.WEB-DL' and
    'The Mandalorian - S01E02' collapse to the same key.

    Args:
        name (str): The raw movie name.

    Returns:
        str: The normalized key (may be empty for noise-only names).
    """
    cleaned = _NAME_NOISE.sub(" ", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _duration_of(row: sqlite3.Row) -> float | None:
    """Reads the duration in seconds from a db row.

    Args:
        row (sqlite3.Row): A files table row.

    Returns:
        float | None: The duration in seconds, or None.
    """
    if row["duration_raw"]:
        return float(row["duration_raw"])
    return None


def find_duplicates(connection: sqlite3.Connection) -> list[DuplicateGroup]:
    """Finds duplicate groups across the whole scanned library.

    Three grouping passes: byte-identical files (same hash, computed by the
    scan's --hash option), same-title files (normalized name + duration
    within tolerance) and similar-title files (fuzzy match for typos and
    variants; also duration-gated). A file can appear in several passes;
    groups are merged when they share members. The caller decides which
    rows to consider (e.g. the API excludes rows flagged missing) — this
    function is pure grouping over the rows it is given.

    Note: the API endpoint feeds this function a temp table named `files`
    that shadows the real one and carries fewer columns; do not reference
    schema-v6 columns here.

    Args:
        connection (sqlite3.Connection): The database connection.

    Returns:
        list[DuplicateGroup]: Groups with at least 2 members, best candidate
            first (largest file = likely best quality).
    """
    rows = connection.execute(
        """
        SELECT path, name, category, size, duration_raw, codec, hash, width, height
        FROM files
        ORDER BY size DESC
        """
    ).fetchall()

    files = [
        DuplicateFile(
            path=r["path"],
            name=r["name"],
            category=r["category"],
            size=int(r["size"]),
            duration=_duration_of(r),
            codec=r["codec"],
            hash=r["hash"],
            width=r["width"],
            height=r["height"],
        )
        for r in rows
    ]

    groups: list[DuplicateGroup] = []

    # pass 1: byte-identical (same content hash, only meaningful when set)
    by_hash: dict[str, list[DuplicateFile]] = {}
    for f in files:
        if f.hash:
            by_hash.setdefault(f.hash, []).append(f)
    for members in by_hash.values():
        if len(members) >= 2:
            groups.append(DuplicateGroup(reason="identical", files=members))

    # pass 2: same title (normalized name + duration within tolerance)
    by_title: dict[str, list[DuplicateFile]] = {}
    for f in files:
        key = normalize_name(f.name)
        if key:
            by_title.setdefault(key, []).append(f)
    for members in by_title.values():
        if len(members) < 2:
            continue
        # duration gate: only group when durations are within tolerance
        # (guards against different episodes/movies sharing a name key)
        with_duration = [f for f in members if f.duration is not None]
        if len(with_duration) < 2:
            continue
        reference = max(f.duration for f in with_duration if f.duration is not None)
        similar = [
            f
            for f in members
            if f.duration is None or abs((f.duration or 0) - reference) <= DURATION_TOLERANCE
        ]
        if len(similar) >= 2:
            groups.append(DuplicateGroup(reason="same title", files=similar))

    # pass 3: similar titles (fuzzy, for typos/variants the exact pass
    # misses, e.g. 'Django Unchainedd' or roman-vs-digit numerals); same
    # duration gate as pass 2
    grouped_paths = {f.path for g in groups for f in g.files}
    fuzzy_candidates = [f for f in files if normalize_name(f.name) and f.path not in grouped_paths]
    for i, a in enumerate(fuzzy_candidates):
        for b in fuzzy_candidates[i + 1 :]:
            if a.path in grouped_paths or b.path in grouped_paths:
                continue
            score = rapidfuzz.fuzz.token_set_ratio(normalize_name(a.name), normalize_name(b.name))
            if score < FUZZY_THRESHOLD:
                continue
            if a.duration is None or b.duration is None:
                continue
            if abs(a.duration - b.duration) > DURATION_TOLERANCE:
                continue
            groups.append(DuplicateGroup(reason="similar title", files=[a, b]))
            grouped_paths.update((a.path, b.path))

    # merge groups sharing members (a file identical AND same-title)
    merged: list[DuplicateGroup] = []
    for group in groups:
        group_paths = {f.path for f in group.files}
        for existing in merged:
            existing_paths = {f.path for f in existing.files}
            if group_paths & existing_paths:
                existing.reason = "identical + same title"
                existing.files.extend(f for f in group.files if f.path not in existing_paths)
                break
        else:
            merged.append(group)

    # best candidate first: largest file (likely best quality); ties by path
    for group in merged:
        group.files.sort(key=lambda f: (-f.size, f.path))

    return merged
