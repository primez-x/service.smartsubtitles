# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import threading
import time
import traceback

import xbmc
import xbmcgui

from subtitles import select_signs_songs_track

RETRY_WINDOW = 2.0    # seconds to keep retrying while streams enumerate
RETRY_INTERVAL = 0.25  # seconds between attempts
# Home-window property "<result>|<playing file>" read by PlexKodiConnect: it
# applies the Plex subtitle choice itself unless the result is final here.
RESULT_PROPERTY = 'SmartSubtitles.Result'


def _publish_result(result, playing_file):
    try:
        xbmcgui.Window(10000).setProperty(RESULT_PROPERTY, '%s|%s' % (result, playing_file or ''))
    except Exception:  # pylint: disable=broad-except
        pass


class SmartSubtitlesPlayer(xbmc.Player):
    """Player monitor that fires track selection on playback start."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self._timer = None
        self._cancel_event = None
        xbmc.log('Smart Subtitles: player monitor created', xbmc.LOGINFO)

    def onAVStarted(self):  # pylint: disable=invalid-name
        xbmc.log('Smart Subtitles: onAVStarted fired', xbmc.LOGINFO)
        self._cancel_timer()
        playing_file = self._current_file()
        # Try immediately, then retry briefly if streams aren't ready yet.
        # Streams may not be fully enumerated at onAVStarted on some sources.
        # Each attempt gets its own Event so a newer playback (or stop/end)
        # can cancel it without affecting the next attempt.
        cancel = threading.Event()
        self._cancel_event = cancel
        self._timer = threading.Thread(target=self._select_with_retry,
                                       args=(cancel, playing_file), daemon=True)
        self._timer.start()

    def _current_file(self):
        try:
            return self.getPlayingFile()
        except RuntimeError:
            return None

    def _should_stop(self, cancel, monitor, playing_file):
        """True when this attempt was cancelled, Kodi is shutting down, or the
        player has moved on to a different file than the one we started for."""
        if cancel.is_set() or monitor.abortRequested():
            return True
        if playing_file is not None and self._current_file() != playing_file:
            return True
        return False

    def _select_with_retry(self, cancel, playing_file):
        """Attempt selection immediately, retry every 250ms up to 2s total."""
        monitor = xbmc.Monitor()
        deadline = time.monotonic() + RETRY_WINDOW
        attempts = 0
        skips = 0
        _publish_result('pending', playing_file)

        def should_stop():
            return self._should_stop(cancel, monitor, playing_file)

        while time.monotonic() < deadline:
            if should_stop():
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
            attempts += 1
            try:
                result = select_signs_songs_track(should_stop)
            except Exception:  # pylint: disable=broad-except
                xbmc.log('Smart Subtitles: selection failed:\n%s' % traceback.format_exc(),
                         xbmc.LOGERROR)
                _publish_result('error', playing_file)
                return
            if result == 'cancelled':
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
            # Right after onAVStarted Kodi can still report the previous
            # file's subtitle, which on the next episode of the same release
            # resolves to a valid stream: only a repeated 'skip' is final.
            skips = skips + 1 if result == 'skip' else 0
            if result not in ('retry', 'skip') or skips >= 2:
                xbmc.log('Smart Subtitles: selection finished after %d attempt(s) (%s)' % (
                    attempts, result), xbmc.LOGINFO)
                _publish_result(result, playing_file)
                return
            if cancel.wait(RETRY_INTERVAL):
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
        result = 'skip' if skips else 'no_streams'
        xbmc.log('Smart Subtitles: no final result after %d attempts (%s)'
                 % (attempts, result), xbmc.LOGINFO)
        _publish_result(result, playing_file)

    def onPlayBackStopped(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def onPlayBackEnded(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def onPlayBackError(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def _cancel_timer(self):
        """Signal any in-flight selection attempt to stop at its next check."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._cancel_event = None
        self._timer = None
