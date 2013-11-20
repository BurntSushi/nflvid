"""
A simple library to download, slice and search NFL game footage on a
play-by-play basis.

This library comes with preloaded play-by-play meta data, which
describes the start time of each play in the game footage. However,
the actual footage does not come with this library and is not released
by me. This package therefore provides utilities to batch download NFL
Game Footage from the original source.

Once game footage is downloaded, you can use this library to search
plays and construct a playlist to play in `vlc` with the
[nflvid.vlc](http://pdoc.burntsushi.net/nflvid/vlc.m.html) submodule.
"""

import gzip
import json
import math
import multiprocessing.pool
import os
import os.path as path
import socket
import subprocess
import sys
import tempfile
import urllib2

import httplib2

import bs4

from nflgame import OrderedDict

try:
    strtype = basestring
except NameError:  # I have lofty hopes for Python 3.
    strtype = str

__pdoc__ = {}

__broadcast_cache = {}  # game eid -> play id -> Play
__coach_cache = {}  # game eid -> play id -> Play

_xmlf = path.join(path.split(__file__)[0], 'pbp-xml', '%s.xml.gz')
_xml_base_url = 'http://e2.cdnl3.neulion.com/nfl/edl/nflgr/%d/%s.xml'
_coach_url = 'rtmp://neulionms.fcod.llnwd.net/a5306/e1/mp4:' \
             'u/nfl/nfl/coachtapes/%s/%s_all_1600'
_coach_url = (
    'rtmp://neulionms.fcod.llnwd.net',
    'a5306/e1',
    'mp4:u/nfl/nfl/coachtapes/%s/%s_all_1600',
)
_broadcast_url = 'http://nlds82.cdnl3nl.neulion.com/nlds_vod/nfl/vod/' \
                 '%s/%s/%s/%s/%d_%s_%s_%s_%s_h_%s_%s_%s.mp4.m3u8'


def _eprint(s):
    print >> sys.stderr, s


def broadcast_urls(gobj, quality='1600', condensed=False):
    """
    Returns possible HTTP Live Stream URLs (an m3u8 file) for the given
    game and quality. Use `nflvid.url_status` to determine
    if it's a valid URL or not. Alternatively, use
    `nflvid.first_valid_broadcast_url` to retrieve the first valid URL.

    The kludge here is that the broadcast URLs can vary slightly and
    unpredictably from game to game. I haven't discovered a reliable
    means of accurately predicting which URL is correct.

    Note that it is unlikely any URL returned will be valid for
    preseason or postseason games.
    """
    year, month, day = gobj.eid[0:4], gobj.eid[4:6], gobj.eid[6:8]
    if gobj.schedule['season_type'] == 'POST':
        stype = 3
    elif gobj.schedule['season_type'] == 'PRE':
        stype = 1
    else:
        stype = 2

    kind = 'snap2w' if condensed else 'whole'
    return [
        _broadcast_url
        % (year, month, day, gobj.gamekey, stype, gobj.gamekey,
           gobj.away.lower(), gobj.home.lower(), gobj.season(), kind,
           i, quality)
        for i in ['3', '2', '1', '4a']
        # We count down here because higher numbers seem to take precedent.
        # For example, the DEN @ NYG game in week 2 of 2013 regular season
        # game. Using `1` links to valid footage that is only ~40 minutes
        # long. The real stream uses `2`.
        # I have no idea if this is a general rule or not.
    ]


def url_status(url):
    """
    Returns the HTTP status as a string for the given URL. A broadcast
    URL should be considered valid if and only if its HTTP status is
    `200`.
    """
    resp, _ = httplib2.Http().request(url, 'HEAD')
    return resp['status']


def first_valid_broadcast_url(urls):
    """
    Returns the first valid broadcast URL in the list. If there is no
    valid broadcast URL, then `None` is returned.
    """
    for url in urls:
        if url_status(url) == '200':
            return url
    return None


def coach_url(gobj):
    """
    Returns the rtmp URL as a triple for the coach footage of the given
    game. The elements of the triple are:

        (rtmp server, rtmp app name, rtmp playpath)

    Coach video only comes in 1600 quality.
    """
    return (
        _coach_url[0],
        _coach_url[1],
        _coach_url[2] % (gobj.season(), gobj.gamekey),
    )


def footage_full(footage_dir, eid):
    """
    Returns the path to the full video for a given game inside an
    nflvid footage directory.

    If the full footage doesn't exist, then None is returned.
    """
    fp = _full_path(footage_dir, eid)
    if not os.access(fp, os.R_OK):
        return None
    return fp


