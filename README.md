# MetaDataScraper
### Table of contents
- [Overview](#overview)
- [Quickstart](#quickstart)
- [How to install](#how-to-install)
    - [Basic setup](#basic-setup)
    - [Install with uv](#install-with-uv)
- [How it works](#how-it-works)
- [External dependencies](#external-dependencies)
- [Useful commands](#useful-commands)
- [Re-encoding with HandBrake](#re-encoding-with-handbrake)
- [Configuration](#configuration)
- [Development](#development)
    - [Devcontainer](#devcontainer)
    - [Tests and linting](#tests-and-linting)
- [Features for the future](#features-for-the-future)
- [FAQ](#faq)
- [Additional ressources](#additional-ressources)

## Overview
A short python script to get video meta data.\
Notes:
>This project uses Python 3.15 (managed with [uv](https://docs.astral.sh/uv/), older versions >= 3.10 work too)

>Movie files are found by the extensions configured in [config.json](config.json) (by default `.mkv` and other common formats).

## Quickstart
Usage: run the ``scan`` command with the root of your movies folder (e.g. 'D:\Movies') as parameter.\
Execute this command in the ``Windows Terminal``, ``CMD`` or any shell:
```
uvx metadata-scrapper scan 'path:\to\movie-root'
```
or, from a cloned repository:
```
uv run metadata-scrapper scan 'path:\to\movie-root'
```

## How to install
### Basic setup
The first steps are to install [uv](https://docs.astral.sh/uv/getting-started/installation/) (manages Python and all dependencies), [FFmpeg](https://www.ffmpeg.org/download.html) ([short tutorial for windows](https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/)) and, optionally, [HandBrake CLI](https://handbrake.fr/downloads.php) for re-encoding.

### Install with uv
Clone this repository:
```
git clone https://github.com/bennischober/MetaDataScraper.git
```
Install and run:
```
uv sync
uv run metadata-scrapper scan 'path:\to\movie-root'
```
Or run it directly from GitHub without cloning:
```
uvx --from git+https://github.com/bennischober/MetaDataScraper metadata-scrapper scan 'path:\to\movie-root'
```

## How it works
The scripts will search recursively for movie files (extensions are configured in ``config.json``). If the script found a movie file, it uses ``ffprobe`` and looks specificly for its streams. If the return value is not ``None``, it proceeds to get all the meta data. Some of the data has to be calculated/created/converted, e.g the raw duration of the movie, the aspect ratio, the size of the movie file (convertion only) and the duration (convertion only).\
To detect black bars in the movie, we need to check some frames of the video by using a ``subprocess`` and using the ffmpeg ``cropdetect`` command. The output will checked against the codec data (using a threshold to be 100% sure, its not just some pixels that can be cut).\
This is why we you need to install the FFmpeg standalone tools (``ffmpeg`` and ``ffprobe``).

## External dependencies

Meta data detection: [ffmpeg](https://www.ffmpeg.org/) (``ffmpeg``/``ffprobe`` binaries)\
Black bar detection: [ffmpeg](https://www.ffmpeg.org/)\
Re-encoding (optional): [HandBrake CLI](https://handbrake.fr/)\
Loading bar and CLI output: [rich](https://github.com/Textualize/rich)\
CLI framework: [typer](https://typer.tiangolo.com/)\
Logging: [loguru](https://github.com/Delgan/loguru)\
Python and package management: [uv](https://docs.astral.sh/uv/)

## Useful commands
The CLI has two commands: ``scan`` (collect metadata) and ``encode`` (re-encode with HandBrake). Use ``--help`` on each for all options.

``--csv`` generates a csv file of the processed data. The results are shown as a table in the console (disable with ``--no-table``).
```
uv run metadata-scrapper scan 'path:\to\movie-root' --csv
```

Scans are cached in a small sqlite database (``metadata.db`` in the current directory, changeable with ``--db``): unchanged files (same mtime and size) are not probed again => repeated scans are much faster. Use ``--rescan`` to force a full re-probe and ``--hash`` to store a fast content hash (first/last MB) for new/changed files.

``--workers N`` controls how many movies are scanned in parallel (default: automatic, ``1`` = sequential).

## Re-encoding with HandBrake
With the ``encode`` command the found movies are re-encoded with HandBrake into an output directory (smaller file size at almost the same quality). Audio and subtitle tracks are kept as-is, the video is encoded with the configured preset/encoder (see [Configuration](#configuration)).\
Preview what would be encoded with ``--dry-run``:
```
uv run metadata-scrapper encode 'path:\to\movie-root' 'D:\encoded' --dry-run
```
Every successful encode is recorded in the sqlite database (preset, encoder, sizes, saved percentage). Movies that were already encoded are skipped on the next run (disable with ``--overwrite``).

## Quality-driven re-encoding (`--quality`)
The fixed preset uses the same quality setting for every movie. With ``--quality`` the tool instead measures each movie and picks the largest CRF that keeps the picture perceptually transparent (VMAF >= target, measured with Netflix's [VMAF](https://github.com/Netflix/vmaf) via ffmpeg):

```
uv run metadata-scrapper encode 'path:\to\movie-root' 'D:\encoded' --quality --remove-bars --target-vmaf 95
```

How it works per movie:
1. Three 10-second samples are extracted and re-encoded by HandBrake at different CRF values (binary search).
2. Each probe is scored against its source with VMAF; the largest CRF whose worst sample still meets the target wins.
3. The whole movie is encoded once with that CRF; the output duration is verified against the source.

Measured on real content (ffmpeg 9, x265 medium, target VMAF 95):

| Movie | Content | Result | Savings |
|---|---|---|---|
| Django Unchained (PAL DVD rip) | grainy live action | CRF 19, VMAF 95.25, bars removed | 64% |
| Bloodshot alternate ending | 1080p live action | CRF 20, VMAF 95.76 | 73% |
| Rick & Morty S01E01 | already-efficient HEVC | skipped: target unreachable | 0% (correctly) |

Notes:
- Movies whose target is provably unreachable (even CRF 14 scores below the floor) or that would not shrink are skipped and remembered in the database, so future runs do not re-probe them.
- ``--remove-bars`` detects stable baked-in black bars (letterbox) and crops them away (irreversible, opt-in): cropdetect runs on several segments and only bars present in all of them are removed.
- Absolute VMAF scores differ between libvmaf versions (the same encode scored 95.76 on ffmpeg 5 and 94.40 on ffmpeg 9); decisions stay consistent because the probing measures and decides with the local toolchain.

## Configuration
The [config.json](config.json) file controls the behaviour of the tool:
- ``file.extensions``: the movie file extensions to search for (e.g. `.mkv`, `.mp4`, `.avi`)
- ``file.output.seperator``: the column separator of the `movies.txt` output file (default: `;`)
- ``ffmpeg.black_bars``: `MIN_WIDTH`/`MIN_HEIGHT` threshold for the black bar (cropdetect) detection
- ``handbrake.preset``: HandBrake preset for re-encoding (default: `Fast 1080p30`)
- ``handbrake.encoder``: HandBrake video encoder (default: `x265`)
- ``codec.hdr``: color space/transfer values that identify HDR content
- ``language``: preferred language for metadata tags (e.g. `en`, `de`)

## Development
### Devcontainer
A [devcontainer](.devcontainer/devcontainer.json) is included (Ubuntu with uv, ffmpeg and handbrake-cli). Open the repository in VS Code and reopen in the container => everything is installed automatically.

### Tests and linting
```
uv run pytest          # run the tests (with coverage report)
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # static type checking
```

### Releasing
Releases are automated: push a tag and the release workflow builds the package and publishes a GitHub release (after running ruff, mypy and the tests):
```
git tag v0.2.1
git push origin v0.2.1
```


## Features for the future
- get missing meta data from external movie APIs, e.g. [OMDB API](http://www.omdbapi.com/) or [List](https://the-api-collective.com/category/media)

## FAQ
- The command ``python``/``uv`` does not work in the command prompt. [Have a look at this.](https://stackoverflow.com/a/13596981)
- "No such filter: 'cropdetect'...". On Windows: Make sure you installed the gpl version of [ffmpeg](https://github.com/BtbN/FFmpeg-Builds/releases). On Linux/UNIX [this might help](http://ffmpeg.org/pipermail/ffmpeg-user/2015-October/028753.html)
- "'HandBrakeCLI' not found!": Install [HandBrake CLI](https://handbrake.fr/downloads.php) and make sure it is in your ``PATH``.

## Additional ressources
[FFmpeg Documentation](https://ffmpeg.org/ffmpeg.html)\
[How the black bar detection works](https://ffmpeg.org/ffmpeg-filters.html#cropdetect)\
[HandBrake CLI options](https://handbrake.fr/docs/en/latest/cli/cli-options.html)\
[rich Documentation](https://rich.readthedocs.io/)\
[typer Documentation](https://typer.tiangolo.com/)\
[loguru Documentation](https://loguru.readthedocs.io/)\
[uv Documentation](https://docs.astral.sh/uv/)
