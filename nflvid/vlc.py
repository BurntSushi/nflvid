'''
This submodule exposes a couple of convenience functions for opening a
sequence of plays with `vlc`. In order to use this submodule, you must
have [nfldb](https://github.com/BurntSushi/nfldb) installed.

This module is for picky users where just running `vlc play1.mp4
play2.mp4 ...` isn't enough. Namely, this module instructs vlc to write
a text marquee for each play describing the current game situation and
a textual description of the play.

For most use cases, you will only need the `nflvid.vlc.watch` function.
It can take the results of `nfldb.Query.as_plays`, and open `vlc`
with a playlist corresponding to available footage for each play. As
a simple example, let's watch all available Adrian Peterson 50+ yard
rushes:

    #!python
    from nfldb import connect, Query
    import nflvid.vlc

    db = connect()
    q = Query(db).player(full_name='Adrian Peterson').play(rushing_yds__ge=50)
    nflvid.vlc.watch(db, q.as_plays())

Or, we can write more complex queries using disjunctions. For example,
watching all plays that are either third or fourth down attempts:

    #!python
    from nfldb import connect, Query, QueryOR
    import nflvid.vlc

    db = connect()

    # Build the "OR" part of our query.
    third_or_fourth = QueryOR(db)
    third_or_fourth.play(third_down_att=1, fourth_down_att=1)

    # Now "AND" it with criteria that selects a single game.
    plays = Query(db).game(gsis_id='2012090904').andalso(third_or_fourth)

    # Play it in VLC.
    nflvid.vlc.watch(db, plays.as_plays())

Sometimes nfldb's query interface isn't expressive enough to return
exactly what we want. For example, we might want to look at all of
Calvin Johnson's receptions in the 2012 season that ended within the
opponent's 5 yard line without scoring. We can use nfldb to retrieve
all of Johnson's non-scoring receptions, and then use a loop to filter
those down to exactly what we want to see. We do this because nfldb
does not have data on where plays end.

    #!python
    from nfldb import connect, FieldPosition, Query
    import nflvid.vlc

    db = connect()
    q = Query(db)

    # Specify all of CJ's non-scoring receptions in the 2012 regular season.
    q.game(season_year=2012, season_type='Regular')
    q.player(full_name="Calvin Johnson")
    q.play(receiving_rec=1, receiving_tds=0)

    # inside takes a field position and a statistical category, and
    # returns a predicate. The predicate takes a play and returns
    # true if and only if that play ends inside the field position
    # determined by adding the statistical category to the start of the
    # play. (i.e., play yardline + receiving yards determines the field
    # position where CJ was tackled.)
    def inside(field, stat):
        cutoff = FieldPosition.from_str(field)
        return lambda play: play.yardline + getattr(play, stat) >= cutoff
    watch = filter(inside('OPP 5', 'receiving_yds'), q.as_plays())

    # Watch the plays!
    nflvid.vlc.watch(db, watch, '/m/nfl/coach/pbp')
'''
# This module uses auto-updating marquees by exploiting the XSPF play
# list format to inject meta data for each play. See the `xspf_track`
# template for the details.

from __future__ import absolute_import, division, print_function
import os
import os.path
import re
import subprocess
import tempfile
import urllib
import xml.sax.saxutils

import nfldb

import nflvid


_games = {}
"""
A cache of games that is filled on the first call to
`nflvid.vlc.make_xspf`.
"""


marquee = 'marq{marquee=$d,size=18}' \
          ':marq{marquee=$n/%d,size=18,position=9}' \
          ':marq{marquee=$b,size=18,position=10}'
"""The marquee command to pass to `vlc`."""


# The main XSPF template to use.
_xspf_template = '''<?xml version="1.0" encoding="UTF-8"?>
<playlist xmlns="http://xspf.org/ns/0/"
          xmlns:vlc="http://www.videolan.org/vlc/playlist/ns/0/" version="1">
    <title>nflvid playlist</title>
    <trackList>
{track_list}
    </trackList>
</playlist>
'''


# If you want to add more meta data items here, then see the XSPF spec
# for other available elements. Namely, section 4.1.1.2.14.1.1.1 at
# http://xspf.org/xspf-v1.html#rfc.section.4.1.1.2.14.1.1.1
#
# We are shoe-horning data this way so that we can use VLC's marquee feature:
# https://wiki.videolan.org/Documentation:Modules/marq/
_xspf_track = '''
        <track>
            <title>{title}</title>
            <annotation>{desc}</annotation>
            <location>file://{location}</location>
            <trackNum>{track_num}</trackNum>
            <album>{situation}</album>
        </track>
'''