def footage_plays(footage_play_dir, eid):
    """
    Returns a list of all footage broken down by play inside an nflvid
    footage directory. The list is sorted numerically by play id.

    If no footage breakdown exists for the game provided, then an empty
    list is returned.
    """
    fp = _play_path(footage_play_dir, eid)
    if not os.access(fp, os.R_OK):
        return []
    return sorted(os.listdir(fp), key=lambda s: int(s[0:-4]))


def footage_play(footage_play_dir, eid, playid, stat=True):
    """
    Returns a file path to an existing play slice in the footage play
    directory for the game and play given.

    If the file for the play is not readable, then `None` is returned.

    If `stat` is `False`, then the file's access will not be checked.
    """
    gamedir = _play_path(footage_play_dir, eid)
    fp = path.join(gamedir, '%04d.mp4' % int(playid))
    if stat and not os.access(fp, os.R_OK):
        return None
    return fp


def _full_path(footage_dir, eid):
    return path.join(footage_dir, '%s.mp4' % eid)


def _play_path(footage_play_dir, eid):
    return path.join(footage_play_dir, '%s' % eid)


def _nice_game(gobj):
    return '(Season: %s, Week: %s, %s)' \
           % (gobj.schedule['year'], gobj.schedule['week'], gobj)


def unsliced_plays(footage_play_dir, gobj, coach=True, dry_run=False):
    """
    Scans the game directory inside footage_play_dir and returns a list
    of plays that haven't been sliced yet. In particular, a play is
    only considered sliced if the following file is readable, assuming
    {playid} is its play id:

        {footage_play_dir}/{eid}/{playid}.mp4

    All plays for the game given that don't fit this criteria will be
    returned in the list.

    If the list is empty, then all plays for the game have been sliced.
    Alternatively, `None` can be returned if there was a problem
    retrieving the play-by-play meta data.

    If `coach` is `False`, then play timings for broadcast footage will
    be used instead of coach timings.

    If `dry_run` is `True`, then only the first 10 plays of the game
    are sliced.
    """
    ps = plays(gobj, coach)
    outdir = _play_path(footage_play_dir, gobj.eid)

    unsliced = []
    if ps is None:
        return None
    for i, p in enumerate(ps.values()):
        if dry_run and i >= 10:
            break
        pid = p.idstr()
        if not os.access(path.join(outdir, '%s.mp4' % pid), os.R_OK):
            unsliced.append(p)
    return unsliced


def slice(footage_play_dir, full_footage_file, gobj, coach=True,
          num_parallel=4, dry_run=False):
    """
    Uses `ffmpeg` to slice the given footage file into play-by-play
    pieces.  The `full_footage_file` should be a path to a full
    game downloaded with `nflvid-footage` and `gobj` should be the
    corresponding `nflgame.game.Game` object.

    The `footage_play_dir` is where the pieces will be saved:

        {footage_play_dir}/{eid}/{playid}.mp4

    This function will not duplicate work. If a video file exists for
    a particular play, then slice will not regenerate it.

    Note that this function uses a `multiprocessing` pool to run
    multiple `ffmpeg` instances simultaneously. The maximum number of
    simultaneously executing `ffmpeg` commands to use is specified by
    `num_parallel`. This function only terminates when all `ffmpeg`
    commands have finished processing.

    If `coach` is `False`, then play timings for broadcast footage will
    be used instead of coach timings.

    If `dry_run` is `True`, then only the first 10 plays of the game
    are sliced.
    """
    outdir = _play_path(footage_play_dir, gobj.eid)
    if not os.access(outdir, os.R_OK):
        os.makedirs(outdir)

    unsliced = unsliced_plays(footage_play_dir, gobj, coach, dry_run)
    if unsliced is None or len(unsliced) == 0:
        # Only show an annoying error message if there are no sliced
        # plays on disk.
        if not footage_plays(footage_play_dir, gobj.eid):
            _eprint(
                'There are no unsliced plays remaining for game %s %s.\n'
                'If they have not been sliced yet, then the XML play-by-play '
                'meta data may not be available or is corrupt.'
                % (gobj, _nice_game(gobj)))
        return

    # If this is broadcast footage, we need to find the offset of each play.
    # My current estimate is that the offset is the difference between the
    # the reported game end time and the actual game end time.
    # (This only applies to broadcast footage. Coach footage is well behaved.)
    offset = 0
    if not coach:
        reported = unsliced[0].game_end  # Any play will do.
        actual = _video_duration(full_footage_file)
        offset = reported.fractional() - actual.fractional()

        # Add a little padding...
        offset += 2

        # Something has gone horribly wrong.
        if offset < 0:
            offset = 0

    max_dur = 0 if coach else 25
    pool = multiprocessing.pool.ThreadPool(num_parallel)

    def doslice(p):
        slice_play(footage_play_dir, full_footage_file, gobj, p,
                   max_dur, coach, offset)
    pool.map(doslice, unsliced)

    _eprint('DONE slicing game %s %s' % (gobj.eid, _nice_game(gobj)))


