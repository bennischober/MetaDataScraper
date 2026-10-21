"""Encode orchestration (the service layer between CLI and engine).

Contains the logic that drives multiple encode engine calls for a whole
movie set: batch resume, skip recording and result persistence. It has no
presentation logic — the CLI prints, this module decides and records.
"""

from dataclasses import dataclass

from metadata_scrapper import db
from metadata_scrapper.db import MovieRecord


@dataclass
class EncodeOutcome:
    """The outcome of processing one movie in an encode run.

    Attributes:
        movie (str): The movie file path as string.
        encoded (bool): True if an encode was performed and succeeded.
        skipped (bool): True if a guardrail or the resume check skipped it.
        error (str | None): The failure message for failed encodes.
    """

    movie: str
    encoded: bool = False
    skipped: bool = False
    error: str | None = None


def ensure_file_row(connection, movie) -> int:
    """Returns the file id for the movie, storing a minimal row if needed.

    Args:
        connection: The database connection.
        movie (Path): The movie file.

    Returns:
        int: The file id.
    """
    movie = movie.resolve()
    file_row = db.get_file_by_path(connection, movie)
    if file_row is not None:
        return int(file_row["id"])
    record = MovieRecord(
        path=movie,
        mtime=movie.stat().st_mtime,
        size=movie.stat().st_size,
        name=movie.stem,
        category=movie.parent.name,
    )
    return db.upsert_file(connection, record)


def save_encode(
    connection,
    movie,
    preset: str,
    encoder: str,
    input_size: int,
    output_size: int,
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
):
    """Stores an encode record for the given movie in the database.

    Args:
        connection: The database connection.
        movie (Path): The input movie file.
        preset (str): The preset (or quality target) used.
        encoder (str): The encoder used.
        input_size (int): Input size in bytes.
        output_size (int): Output size in bytes.
        chosen_crf (int | None): The CRF chosen by the quality search, if any.
        achieved_vmaf (float | None): The VMAF measured at the chosen CRF.
        encoder_preset (str | None): The speed preset used (benchmark data).
        encode_seconds (float | None): Wall time of the encode (benchmark).
        probe_count (int | None): Number of probes in the search (benchmark).
        probe_seconds (float | None): Wall time of the probing (benchmark).
        hdr (bool | None): Whether the source was HDR (benchmark).
        source_width (int | None): Source resolution snapshot (benchmark).
        source_height (int | None): Source resolution snapshot (benchmark).
        source_codec (str | None): Source codec snapshot (benchmark).
        source_duration (float | None): Source duration snapshot (benchmark).
        tool_versions (str | None): Tool version string (benchmark).
    """
    file_id = ensure_file_row(connection, movie)
    saved_percent = round((1 - output_size / input_size) * 100, 2) if input_size else 0.0
    db.add_encode(
        connection,
        file_id,
        preset,
        encoder,
        input_size,
        output_size,
        saved_percent,
        chosen_crf=chosen_crf,
        achieved_vmaf=achieved_vmaf,
        encoder_preset=encoder_preset,
        encode_seconds=encode_seconds,
        probe_count=probe_count,
        probe_seconds=probe_seconds,
        hdr=hdr,
        source_width=source_width,
        source_height=source_height,
        source_codec=source_codec,
        source_duration=source_duration,
        tool_versions=tool_versions,
    )


def save_skip(connection, movie, reason: str):
    """Stores a skip record for the given movie in the database.

    Args:
        connection: The database connection.
        movie (Path): The input movie file.
        reason (str): Why the movie was skipped.
    """
    file_id = ensure_file_row(connection, movie)
    db.add_skip(connection, file_id, reason)


def finished_movies(connection, movies: list, output_dir) -> set[str]:
    """Determines which movies are already encoded (batch resume check).

    A movie counts as finished when the database holds a real encode (not a
    skip marker) AND its output file still exists and is non-empty.

    Args:
        connection: The database connection.
        movies (list[Path]): The movie files of the run.
        output_dir (Path): The output directory of the run.

    Returns:
        set[str]: The movie paths (as strings) that can be skipped.
    """
    finished: set[str] = set()
    for movie in movies:
        file_row = db.get_file_by_path(connection, movie)
        if file_row is None:
            continue
        if db.has_output_for_latest_encode(
            connection, int(file_row["id"]), output_dir / movie.name
        ):
            finished.add(str(movie))
    return finished


def record_quality_results(connection, movie, result, config, target_vmaf: float) -> EncodeOutcome:
    """Persists the result of one quality-mode encode (or skip).

    Args:
        connection: The database connection.
        movie (Path): The input movie file.
        result (QualityResult): The engine result.
        config (Conf): The config settings.
        target_vmaf (float): The VMAF quality floor of the run.

    Returns:
        EncodeOutcome: What happened (encoded / skipped / failed).
    """
    if result.success:
        save_encode(
            connection,
            movie,
            f"VMAF>{target_vmaf}",
            config.handbrake_encoder,
            result.input_size,
            result.output_size,
            chosen_crf=result.chosen_crf,
            achieved_vmaf=result.achieved_vmaf,
            encoder_preset=result.encoder_preset,
            encode_seconds=result.encode_seconds,
            probe_count=len(result.probes),
            probe_seconds=result.probe_seconds,
            hdr=result.hdr,
            source_width=result.input_width,
            source_height=result.input_height,
            source_codec=result.input_codec,
            source_duration=result.input_duration,
            tool_versions=result.tool_versions,
        )
        return EncodeOutcome(movie=str(movie), encoded=True)
    if result.skipped_reason is not None:
        save_skip(connection, movie, result.skipped_reason)
        return EncodeOutcome(movie=str(movie), skipped=True, error=result.error)
    return EncodeOutcome(movie=str(movie), error=result.error)


def record_fixed_results(connection, results: list, config) -> list[EncodeOutcome]:
    """Persists the results of a fixed-preset run.

    An encode that did NOT shrink the file is recorded as a skip ('no size
    gain') instead of a success: a pointless encode should never wear a
    green chip in the UI.

    Args:
        connection: The database connection.
        results (list[EncodeResult]): The engine results.
        config (Conf): The config settings.

    Returns:
        list[EncodeOutcome]: One outcome per result.
    """
    outcomes = []
    for encode_result in results:
        if encode_result.success and encode_result.output_size >= encode_result.input_size:
            save_skip(connection, encode_result.input, "no size gain")
            outcomes.append(EncodeOutcome(movie=str(encode_result.input), skipped=True))
            continue
        if encode_result.success:
            save_encode(
                connection,
                encode_result.input,
                config.handbrake_preset,
                config.handbrake_encoder,
                encode_result.input_size,
                encode_result.output_size,
                encoder_preset=config.handbrake_preset,
                source_width=encode_result.input_width,
                source_height=encode_result.input_height,
                source_codec=encode_result.input_codec,
                source_duration=encode_result.input_duration,
            )
            outcomes.append(EncodeOutcome(movie=str(encode_result.input), encoded=True))
        else:
            outcomes.append(
                EncodeOutcome(movie=str(encode_result.input), error=encode_result.error)
            )
    return outcomes