def _nice_down(down):
    """Returns the integer down with a suffix. e.g., `1st`."""
    return {
        1: '1st', 2: '2nd', 3: '3rd', 4: '4th',
    }.get(down, '???')


def _strip_time(desc):
    """Strips time from play description."""
    return re.sub('^\([^)]+\)', '', desc).strip()


def _play_path(footage_play_dir, play):
    return nflvid.footage_play(footage_play_dir, play.gsis_id, play.play_id)


def plays_and_paths(plays, footage_play_dir=None):
    """
    Given a list of `nfldb.Play` objects, return an association list
    with `nfldb.Play` objects and their corresponding file paths of
    the video of the play.

    Note that the returned association list may have fewer items than
    `plays` since some plays may not have any footage.

    If `footage_play_dir` is `None`, then the value of the
    `NFLVID_FOOTAGE_PLAY_DIR` environment variable is used.
    """
    footage_play_dir = footage_play_dir or os.getenv('NFLVID_FOOTAGE_PLAY_DIR')
    if not footage_play_dir:
        raise IOError('Invalid footage play directory %s' % footage_play_dir)

    al = []
    for play in plays:
        path = _play_path(footage_play_dir, play)
        if path is not None:
            al.append((play, path))
    return al


def make_xspf(db, play_paths):
    """
    Given an association list of `nfldb.Play` objects with a file path
    to the video of that play, return a file path to an XSPF playlist
    file corresponding to the plays given. The onus is on the caller
    to remove the XSPF file.

    `db` should be a psycopg2 database connection returned from
    `nfldb.connect`. It is used to build meta data for games.
    """
    def path_encode(p):
        p = urllib.pathname2url(os.path.realpath(p))
        if not p.startswith('/'):
            p = '/' + p
        return p

    if len(_games) == 0:
        for game in nfldb.Query(db).as_games():
            _games[game.gsis_id] = game

    escape = xml.sax.saxutils.escape
    tracks = []
    for i, (play, path) in enumerate(play_paths, 1):
        path = path_encode(path)
        context = str(_games[play.gsis_id])
        context += '\n' + _strip_time(play.description)
        situation = str(play.time)
        if play.down > 0:
            down = _nice_down(play.down)
            situation += ' (%s and %s)' % (down, play.yards_to_go)
        s = _xspf_track.format(
            title=play.play_id, location=escape(path), desc=escape(context),
            situation=escape(situation), track_num=i)
        tracks.append(s)

    temp = tempfile.NamedTemporaryFile(suffix='.xspf', delete=False)
    print(_xspf_template.format(track_list='\n'.join(tracks)), file=temp)
    return temp.name


def watch(db, plays, footage_play_dir=None, verbose=False, hide_marquee=False):
    """
    Opens an instance of `vlc` with a playlist corresponding to
    available footage for the `plays` given, where `plays` should be a
    list of `nfldb.Play` objects. There are two pertinent features of
    this function. The first is discovering the available play footage.
    The second is creating an XSPF playlist with pertinent meta data
    that is used to overlay text for each play (like the current game
    situation).

    If `footage_play_dir` is `None`, then the value of the
    `NFLVID_FOOTAGE_PLAY_DIR` environment variable is used.

    If `verbose` is `True`, then the stdout and stderr of `vlc` will be
    inherited from the current process.

    If `hide_marquee` is `True`, then no overlay text will be written
    on the plays.
    """
    footage_play_dir = footage_play_dir or os.getenv('NFLVID_FOOTAGE_PLAY_DIR')
    if not footage_play_dir:
        raise IOError('Invalid footage play directory %s' % footage_play_dir)

    out = None
    if not verbose:
        out = open(os.devnull)

    play_paths = nflvid.vlc.plays_and_paths(plays,
                                            footage_play_dir=footage_play_dir)
    if len(play_paths) == 0:
        raise LookupError(
            'No video of plays found matching the criteria given.')

    if hide_marquee:
        cmd = ['vlc'] + [x[1] for x in play_paths]
    else:
        playlist = make_xspf(db, play_paths)
        cmd = ['vlc', '--sub-filter', marquee % len(play_paths), playlist]

    subprocess.check_call(cmd, stdout=out, stderr=out)
    if not hide_marquee:
        os.unlink(playlist)