def slice_play(footage_play_dir, full_footage_file, gobj, play,
               max_duration=0, cut_scoreboard=True, offset=0):
    """
    This is just like `nflvid.slice`, but it only slices the play
    provided.  In typical cases, `nflvid.slice` should be used since it
    makes sure not to duplicate work.

    This function will not check if the play-by-play directory for
    `gobj` has been created.

    `max_duration` is used to cap the length of a play. This
    drastically cuts down on the storage requirements of a game at the
    cost of potentially missing longer plays. This is particularly
    useful if you are slicing broadcast footage, where imposing a cap
    at about 15 seconds can decrease storage requirements by more than
    half without missing much.

    When `cut_scoreboard` is `True`, the first 3.0 seconds of the play
    will be clipped to remove the scoreboard view.

    When `offset` is greater than `0`, it is subtracted from the start
    time of `play` to get the actual start time used.
    """
    outdir = _play_path(footage_play_dir, gobj.eid)
    st = play.start
    outpath = path.join(outdir, '%s.mp4' % play.idstr())

    st = st.add_seconds(-offset)
    et = play.end
    if et is None:  # Probably the last play of the game.
        et = st.add_seconds(40)
    if max_duration > 0 and (et.seconds() - st.seconds()) > max_duration:
        et = st.add_seconds(max_duration)

    if cut_scoreboard:
        st = st.add_seconds(3.0)

    dr = PlayTime(seconds=et.fractional() - st.fractional())

    start_time = '%02d:%02d:%02d.%d' % (st.hh, st.mm, st.ss, st.milli)
    duration = '%02d:%02d:%02d.%d' % (dr.hh, dr.mm, dr.ss, dr.milli)
    cmd = ['ffmpeg',
           '-ss', start_time,
           '-i', full_footage_file,
           '-acodec', 'copy',
           '-vcodec', 'copy',
           '-t', duration,
           outpath,
           ]
    _run_command(cmd)


def artificial_slice(footage_play_dir, gobj, gobj_play):
    """
    Creates a video file that contains a single static image with a
    textual description of the play. The purpose is to provide some
    representation of a play even if its video form doesn't exist. (Or
    more likely, the play-by-play meta data for that play is corrupt.)

    This function requires the use of ImageMagick's `convert` with
    pango support.

    Note that `gobj_play` is an `nflgame.game.Play` object and not a
    `nflvid.Play` object.
    """
    outdir = _play_path(footage_play_dir, gobj.eid)
    outpath = path.join(outdir, '%04d.mp4' % int(gobj_play.playid))

    pango = '<span size="20000" foreground="white">'
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.png') as tmp:
        cmd = ['convert',
               '-size', '640x480',  # size of coach footage. configurable?
               '-background', 'black',
               'pango:\n\n\n\n\n\n\n\n\n\n%s%s</span>' % (pango, gobj_play),
               tmp.name,
               ]
        _run_command(cmd)

        cmd = ['ffmpeg',
               '-f', 'image2',
               '-loop', '1',
               '-r:v', '7',
               '-i', tmp.name,
               '-pix_fmt', 'yuv420p',
               '-an',
               '-t', '10',
               outpath,
               ]
        _run_command(cmd)


