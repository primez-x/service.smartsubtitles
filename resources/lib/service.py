# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import xbmc

from player import SmartSubtitlesPlayer
from subtitles import warn_plexkodiconnect_interop


class SmartSubtitlesMonitor(xbmc.Monitor):
    """Service monitor that listens for playback start and auto-selects
    Signs/Songs subtitle tracks when present."""

    def __init__(self):
        self.player = SmartSubtitlesPlayer()
        xbmc.Monitor.__init__(self)
        xbmc.log('Smart Subtitles: service started', xbmc.LOGINFO)
        warn_plexkodiconnect_interop()

    def run(self):
        """Main service loop. Blocks until Kodi requests abort."""
        while not self.abortRequested():
            if self.waitForAbort(1):
                break
        xbmc.log('Smart Subtitles: service stopping', xbmc.LOGINFO)
