# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import threading
import time
import traceback

import xbmc
import xbmcgui

import subtitles
from corrections import CorrectionMemory, correction_key
from subtitles import select_signs_songs_track

RETRY_WINDOW = 10.0   # seconds to wait for streams and player readiness
RETRY_INTERVAL = 0.25  # seconds between attempts
CHANGE_DEBOUNCE = 0.75
# Home-window property "<result>|<playing file>" read by PlexKodiConnect: it
# applies the Plex subtitle choice itself unless the result is final here.
RESULT_PROPERTY = 'SmartSubtitles.Result'


def _publish_result(result, playing_file):
    try:
        xbmcgui.Window(10000).setProperty(RESULT_PROPERTY, '%s|%s' % (result, playing_file or ''))
    except Exception:  # pylint: disable=broad-except
        pass


class SmartSubtitlesPlayer(xbmc.Player):
    """Select tracks and observe later stream changes and manual corrections."""

    def __init__(self):
        xbmc.Player.__init__(self)
        self._timer = None
        self._cancel_event = None
        self._lock = threading.RLock()
        self._playing_file = None
        self._snapshot = None
        self._change_candidate = None
        self._manual_candidate = None
        self._automatic = None
        self._respect_choice = False
        self._selection_state = {}
        self._av_changed = threading.Event()
        self._next_poll = 0.0
        self._memory = CorrectionMemory()
        xbmc.log('Smart Subtitles: player monitor created', xbmc.LOGINFO)

    def onAVStarted(self):  # pylint: disable=invalid-name
        xbmc.log('Smart Subtitles: onAVStarted fired', xbmc.LOGDEBUG)
        self._cancel_timer()
        playing_file = self._current_file()
        with self._lock:
            self._playing_file = playing_file
            try:
                self._snapshot = subtitles._get_playback_snapshot()
            except Exception as error:  # pylint: disable=broad-except
                self._snapshot = None
                xbmc.log('Smart Subtitles: initial player properties unavailable: %s'
                         % error, xbmc.LOGWARNING)
            self._change_candidate = None
            self._manual_candidate = None
            self._automatic = None
            self._respect_choice = False
            self._selection_state = {}
            self._av_changed.clear()
            self._next_poll = 0.0
            self._start_selection(playing_file)

    def onAVChange(self):  # pylint: disable=invalid-name
        # Kodi also sends this callback for this add-on's own subtitle writes.
        self._av_changed.set()

    def _start_selection(self, playing_file):
        # Try immediately, then retry briefly if streams aren't ready yet.
        # Streams may not be fully enumerated at onAVStarted on some sources.
        # Each attempt gets its own Event so a newer playback (or stop/end)
        # can cancel it without affecting the next attempt.
        cancel = threading.Event()
        self._cancel_event = cancel
        self._timer = threading.Thread(target=self._select_with_retry,
                                       args=(cancel, playing_file), daemon=True)
        self._timer.start()

    def _correction_key(self, playing_file):
        playerid = subtitles._get_active_player_id()
        item = subtitles._get_playing_item(playerid) if playerid is not None else {}
        return correction_key(playing_file, item)

    def _has_correction(self, playing_file):
        if not subtitles._get_setting_bool('remember_manual', default=True):
            return False
        if self._memory.contains(correction_key(playing_file, {})):
            return True
        # Most profiles have no saved corrections. Avoid metadata RPCs there.
        return bool(self._memory.entries and
                    self._memory.contains(self._correction_key(playing_file)))

    def _finish_selection(self, result, playing_file, cancel, selection_state):
        with self._lock:
            if cancel.is_set() or self._current_file() != playing_file:
                return
            if self._playing_file == playing_file:
                if result == 'selected':
                    self._automatic = selection_state.get('automatic_selection')
                elif result == 'skip' and self._automatic is None:
                    self._respect_choice = True
            _publish_result(result, playing_file)
            subtitles.notify_selection_failure(result)

    def poll(self):
        """Observe a stable manual correction before any new automatic choice."""
        with self._lock:
            playing_file = self._playing_file
            if (not playing_file or self._current_file() != playing_file
                    or (self._timer is not None and self._timer.is_alive())):
                return
            if self._respect_choice:
                return
            now = time.monotonic()
            if not self._av_changed.is_set() and now < self._next_poll:
                return
            self._av_changed.clear()
            self._next_poll = now + 1.0
            snapshot = subtitles._get_playback_snapshot()
            if snapshot is None or self._current_file() != playing_file:
                return
            previous = self._snapshot
            if previous is None:
                self._snapshot = snapshot
                if self._automatic is None:
                    self._start_selection(playing_file)
                return
            environment = snapshot['environment']
            changed = environment != previous['environment']
            if changed:
                # Stream replacement, audio changes, and transient Kodi resets
                # cannot reliably prove a manual subtitle correction.
                self._manual_candidate = None
                candidate = self._change_candidate
                if candidate is None or candidate[0] != environment:
                    self._change_candidate = (environment, now)
                    return
                if now - candidate[1] < CHANGE_DEBOUNCE:
                    return
                if snapshot['subtitle'] != self._automatic:
                    self._automatic = None
                self._snapshot = snapshot
                self._change_candidate = None
                self._start_selection(playing_file)
                return
            self._change_candidate = None
            observed = snapshot['subtitle']
            if (self._automatic is not None and observed != self._automatic
                    and observed[0] == self._automatic[0]
                    and (not observed[1] or observed[3] is not None)):
                candidate = self._manual_candidate
                if candidate is None or candidate[0] != observed:
                    self._manual_candidate = (observed, now)
                    return
                if now - candidate[1] < CHANGE_DEBOUNCE:
                    return
                self._respect_choice = True
                self._automatic = None
                if subtitles._get_setting_bool('remember_manual', default=True):
                    key = self._correction_key(playing_file)
                    if self._current_file() != playing_file:
                        return
                    self._memory.record(key, correction_key(playing_file, {}))
                _publish_result('skip', playing_file)
                return
            self._manual_candidate = None

    def _current_file(self):
        # Kodi logs a C++ exception when getPlayingFile() is called while
        # stopped, even though Python catches the RuntimeError.
        if not self.isPlaying():
            return None
        try:
            return self.getPlayingFile()
        except RuntimeError:
            return None

    def _should_stop(self, cancel, monitor, playing_file):
        """True when this attempt was cancelled, Kodi is shutting down, or the
        player has moved on to a different file than the one we started for."""
        if cancel.is_set() or monitor.abortRequested():
            return True
        return not playing_file or self._current_file() != playing_file

    def _select_with_retry(self, cancel, playing_file):
        """Retry delayed or failed selection until the ten-second deadline."""
        monitor = xbmc.Monitor()
        deadline = time.monotonic() + RETRY_WINDOW
        attempts = 0
        skips = 0
        selection_state = (self._selection_state if self._playing_file == playing_file else {})

        def should_stop():
            return self._should_stop(cancel, monitor, playing_file)

        if should_stop():
            return
        _publish_result('pending', playing_file)

        last_error = False
        while time.monotonic() < deadline:
            if should_stop():
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
            attempts += 1
            try:
                if self._has_correction(playing_file):
                    result = 'skip'
                else:
                    result = select_signs_songs_track(should_stop, selection_state)
            except Exception:  # pylint: disable=broad-except
                xbmc.log('Smart Subtitles: selection failed:\n%s' % traceback.format_exc(),
                         xbmc.LOGERROR)
                result = 'retry'
                last_error = True
            else:
                last_error = False
            if should_stop():
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
            if result == 'cancelled':
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
            # Right after onAVStarted Kodi can still report the previous
            # file's subtitle, which on the next episode of the same release
            # resolves to a valid stream: only a repeated 'skip' is final.
            skips = skips + 1 if result == 'skip' else 0
            if result == 'no_player':
                result = 'retry'
            if result not in ('retry', 'skip') or skips >= 2:
                xbmc.log('Smart Subtitles: selection finished after %d attempt(s) (%s)' % (
                    attempts, result),
                    xbmc.LOGDEBUG if result == 'audio_only' else xbmc.LOGINFO)
                if should_stop():
                    return
                self._finish_selection(result, playing_file, cancel, selection_state)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if cancel.wait(min(RETRY_INTERVAL, remaining)):
                xbmc.log('Smart Subtitles: selection cancelled after %d attempt(s)'
                         % attempts, xbmc.LOGINFO)
                return
        if should_stop():
            return
        result = 'error' if last_error else ('skip' if skips else 'no_streams')
        xbmc.log('Smart Subtitles: no final result after %d attempts (%s)'
                 % (attempts, result), xbmc.LOGINFO)
        if should_stop():
            return
        self._finish_selection(result, playing_file, cancel, selection_state)

    def onPlayBackStopped(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def onPlayBackEnded(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def onPlayBackError(self):  # pylint: disable=invalid-name
        self._cancel_timer()

    def _cancel_timer(self):
        """Signal any in-flight selection attempt to stop at its next check."""
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._cancel_event = None
            self._timer = None
            self._playing_file = None