def download_broadcast(footage_dir, gobj, quality='1600', dry_run=False,
                       condensed=False):
    """
    Starts an `ffmpeg` process to download the full broadcast of the
    given game with the quality provided. The qualities available are:
    400, 800, 1200, 1600, 2400, 3000, 4500 with 4500 being the best.

    The footage will be saved to the following path:

        footage_dir/{eid}.mp4

    If footage is already at that path, then an
    `exceptions.LookupError` is raised.

    A full game's worth of broadcast footage at a quality of 1600 is
    about **2GB**.

    If `dry_run` is `True`, then only the first 30 seconds of the game
    will be downloaded. Use this to quickly make sure everything is
    working correctly.

    If `condensed` is `True`, then a small recap of the game will be
    downloaded instead.
    """
    fp = _full_path(footage_dir, gobj.eid)
    if os.access(fp, os.R_OK):
        raise LookupError('Footage path "%s" already exists.' % fp)

    urls = broadcast_urls(gobj, quality, condensed=condensed)
    url = first_valid_broadcast_url(urls)
    if url is None:
        _eprint('BAD URLs for game %s: %s'
                % (_nice_game(gobj), ', '.join(urls)))
        _eprint('FAILED to download game %s' % _nice_game(gobj))
        return

    cmd = ['ffmpeg']
    if not _is_avconv():
        cmd += ['-timeout', '120']
    cmd += ['-i', url]
    if dry_run:
        cmd += ['-t', '30']
    cmd += ['-absf', 'aac_adtstoasc',  # no idea. ffmpeg says I need it though.
            '-acodec', 'copy',
            '-vcodec', 'copy',
            fp,
            ]

    _eprint('Downloading game %s %s' % (gobj.eid, _nice_game(gobj)))
    if not _run_command(cmd):
        _eprint('FAILED to download game %s' % _nice_game(gobj))
    else:
        _eprint('DONE with game %s %s' % (gobj.eid, _nice_game(gobj)))


def download_coach(footage_dir, gobj, dry_run=False):
    """
    Starts an `rtmpdump` process to download the full coach footage of
    the given game. Currently, the only quality available is 1600.

    The footage will be saved to the following path:

        footage_dir/{eid}.mp4

    If footage is already at that path, then an
    `exceptions.LookupError` is raised.

    A full game's worth of footage at a quality of 1600 is about
    **1GB**.
    """
    fp = _full_path(footage_dir, gobj.eid)
    if os.access(fp, os.R_OK):
        raise LookupError('Footage path "%s" already exists.' % fp)

    server, app, path = coach_url(gobj)

    cmd = ['rtmpdump',
           '--rtmp', server,
           '--app', app,
           '--playpath', path,
           '--timeout', '10',
           ]
    if dry_run:
        cmd += ['--stop', '30']
    cmd += ['-o', fp]

    _eprint('Downloading game %s %s' % (gobj.eid, _nice_game(gobj)))
    status = _run_command(cmd)
    if status is None:
        _eprint('DONE (incomplete) with game %s %s'
                % (gobj.eid, _nice_game(gobj)))
    elif not status:
        _eprint('FAILED to download game %s %s' % (gobj.eid, _nice_game(gobj)))
        try:
            os.remove(fp)
        except OSError:
            pass
    else:
        fp_size = 0
        try:
            fp_size = os.stat(fp).st_size
        except OSError:
            pass
        except AttributeError:
            pass
        if fp_size > 0:
            _eprint('DONE with game %s %s' % (gobj.eid, _nice_game(gobj)))
        else:
            _eprint('FAILED to download game %s %s'
                    % (gobj.eid, _nice_game(gobj)))
            _eprint('No data retrieved. Maybe coach footage does not exist '
                    'yet?')
            try:
                os.remove(fp)
            except OSError:
                pass


def _run_command(cmd):
    try:
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        output = p.communicate()[0].strip()

        if p.returncode != 0:
            err = subprocess.CalledProcessError(p.returncode, cmd)
            err.output = output
            raise err
    except subprocess.CalledProcessError, e:
        # A hack for rtmpdump...
        if e.returncode == 2 and cmd[0] == 'rtmpdump':
            return None
        indent = lambda s: '\n'.join(map(lambda l: '   %s' % l, s.split('\n')))
        _eprint("Could not run '%s' (exit code %d):\n%s"
                % (' '.join(cmd), e.returncode, indent(e.output)))
        return False
    except OSError, e:
        _eprint("Could not run '%s' (errno: %d): %s"
                % (' '.join(cmd), e.errno, e.strerror))
        return False
    return output or True


