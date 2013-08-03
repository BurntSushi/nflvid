nflvid is a Python package that facilates the processing of NFL game footage. 
In particular, this library provides routines to do the following:

  - Download game footage from NFL's content provider (Neulion).

  - Download play meta data associated with game footage that, among other 
    things, describes the start time of every play in the game.

  - Cut the game footage into pieces where each piece corresponds to a single 
    play.

  - Provide a few API functions for accessing the file path of a particular
    play by integration with [nflgame](https://github.com/BurntSushi/nflgame).

The methods used in this library rely heavily on the open availability of data 
that could be shut off at any time. More to the point, the content that this 
library requires is large and cannot be distributed easily. Therefore, this 
package's future remains uncertain.

Slicing game footage into play-by-play pieces is done using meta data, which 
can sometimes contain errors. Not all of them are detectable, but when they 
are, nflvid can create a ten-second "stand in" video clip with a textual 
description of the play.

The meta data for when each play starts in the footage is included in this 
repository and is installed automatically.

The actual game footage can either be broadcast footage (with commercials 
removed), or it can be "all-22" (coach) footage. Broadcast footage comes in 
varying qualities (up to 720p HD) while "all-22" footage is limited to only 
standard definition (480p) quality. nflvid faciliates acquiring either, but 
getting coach footage is much more reliable and is therefore the default 
operation. Gathering broadcast footage is possible, but it is buggy.


## Documentation

The API documentation is generated from the code using `epydoc`. A copy of
it can be found here: http://burntsushi.net/doc/nflvid


## Installation

[nflvid is on PyPI](https://pypi.python.org/pypi/nflvid), so it can be 
installed with `pip`:

    pip2 install nflvid


## Dependencies

`nflvid` depends on the following third-party Python packages, which are all 
available in `PyPI` and are installed automatically by `pip` if the above 
method is used.

* [nflgame](https://pypi.python.org/pypi/nflgame)
* [httplib2](https://pypi.python.org/pypi/httplib2)
* [eventlet](https://pypi.python.org/pypi/eventlet)
* [beautifulsoup4](https://pypi.python.org/pypi/beautifulsoup4)

Additionally, the following programs are used to facilitate the downloading and 
slicing of video. They should be available in the standard repositories of any 
Linux distribution:

* [ffmpeg](http://www.ffmpeg.org)
* [imagemagick](http://www.imagemagick.org/) (specifically, the `convert` 
  program)
* [rtmpdump](http://www.imagemagick.org/) (to download rtmp streams)

