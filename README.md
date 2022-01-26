# MetaDataScraper
### Table of contents
- [Overview](#overview)
- [Quickstart](#quickstart)
- [How to install](#how-to-install)
    - [Basic setup](#basic-setup)
    - [Install external dependencies](#install-external-dependencies)
        - [Automatically using the pip requirements file (requires pip!)](#automatically-using-the-pip-requirements-file-requires-pip)
    - [Install MetaDataScraper](#install-metadatascraper)
- [How it works](#how-it-works)
- [External dependencies](#external-dependencies)
- [Useful commands](#useful-commands)
- [Features for the future](#features-for-the-future)
- [FAQ](#faq)
    - [Install the external dependencies manually](#install-the-external-dependencies-manually)
- [Additional ressources](#additional-ressources)

## Overview
A short python script to get video meta data.\
Notes:
>This project uses Python 3.10

>This is an very early state of the tool. Many Null/None checks are missing and by now I´m only looking for .mkv files. These problems will be fixed in the future, but I don't have the time to address them currently.

## Quickstart
Usage: Run the main python script (main.py) with the root of you movies folder (e.g. 'D:\Movies') as parameter.\
Execute this command in the ``Windows Terminal`` or ``CMD``.
```
python main.py 'path:\to\movie-root'
```

## How to install
### Basic setup
The first steps are to install [Python](https://www.python.org/downloads/) (preffered the latest stable, currently 3.10), [FFmpeg](https://www.ffmpeg.org/download.html) ([short tutorial for windows](https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/)) and [pip](https://pypi.org/project/pip/).

### Install external dependencies
#### Automatically using the pip requirements file (requires pip!)
```
pip install -r requirements.txt
```

### Install MetaDataScraper
Run
```
git clone https://github.com/bennischober/MetaDataScraper.git
```

## How it works
The scripts will search recursively for (by now .mkv) movie files. If the script found a movie file, it uses ``ffmpeg.probe()`` and looks specificly for its streams. If the return value is not ``None``, it proceeds to get all the meta data. Some of the data has to be calculated/created/converted, e.g the raw duration of the movie, the aspect ratio, the size of the movie file (convertion only) and the duration (convertion only).\
To detect black bars in the movie, we need to check some frames of the video by using a ``subprocess`` and using the ffmpeg ``cropdetect`` command. The output will checked against the codec data (using a threshold to be 100% sure, its not just some pixels that can be cut).\
This is why we you need to install the ``ffmpeg-python`` package and the FFmpeg standalone tool.

## External dependencies

Meta data detection: [ffmpeg](https://www.ffmpeg.org/) and [ffmpeg python](https://pypi.org/project/ffmpeg-python/)([GitHub](https://github.com/kkroening/ffmpeg-python))\
Black bar detection: [ffmpeg](https://www.ffmpeg.org/)\
Loading bar: [tqdm](https://github.com/tqdm/tqdm)\
Python package installer: [pip](https://pypi.org/project/pip/)

## Useful commands
``-csv`` generates a csv file of the processed data.
```
python main.py 'path:\to\movie-root' -csv
```

## Features for the future
- config.json to change the settings (e.g. movie format, language, output, etc.)
- support (almost) all common movie file formats
- get missing meta data from external movie APIs, e.g. [OMDB API](http://www.omdbapi.com/) or [List](https://the-api-collective.com/category/media)

## FAQ
- The command ``python`` does not work in the command prompt. [Have a look at this.](https://stackoverflow.com/a/13596981)
- "No such filter: 'cropdetect'...". On Windows: Make sure you installed the gpl version of [ffmpeg](https://github.com/BtbN/FFmpeg-Builds/releases). On Linux/UNIX [this might help](http://ffmpeg.org/pipermail/ffmpeg-user/2015-October/028753.html)
#### Install the external dependencies manually:
#### [tqdm](https://github.com/tqdm/tqdm)
```
pip install tqdm
```
#### [ffmpeg-python](https://github.com/kkroening/ffmpeg-python)
```
pip install ffmpeg-python
```

## Additional ressources
[FFmpeg Documentation](https://ffmpeg.org/ffmpeg.html)\
[How the black bar detection works](https://ffmpeg.org/ffmpeg-filters.html#cropdetect)\
[tqdm Documentation](https://tqdm.github.io/)
