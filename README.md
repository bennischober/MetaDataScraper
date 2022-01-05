# MetaDataScraper
A short python script to get video metadata.

Usage: Run the main python script (main.py) with the root of you movies folder (e.g. 'D:\Movies')

Run
```
git clone https://github.com/bennischober/MetaDataScraper.git
```

```
python main.py 'path:\to\root'
```

Notes:
>This project uses Python 3.10

>By now I´m only looking for *.mkv files recursively (common video datatypes will follow)



Meta data detection with: [ffmpeg](https://www.ffmpeg.org/) and [ffmpeg python](https://pypi.org/project/ffmpeg-python/)

Black bar detection with: [ffmpeg](https://www.ffmpeg.org/)

Open Movie APIS:
Usage: Get missing meta data (publication etc.)
[OMDB API](http://www.omdbapi.com/)
[List](https://the-api-collective.com/category/media)
