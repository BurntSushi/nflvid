A simple library to download, slice and search NFL game footage on a
play-by-play basis.

This library comes with preloaded play-by-play meta data, which
describes the start time of each play in the game footage. However, the
actual footage does not come with this library and is not released by
me. This package therefore provides utilities to batch download NFL Game
Footage from the original source.

Once game footage is downloaded, you can use this library to search
plays and construct a playlist to play in ``vlc`` with the
`nflvid.vlc <http://pdoc.burntsushi.net/nflvid/vlc.m.html>`__ submodule.
