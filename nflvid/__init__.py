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

_footage_url = 'http://nlds82.cdnl3nl.neulion.com/nlds_vod/nfl/vod/' \
               '%s/%s/%s/%s/2_%s_%s_%s_%s_h_whole_1_%s.mp4.m3u8'

__play_cache = {}  # game eid -> play id -> Play


def footage_url(gobj, quality='1600'):
    month, day = gobj.eid[4:6], gobj.eid[6:8]
    return _footage_url \
        % (gobj.season(), month, day, gobj.gamekey, gobj.gamekey,
           gobj.away.lower(), gobj.home.lower(), gobj.season(), quality)


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


def _full_path(footage_dir, g):
    return path.join(footage_dir, '%s-%s.mp4' % (g.eid, g.gamekey))


def _nice_game(gobj):
    return '(Season: %s, Week: %s, %s)' \
           % (gobj.schedule['year'], gobj.schedule['week'], gobj)


def footage_plays(footage_dir, gobj):
    """
    Returns a list of all footage broken down by play inside an nflvid
    footage directory. The list is sorted numerically by play id.

    If no footage breakdown exists for the game provided, then an empty list
    is returned.
    """
    fp = path.join(footage_dir, '%s-%s' % (gobj.eid, gobj.gamekey))
    if not os.access(fp, os.R_OK):
        return []
    entries = filter(lambda f: f != 'full.mp4', os.listdir(fp))
    return sorted(entries, key=lambda s: int(s[0:-4]))


def download(footage_dir, gobj, quality='1600', dry_run=False):
    """
    Starts an ffmpeg process to download the full footage of the given
    game with the quality provided. The qualities available are:
    400, 800, 1200, 1600, 2400, 3000, 4500 with 4500 being the best.

    The footage will be saved to the following path::

        footage_dir/{eid}-{gamekey}/full.mp4

    If footage is already at that path, then a LookupError is raised.

    A full game's worth of footage at a quality of 1600 is about 2GB.
    """
    fp = _full_path(footage_dir, gobj)
    if os.access(fp, os.R_OK):
        raise LookupError('Footage path "%s" already exists.' % fp)

    url = footage_url(gobj, quality)

    # Let's check to see if the URL exists. We could let ffmpeg catch
    # the error, but since this is a common error, let's show something
    # nicer than a bunch of ffmpeg vomit.
    resp, _ = httplib2.Http().request(url, 'HEAD')
    if resp['status'] != '200':
        print >> sys.stderr, 'BAD URL (http status %s) for game %s: %s' \
            % (resp['status'], _nice_game(gobj), url)
        print >> sys.stderr, 'FAILED to download game %s' % _nice_game(gobj)
        return

    cmd = ['ffmpeg', '-i', url]
    if dry_run:
        cmd += ['-t', '30']
    cmd += ['-strict', '-2', fp]
    try:
        print >> sys.stderr, 'Downloading game %s %s' \
            % (gobj.eid, _nice_game(gobj))
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        output = p.communicate()[0].strip()

        if p.returncode > 0:
            err = subprocess.CalledProcessError(p.returncode, cmd)
            err.output = output
            raise err

        print >> sys.stderr, 'DONE with game %s' % _nice_game(gobj)
    except subprocess.CalledProcessError, e:
        indent = lambda s: '\n'.join(map(lambda l: '   %s' % l, s.split('\n')))
        print >> sys.stderr, "Could not run '%s' (exit code %d):\n%s" \
            % (' '.join(cmd), e.returncode, indent(e.output))
        print >> sys.stderr, 'FAILED to download game %s' % _nice_game(gobj)
    except OSError, e:
        print >> sys.stderr, "Could not run '%s' (errno: %d): %s" \
            % (' '.join(cmd), e.errno, e.strerror)
        print >> sys.stderr, 'FAILED to download game %s' % _nice_game(gobj)


