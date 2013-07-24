"""
Introduction
============
A simple library to download, slice and search NFL game footage on a
play-by-play basis.

This library comes with preloaded play-by-play meta data, which describes the
start time of each play in the game footage. However, the actual footage does
not come with this library and is not released by me. This package therefore
provides utilities to batch download NFL Game Footage from the original source.

Once game footage is downloaded, you can use this library to search plays and
construct a playlist to play in any video player.
"""

import gzip
import math
import os
import os.path as path
import socket
import sys
import urllib2

import bs4

import eventlet
httplib2 = eventlet.import_patched('httplib2')
import eventlet.green.subprocess as subprocess

from nflgame import OrderedDict

_xmlf = path.join(path.split(__file__)[0], 'pbp-xml', '%s-%s.xml.gz')
_xml_base_url = 'http://e2.cdnl3.neulion.com/nfl/edl/nflgr/%d/%s.xml'
_coach_url = 'rtmp://neulionms.fcod.llnwd.net/a5306/e1/mp4:' \
             'u/nfl/nfl/coachtapes/%s/%s_all_1600'
_coach_url = (
    'rtmp://neulionms.fcod.llnwd.net',
    'a5306/e1',
    'mp4:u/nfl/nfl/coachtapes/%s/%s_all_1600',
)
_broadcast_url = 'http://nlds82.cdnl3nl.neulion.com/nlds_vod/nfl/vod/' \
                 '%s/%s/%s/%s/2_%s_%s_%s_%s_h_whole_1_%s.mp4.m3u8'

__broadcast_cache = {}  # game eid -> play id -> Play
__coach_cache = {}  # game eid -> play id -> Play


def _eprint(s):
    print >> sys.stderr, s


def broadcast_url(gobj, quality='1600'):
    """
    Returns the HTTP Live Stream URL (an m3u8 file) for the given game
    and quality.

    Note that this does not work with every game (yet). In particular,
    URLs vary unpredictably (to me) from game to game.
    """
    month, day = gobj.eid[4:6], gobj.eid[6:8]
    return _broadcast_url \
        % (gobj.season(), month, day, gobj.gamekey, gobj.gamekey,
           gobj.away.lower(), gobj.home.lower(), gobj.season(), quality)


def coach_url(gobj):
    """
    Returns the rtmp URL as a triple for the coach footage
    of the given game. The elemtns of the triple are::

        (rtmp server, rtmp app name, rtmp playpath)

    Coach video only comes in 1600 quality.
    """
    return (
        _coach_url[0],
        _coach_url[1],
        _coach_url[2] % (gobj.season(), gobj.gamekey),
    )


def footage_full(footage_dir, gobj):
    """
    Returns the path to the full video for a given game inside an nflvid
    footage directory.

    If the full footage doesn't exist, then None is returned.
    """
    fp = _full_path(footage_dir, gobj)
    if not os.access(fp, os.R_OK):
        return None
    return fp


def footage_plays(footage_play_dir, gobj):
    """
    Returns a list of all footage broken down by play inside an nflvid
    footage directory. The list is sorted numerically by play id.

    If no footage breakdown exists for the game provided, then an empty list
    is returned.
    """
    fp = _play_path(footage_play_dir, gobj)
    if not os.access(fp, os.R_OK):
        return []
    return sorted(os.listdir(fp), key=lambda s: int(s[0:-4]))


def footage_play(footage_play_dir, gobj, playid):
    """
    Returns a file path to an existing play slice in the footage play
    directory for the game and play given.

    If the file for the play is not readable, then None is returned.
    """
    gamedir = _play_path(footage_play_dir, gobj)
    fp = path.join(gamedir, '%04d.mp4' % int(playid))
    if not os.access(fp, os.R_OK):
        return None
    return fp


def _full_path(footage_dir, g):
    return path.join(footage_dir, '%s-%s.mp4' % (g.eid, g.gamekey))


def _play_path(footage_play_dir, g):
    return path.join(footage_play_dir, '%s-%s' % (g.eid, g.gamekey))


def _nice_game(gobj):
    return '(Season: %s, Week: %s, %s)' \
           % (gobj.schedule['year'], gobj.schedule['week'], gobj)


