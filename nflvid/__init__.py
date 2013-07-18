from collections import namedtuple
import gzip
import os
import os.path as path
import socket
import sys
import urllib2

from nflgame import OrderedDict
import nflgame.game

import bs4

_xmlf = path.join(path.split(__file__)[0], 'pbp-xml', '%s-%s.xml.gz')
_xml_base_url = 'http://e2.cdnl3.neulion.com/nfl/edl/nflgr/%d/%s.xml'

__play_cache = { } # game eid -> play id -> Play


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
    The footage_start corresponds to the 'ArchiveTCIN', which is when the
    play starts. Since there is no record of when a play stops, the duration is
    computed by subtracting the start time from the start time of the next play.
    If it's the last play recorded, then the duration is None.

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
        u = _xml_base_url % (year, game[1]) # The year and the game key.
        return urllib2.urlopen(u, timeout=10).read()
    except urllib2.HTTPError, e:
        print >> sys.stderr, e
    except socket.timeout, e:
        print >> sys.stderr, e
    return None