def plays(gobj, coach=True):
    """
    Returns an ordered dictionary of all plays for a particular game
    with timings for the coach footage. If `coach` is `False`, then the
    timings will be for the broadcast footage.

    The game `gobj` must be an `nflgame.game.Game` object.

    If there is a problem retrieving the data, `None` is returned.

    If the game is over, then the XML data is saved to disk.
    """
    if coach:
        cache = __coach_cache
    else:
        cache = __broadcast_cache

    if gobj.game_over() and gobj.eid in cache:
        return cache[gobj.eid]

    rawxml = _get_xml_data(gobj.eid, gobj.gamekey)
    ps = _xml_plays(rawxml, coach)
    if ps is None:
        return None
    if len(ps) == 0:
        _eprint('Could not find timing nodes in XML data, '
                'which provide the start time of each play.')
        return None
    __broadcast_cache[gobj.eid] = ps

    # Save the XML data to disk if the game is over.
    fp = _xmlf % gobj.eid
    if gobj.game_over() and not os.access(fp, os.R_OK):
        try:
            print >> gzip.open(fp, 'w+'), rawxml,
        except IOError:
            _eprint('Could not cache XML data. Please make '
                    '"%s" writable.' % path.dirname(fp))
    return ps


def play(gobj, playid, coach=True):
    """
    Returns a `nflvid.Play` object given a game and a play id with
    timings for the coach footage. If `coach` is `False`, then the
    timings will be for the broadcast footage.

    The game `gobj` must be an `nflgame.game.Game` object.

    If a play with the given id does not exist, `None` is returned.
    """
    return plays(gobj).get(playid, None)


class Play (object):
    """
    Represents the start and end timings of single play in coach or
    broadcast footage.
    """

    def __init__(self, start, end, playid, game_end):
        self.start = start
        """
        Corresponds to the `ArchiveTCIN` or `CATIN` field in the source
        data. `ArchiveTCIN` is used for broadcast footage while `CATIN`
        is used for coach footage.
        """

        self.end = end
        """
        The end time of the play. This is typically the start time of
        the next play (from `ArchiveTCIN` or `CATIN`). When the next
        play isn't available, this is `None`.
        """

        self.playid = playid
        """
        A numeric play identifier that serves as a foreign key from an
        `nflgame.game.Play` object to a `nflvid.Play` object.
        """

        self.game_end = game_end
        """
        Corresponds to the `endTime` of the broadcast footage for the
        game that this play belongs to. It is used to compute a correct
        offset of the start time for the play.
        """

    def idstr(self):
        """Returns a string play id padded with zeroes."""
        return '%04d' % int(self.playid)

    def __str__(self):
        return '(%s, %s, %s)' % (self.playid, self.start, self.end)


class PlayTime (object):
    """
    Represents a footage time point retrieved from the source XML
    meta data.
    """
    __pdoc__['hh'] = 'The hour portion of the play time.'
    __pdoc__['mm'] = 'The minutes portion of the play time.'
    __pdoc__['ss'] = 'The seconds portion of the play time.'
    __pdoc__['milli'] = 'The milliseconds portion of the play time.'

    def __init__(self, point=None, seconds=None):
        """
        Construct a PlayTime object given a `point` in time in the
        format `HH:MM:SS:MMM` where `MMM` can be either 2 or 3 digits.

        Alternatively, `seconds` can be provided (which may be a
        float).
        """
        if seconds is not None:
            milli = int(1000 * (seconds - math.floor(seconds)))

            seconds = int(math.floor(seconds))
            hh = seconds / 3600

            seconds -= hh * 3600
            mm = seconds / 60

            seconds -= mm * 60
            ss = seconds

            self.hh, self.mm, self.ss, self.milli = hh, mm, ss, milli
            self.__point = '%02d:%02d:%02d:%03d' % (hh, mm, ss, milli)
            return

        self.__point = point
        self.__coach = False

        try:
            parts = self.__point.split(':')
            if len(parts[3]) == 3:
                self.__coach = True
            parts = map(int, parts)
        except ValueError:
            assert False, 'Bad play time format: %s' % self.__point

        if len(parts) != 4:
            assert False, 'Expected 4 parts but got %d in: %s' \
                % (len(parts), self.__point)

        self.hh, self.mm, self.ss, self.milli = parts

        # I believe milliseconds is given in tens of milliseconds
        # for the ArchiveTCIN node. But the CATIN node (coach timing)
        # provides regular milliseconds.
        if not self.__coach:
            self.milli *= 10

    def add_seconds(self, seconds):
        """
        Returns a new PlayTime with `seconds` (int or float) added to
        self.
        """
        return PlayTime(seconds=self.fractional() + seconds)

    def seconds(self):
        """
        Returns this time point rounded to the nearest second.
        """
        secs = (self.hh * 60 * 60) + (self.mm * 60) + self.ss
        if self.milli >= 50:
            secs += 1
        return secs

    def fractional(self):
        """
        Returns this time point as fractional seconds based on
        milliseconds.
        """
        secs = (self.hh * 60 * 60) + (self.mm * 60) + self.ss
        secs = (1000 * secs) + self.milli
        return float(secs) / 1000.0

    def __cmp__(self, other):
        return cmp(self.fractional(), other.fractional())

    def __sub__(self, other):
        """
        Returns the difference rounded to nearest second between two
        time points.  The `other` time point must take place before the
        current time point.
        """
        assert other <= self, '%s is not <= than %s' % (other, self)
        return int(round(self.fractional() - other.fractional()))

    def __str__(self):
        return self.__point