def unsliced_plays(footage_play_dir, gobj, coach=True, dry_run=False):
    """
    Scans the game directory inside footage_play_dir and returns a list
    of plays that haven't been sliced yet. In particular, a play is only
    considered sliced if the following file is readable, assuming {playid}
    is its play id::

        {footage_play_dir}/{eid}-{gamekey}/{playid}.mp4

    All plays for the game given that don't fit this criteria will be
    returned in the list.

    If the list is empty, then all plays for the game have been sliced.
    Alternatively, None can be returned if there was a problem retrieving
    the play-by-play meta data.

    If coach is False, then play timings for broadcast footage will be
    used instead of coach timings.

    If dry_run is True, then only the first 10 plays of the game are
    sliced.
    """
    ps = plays(gobj, coach)
    outdir = _play_path(footage_play_dir, gobj)

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
          threads=4, dry_run=False):
    """
    Uses ffmpeg to slice the given footage file into play-by-play pieces.
    The full_footage_file should point to a full game downloaded with
    nflvid-footage and gobj should be the corresponding nflgame.game.Game
    object.

    The footage_play_dir is where the pieces will be saved::

        {footage_play_dir}/{eid}-{gamekey}/{playid}.mp4

    This function will not duplicate work. If a video file exists for
    a particular play, then slice will not regenerate it.

    Note that this function uses an eventlet green pool to run multiple
    ffmpeg instances simultaneously. The maximum number of threads to
    use is specified by threads. This function only terminates when all
    threads have finished processing.

    If coach is False, then play timings for broadcast footage will be
    used instead of coach timings.

    If dry_run is true, then only the first 10 plays of the game are
    sliced.
    """
    outdir = _play_path(footage_play_dir, gobj)
    if not os.access(outdir, os.R_OK):
        os.makedirs(outdir)

    unsliced = unsliced_plays(footage_play_dir, gobj, coach, dry_run)
    if unsliced is None or len(unsliced) == 0:
        _eprint(
            'There are no unsliced plays remaining for game %s %s.\n'
            'If they have not been sliced yet, then the XML play-by-play '
            'meta data may not be available or is corrupt.'
            % (gobj, _nice_game(gobj)))
        return

    pool = eventlet.greenpool.GreenPool(threads)
    for p in unsliced:
        pool.spawn_n(slice_play, footage_play_dir, full_footage_file, gobj, p,
                     0, True)
    pool.waitall()

    _eprint('DONE slicing game %s' % _nice_game(gobj))


def slice_play(footage_play_dir, full_footage_file, gobj, play,
               max_duration=0, cut_scoreboard=True):
    """
    This is just like slice, but it only slices the play provided.
    In typical cases, slice should be used since it makes sure not
    to duplicate work.

    This function will not check if the play-by-play directory for
    gobj has been created.

    max_duration is used to cap the length of a play. This drastically
    cuts down on the time required to slice a game and the storage
    requirements of a game at the cost of potentially missing bigger
    plays. This is particularly useful if you are slicing broadcast
    footage, where imposing a cap at about 15 seconds can decrease
    storage and CPU requirements by more than half without missing much.

    When cut_scoreboard is True, the first 3.0 seconds of
    the play will be clipped to remove the scoreboard view.
    """
    outdir = _play_path(footage_play_dir, gobj)
    st = play.start
    outpath = path.join(outdir, '%s.mp4' % play.idstr())

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
           '-t', duration,
           '-i', full_footage_file,
           '-acodec', 'copy',
           '-vcodec', 'copy',
           outpath,
           ]
    _run_command(cmd)


def download_broadcast(footage_dir, gobj, quality='1600', dry_run=False):
    """
    Starts an ffmpeg process to download the full broadcast of the given
    game with the quality provided. The qualities available are:
    400, 800, 1200, 1600, 2400, 3000, 4500 with 4500 being the best.

    The footage will be saved to the following path::

        footage_dir/{eid}-{gamekey}.mp4

    If footage is already at that path, then a LookupError is raised.

    A full game's worth of footage at a quality of 1600 is about 2GB.
    """
    fp = _full_path(footage_dir, gobj)
    if os.access(fp, os.R_OK):
        raise LookupError('Footage path "%s" already exists.' % fp)

    url = broadcast_url(gobj, quality)

    # Let's check to see if the URL exists. We could let ffmpeg catch
    # the error, but since this is a common error, let's show something
    # nicer than a bunch of ffmpeg vomit.
    resp, _ = httplib2.Http().request(url, 'HEAD')
    if resp['status'] != '200':
        _eprint('BAD URL (http status %s) for game %s: %s'
                % (resp['status'], _nice_game(gobj), url))
        _eprint('FAILED to download game %s' % _nice_game(gobj))
        return

    cmd = ['ffmpeg',
           '-timeout', '60',
           '-i', url]
    if dry_run:
        cmd += ['-t', '30']
    cmd += ['-strict', '-2', fp]

    _eprint('Downloading game %s %s' % (gobj.eid, _nice_game(gobj)))
    if not _run_command(cmd):
        _eprint('FAILED to download game %s' % _nice_game(gobj))
    else:
        _eprint('DONE with game %s' % _nice_game(gobj))


