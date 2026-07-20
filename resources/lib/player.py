# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import threading
import time

import xbmc

from subtitles import select_signs_songs_track


class SmartSubtitlesPlayer(xbmc.Player):
    """Player monitor that fires track selection on playback start."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self._timer = None
        xbmc.log('Smart Subtitles: player monitor created', xbmc.LOGINFO)

    def onAVStarted(self):  # pylint: disable=invalid-name
        xbmc.log('Smart Subtitles: onAVStarted fired', xbmc.LOGINFO)
        self._cancel_timer()
        # Try immediately, then retry briefly if streams aren't ready yet.
        # Streams may not be fully enumerated at onAVStarted on some sources.
        self._timer = threading.Thread(target=self._select_with_retry, daemon=True)
        self._timer.start()

    def _select_with_retry(self):
        """Attempt selection immediately, retry every 250ms up to 2s total."""
        deadline = time.monotonic() + 2.0
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            result = select_signs_songs_track()
            if result != 'retry':
                xbmc.log('Smart Subtitles: selection finished after %d attempt(s) (%s)' % (
                    attempts, result), xbmc.LOGINFO)
                return
            time.sleep(0.25)
        xbmc.log('Smart Subtitles: no streams available after %d attempts' % attempts, xbmc.LOGINFO)

    def onPlayBackStopped(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def onPlayBackEnded(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def _cancel_timer(self):
        # Timer is now a Thread; we can't cancel it mid-flight, but the daemon
        # flag ensures it won't block shutdown and the deadline bounds its runtime.
        self._timer = None