def _video_duration(fp):
    """
    Returns the duration of the entire video at file path `fp` as a
    `nflvid.PlayTime` object.

    If there was a problem using `ffprobe` to get the duration, `None`
    is returned.
    """
    cmd = ['ffprobe', '-loglevel', 'error', '-show_format', fp,
           '-print_format', 'json']
    out = _run_command(cmd)
    if not out:
        return None
    return PlayTime(seconds=float(json.loads(out)['format']['duration']))


def _xml_plays(data, coach=True):
    """
    Parses the XML raw string `data` given into an ordered dictionary
    of `nflvid.Play` objects corresponding to coach play timings. If
    `coach` is set to `False`, then play timings for the broadcast are
    retrieved.

    The dictionary is keyed by play id.

    A second return value, the ending time of the broadcast footage,
    is also returned. (This is used to compute an offset between the
    ArchiveTCIN time and when the play really starts.)
    """
    if data is None:
        return None
    soup = bs4.BeautifulSoup(data)

    game_end_time = soup.find('dataset').get('endtime', None)
    if game_end_time is not None:
        game_end_time = PlayTime(game_end_time.strip())

    # Load everything into a list first, since we need to look ahead to see
    # the next play's start time to compute the current play's duration.
    rows = []
    for row in soup.find_all('row'):
        playid = row.find('id')
        if not playid:
            playid = row.get('playid', None)
            if not playid:
                continue
            playid = playid.strip()
        else:
            playid = playid.get_text().strip()

        if coach:
            start = row.find('catin')
        else:
            start = row.find('archivetcin')
        if not start:
            continue
        start = PlayTime(start.get_text().strip())
        rows.append((playid, start, row))

    # A predicate for determining whether to ignore a row or not in our final
    # result set. For example, timeouts take a lot of time but aren't needed
    # for play-by-play footage.
    def ignore(row):
        if 'playdescription' in row.attrs:
            if row['playdescription'].lower().startswith('timeout'):
                return True
            if row['playdescription'].lower().startswith('two-minute'):
                return True

        # Did we miss anything?
        if 'preplaybyplay' in row.attrs:
            if row['preplaybyplay'].lower().startswith('timeout'):
                return True
        return False

    d = OrderedDict()
    for i, (playid, start, row) in enumerate(rows):
        if ignore(row):
            continue
        end = None
        if i < len(rows) - 1:
            end = rows[i+1][1]
        d[playid] = Play(start, end, playid, game_end_time)
    return d


def _get_xml_data(eid=None, gamekey=None, fpath=None):
    """
    Returns the XML play data corresponding to the game given. A game
    must be specified in one of two ways: by providing the `eid` and
    `gamekey` or by providing the file path `fpath` to a gzipped XML
    file.

    If the XML data is already on disk, it is read, decompressed and
    returned.

    Otherwise, the XML data is downloaded from the NFL web
    site. If the data doesn't exist yet or there was an error,
    `nflvid._get_xml_data` returns None.
    """
    assert (eid is not None and gamekey is not None) or fpath is not None

    if fpath is not None:
        return gzip.open(fpath).read()

    fpath = _xmlf % eid
    if os.access(fpath, os.R_OK):
        return gzip.open(fpath).read()
    try:
        year = int(eid[0:4])
        month = int(eid[4:6])
        if month <= 3:
            year -= 1
        u = _xml_base_url % (year, gamekey)  # The year and the game key.
        return urllib2.urlopen(u, timeout=10).read()
    except urllib2.HTTPError, e:
        _eprint(e)
    except socket.timeout, e:
        _eprint(e)
    return None


def _is_avconv():
    """
    Returns `True` if the `ffmpeg` binary is really `avconv`.
    """
    out = _run_command(['ffmpeg', '-version'])
    return out and isinstance(out, strtype) and 'DEPRECATED' in out