def download_coach(footage_dir, gobj, dry_run=False):
    """
    Starts an rtmpdump process to download the full coach footage of the
    given game. Currently, the only quality available is 1600.

    The footage will be saved to the following path::

        footage_dir/{eid}-{gamekey}.mp4

    If footage is already at that path, then a LookupError is raised.

    A full game's worth of footage at a quality of 1600 is about 1GB.
    """
    fp = _full_path(footage_dir, gobj)
    if os.access(fp, os.R_OK):
        raise LookupError('Footage path "%s" already exists.' % fp)

    server, app, path = coach_url(gobj)

    cmd = ['rtmpdump',
           '--rtmp', server,
           '--app', app,
           '--playpath', path,
           '--timeout', '60',
           ]
    if dry_run:
        cmd += ['--stop', '30']
    cmd += ['-o', fp]

    _eprint('Downloading game %s %s' % (gobj.eid, _nice_game(gobj)))
    status = _run_command(cmd)
    if status is None:
        _eprint('DONE (incomplete) with game %s' % _nice_game(gobj))
    elif not status:
        _eprint('FAILED to download game %s' % _nice_game(gobj))
    else:
        _eprint('DONE with game %s' % _nice_game(gobj))


def _run_command(cmd):
    try:
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        output = p.communicate()[0].strip()

        if p.returncode > 0:
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
    return True


def plays(gobj, coach=True):
    """
    Returns an ordered dictionary of all plays for a particular game
    with timings for the coach footage. If coach is False, then the
    timings will be for the broadcast footage.

    The game must be a nflgame.game.Game object.

    If there is a problem retrieving the data, None is returned.

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
    fp = _xmlf % (gobj.eid, gobj.gamekey)
    if gobj.game_over() and not os.access(fp, os.R_OK):
        try:
            print >> gzip.open(fp, 'w+'), rawxml,
        except IOError:
            _eprint('Could not cache XML data. Please make '
                    '"%s" writable.' % path.dirname(fp))
    return ps


def play(gobj, playid, coach=True):
    """
    Returns a Play object given a game and a play id with timings for
    the coach footage. If coach is False, then the timings will be for
    the broadcast footage.

    The game must be a nflgame.game.Game object.

    If a play with the given id does not exist, None is returned.
    """
    return plays(gobj).get(playid, None)


class Play (object):
    """
    Represents a single play with meta data that ties it to game footage.
    The footage_start corresponds to the 'ArchiveTCIN' or 'CATIN', which
    is when the play starts. Since there is no record of when a play
    stops, the end is computed by using the start time of the next play.
    If it's the last play recorded, then the end time is None.

    The play id is the foreign key that maps to play data stored in nflgame.
    """
    def __init__(self, start, end, playid):
        self.start, self.end, self.playid = start, end, playid

    def idstr(self):
        """Returns a string play id padded with zeroes."""
        return '%04d' % int(self.playid)

    def __str__(self):
        return '(%s, %s, %s)' % (self.playid, self.start, self.end)


class PlayTime (object):
    """
    Represents a footage time point, in the format HH:MM:SS:MMM where
    MMM can be either 2 or 3 digits.
    """
    def __init__(self, point=None, seconds=None):
        """
        Construct a PlayTime object given a point in time in the format
        HH:MM:SS:MMM where MMM can be either 2 or 3 digits.

        Alternatively, seconds can be provided (which may be a float).
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
        Returns a new PlayTime with seconds (int or float) added to self.
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
        Returns this time point as fractional seconds based on milliseconds.
        """
        secs = (self.hh * 60 * 60) + (self.mm * 60) + self.ss
        secs = (1000 * secs) + self.milli
        return float(secs) / 1000.0

    def __cmp__(self, other):
        return cmp(self.fractional(), other.fractional())

    def __sub__(self, other):
        """
        Returns the difference rounded to nearest second between
        two time points.  The 'other' time point must take place before the
        current time point.
        """
        assert other <= self, '%s is not <= than %s' % (other, self)
        return int(round(self.fractional() - other.fractional()))

    def __str__(self):
        return self.__point


def _xml_plays(data, coach=True):
    """
    Parses the XML raw data given into an ordered dictionary of Play
    objects corresponding to coach play timings. If coach is set to
    False, then play timings for the broadcast are retrieved.

    The dictionary is keyed by play id.
    """
    if data is None:
        return None

    # Load everything into a list first, since we need to look ahead to see
    # the next play's start time to compute the current play's duration.
    rows = []
    for row in bs4.BeautifulSoup(data).find_all('row'):
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

        # If this start doesn't procede the last start time, skip it.
        if len(rows) > 0 and start < rows[-1][1]:
            continue
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
        d[playid] = Play(start, end, playid)
    return d


def _get_xml_data(eid=None, gamekey=None, fpath=None):
    """
    Returns the XML play data corresponding to the game given. A game must
    be specified in one of two ways: by providing the eid and gamekey or
    by providing the file path to a gzipped XML file.

    If the XML data is already on disk, it is read, decompressed and returned.

    Otherwise, the XML data is downloaded from the NFL web site. If the data
    doesn't exist yet or there was an error, _get_xml_data returns None.
    """
    assert (eid is not None and gamekey is not None) or fpath is not None

    if fpath is not None:
        return gzip.open(fpath).read()

    fpath = _xmlf % (eid, gamekey)
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