def plays(gobj):
    """
    Returns an ordered dictionary of all plays for a particular game.

    The game must be a nflgame.game.Game object.

    If there is a problem retrieving the data, None is returned.

    If the game is over, then the XML data is saved to disk.
    """
    if gobj.game_over() and gobj.eid in __play_cache:
        return __play_cache[gobj.eid]

    rawxml = _get_xml_data((gobj.eid, gobj.gamekey))
    ps = _xml_play_data(rawxml)
    if ps is None:
        return None
    __play_cache[gobj.eid] = ps

    # Save the XML data to disk if the game is over.
    if gobj.game_over():
        fp = _xmlf % (gobj.eid, gobj.gamekey)
        try:
            print >> gzip.open(fp, 'w+'), rawxml,
        except IOError:
            print >> sys.stderr, 'Could not cache XML data. Please make ' \
                                 '"%s" writable.' % path.dirname(fp)
    return ps


def play(gobj, playid):
    """
    Returns a Play object given a game and a play id. The game must be
    a nflgame.game.Game object.

    If a play with the given id does not exist, None is returned.
    """
    return plays(gobj).get(playid, None)


class Play (object):
    """
    Represents a single play with meta data that ties it to game footage.
    The footage_start corresponds to the 'ArchiveTCIN', which is when
    the play starts. Since there is no record of when a play stops, the
    duration is computed by subtracting the start time from the start
    time of the next play. If it's the last play recorded, then the
    duration is None.

    The play id is the foreign key that maps to play data stored in nflgame.
    """
    def __init__(self, start, duration, playid):
        self.start, self.duration, self.playid = start, duration, playid

    def __str__(self):
        return '(%s, %s, %s)' % (self.playid, self.start, self.duration)


class PlayTime (object):
    """
    Represents a footage time point, in the format HH:MM:SS:MM
    """
    def __init__(self, point):
        self.point = point

        try:
            parts = map(int, self.point.split(':'))
        except ValueError:
            assert False, 'Bad play time format: %s' % self.point

        if len(parts) != 4:
            assert False, 'Expected 4 parts but got %d in: %s' \
                % (len(parts), self.point)

        self.hh, self.mm, self.ss, self.milli = parts

        # I believe milliseconds is given in tens of milliseconds.
        self.milli *= 10

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
        return self.point


def _xml_play_data(data):
    """
    Parses the XML raw data given into an ordered dictionary of Play
    objects. The dictionary is keyed by play id.
    """
    if data is None:
        return None

    # Load everything into a list first, since we need to look ahead to see
    # the next play's start time to compute the current play's duration.
    rows = []
    for row in bs4.BeautifulSoup(data).find_all('row'):
        playid = row.find('id')
        if not playid or not row.find('CATIN'):
            continue
        playid = playid.text().strip()

        start = row.find('ArchiveTCIN')
        if not start:
            continue
        start = PlayTime(start.text().strip())

        # If this start doesn't procede the last start time, skip it.
        if len(rows) > 0 and start < rows[-1][1]:
            continue
        rows.append((playid, start))

    d = OrderedDict()
    for i, (playid, start) in enumerate(rows):
        duration = None
        if i < len(rows) - 1:
            duration = rows[i+1][1] - start
        d[playid] = Play(start, duration, playid)
    return d


def _get_xml_data(game=None, fpath=None):
    """
    Returns the XML play data corresponding to the game given. A game must
    be specified as a tuple: the first element should be an eid and the second
    element should be a game key. For example, ('2012102108', '55604').

    If the XML data is already on disk, it is read, decompressed and returned.

    Otherwise, the XML data is downloaded from the NFL web site. If the data
    doesn't exist yet or there was an error, _get_xml_data returns None.

    If game is None, then the XML data is read from the file at fpath.
    """
    assert game is not None or fpath is not None

    if fpath is not None:
        return gzip.open(fpath).read()

    fpath = _xmlf % (game[0], game[1])
    if os.access(fpath, os.R_OK):
        return gzip.open(fpath).read()
    try:
        year = int(game[0][0:4])
        month = int(game[0][4:6])
        if month <= 3:
            year -= 1
        u = _xml_base_url % (year, game[1])  # The year and the game key.
        return urllib2.urlopen(u, timeout=10).read()
    except urllib2.HTTPError, e:
        print >> sys.stderr, e
    except socket.timeout, e:
        print >> sys.stderr, e
    return None
