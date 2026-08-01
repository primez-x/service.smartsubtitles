# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.html)
"""Smart Subtitles — auto-select preferred-language Signs/Songs/Forced tracks.

Extends Kodi's ``forced_only`` subtitle mode to also match tracks named
"signs", "songs", or "forced" that aren't flagged with the forced bit in
the MKV container. Language-aware: only selects tracks in the user's
preferred subtitle language (sourced from Kodi's own
``subtitles.languages`` setting — single source of truth, no separate
addon setting to drift out of sync).
"""
from __future__ import absolute_import, division, unicode_literals

import json

import xbmc
import xbmcgui


# ISO 639-2 normalization fallback for common languages. Kodi's JSON-RPC
# subtitle stream ``language`` field returns ISO 639-2 codes (eng, por, spa),
# but Kodi's ``subtitles.languages`` setting holds display names ("English").
# xbmc.convertLanguage handles most of this, but is inconsistent with full
# display names on some Kodi builds, so we seed common mappings here.
_LANG_TO_ISO639_2 = {
    'english': 'eng', 'en': 'eng', 'eng': 'eng',
    'japanese': 'jpn', 'ja': 'jpn', 'jpn': 'jpn',
    'spanish': 'spa', 'es': 'spa', 'spa': 'spa',
    'portuguese': 'por', 'pt': 'por', 'por': 'por',
    'french': 'fre', 'fr': 'fre', 'fra': 'fre', 'fre': 'fre',
    'german': 'ger', 'de': 'ger', 'deu': 'ger', 'ger': 'ger',
    'italian': 'ita', 'it': 'ita', 'ita': 'ita',
    'russian': 'rus', 'ru': 'rus', 'rus': 'rus',
    'arabic': 'ara', 'ar': 'ara', 'ara': 'ara',
    'korean': 'kor', 'ko': 'kor', 'kor': 'kor',
    'chinese': 'chi', 'zh': 'chi', 'zho': 'chi', 'chi': 'chi',
    'dutch': 'dut', 'nl': 'dut', 'nld': 'dut', 'dut': 'dut',
    'polish': 'pol', 'pl': 'pol', 'pol': 'pol',
    'swedish': 'swe', 'sv': 'swe', 'swe': 'swe',
    'norwegian': 'nor', 'no': 'nor', 'nob': 'nor', 'nor': 'nor',
    'finnish': 'fin', 'fi': 'fin', 'fin': 'fin',
    'danish': 'dan', 'da': 'dan', 'dan': 'dan',
    'czech': 'cze', 'cs': 'cze', 'ces': 'cze', 'cze': 'cze',
    'turkish': 'tur', 'tr': 'tur', 'tur': 'tur',
    'thai': 'tha', 'th': 'tha', 'tha': 'tha',
    'hindi': 'hin', 'hi': 'hin', 'hin': 'hin',
    'hebrew': 'heb', 'he': 'heb', 'heb': 'heb',
    'indonesian': 'ind', 'id': 'ind', 'ind': 'ind',
    'vietnamese': 'vie', 'vi': 'vie', 'vie': 'vie',
}


def _notify_selected(name):
    """Show a toast notification confirming the subtitle selection."""
    header = xbmc.getLocalizedString(287) or 'Subtitles'  # 287 = "Subtitles"
    xbmcgui.Dialog().notification(header, name, xbmcgui.NOTIFICATION_INFO, 3000)


def _get_setting(key):
    import xbmcaddon
    return xbmcaddon.Addon('service.smartsubtitles').getSetting(key)


def _get_setting_bool(key, default=True):
    val = _get_setting(key)
    if val == '':
        return default
    return val.lower() == 'true'


def _jsonrpc(method, params=None):
    """Execute a JSON-RPC command on Kodi and return the result dict."""
    payload = {'jsonrpc': '2.0', 'method': method, 'id': 1}
    if params is not None:
        payload['params'] = params
    resp = xbmc.executeJSONRPC(json.dumps(payload))
    try:
        return json.loads(resp)
    except (ValueError, TypeError):
        return {}


def _get_active_player_id():
    result = _jsonrpc('Player.GetActivePlayers')
    for player in result.get('result', []) or []:
        if player.get('type') == 'video':
            return player.get('playerid')
    return None


def _get_subtitle_streams(playerid):
    result = _jsonrpc('Player.GetProperties', {
        'playerid': playerid,
        'properties': ['subtitles', 'currentsubtitle', 'subtitleenabled'],
    })
    props = result.get('result', {}) or {}
    return (
        props.get('subtitles', []) or [],
        props.get('currentsubtitle', {}) or {},
        bool(props.get('subtitleenabled', False)),
    )


def _set_subtitle(playerid, index):
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': index})
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': 'on'})


def _disable_subtitles(playerid):
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': 'off'})


def _get_keywords():
    raw = _get_setting('keywords') or 'signs,songs,sign,forced'
    return [k.strip().lower() for k in raw.split(',') if k.strip()]


def _matches_keywords(name, keywords):
    if not name:
        return False
    name_lower = name.lower()
    return any(kw in name_lower for kw in keywords)


def _to_iso639_2(language):
    """Normalize a language string (display name, ISO 639-1, or ISO 639-2) to
    a lowercase ISO 639-2 code. Returns '' for empty/unknown."""
    if not isinstance(language, str):
        return ''
    value = language.strip().lower().replace('_', '-')
    if not value:
        return ''
    base = value.split('-', 1)[0]
    # Try Kodi's converter first (authoritative when available)
    try:
        converted = xbmc.convertLanguage(base, xbmc.ISO_639_2)
        if converted:
            return converted.lower()
    except (AttributeError, TypeError):
        pass
    # Fallback to our static map
    return _LANG_TO_ISO639_2.get(value, _LANG_TO_ISO639_2.get(base, base))


def _get_preferred_language_codes():
    """Return a set of ISO 639-2 codes for the user's preferred subtitle
    languages. Sourced from Kodi's OWN settings so there is a single source
    of truth (no separate addon setting to drift):

      1. ``subtitles.languages`` — Kodi UI: Player > Subtitles > Preferred
         subtitle language. Holds display names like ["English"].
      2. Fallback: Kodi UI locale (``xbmc.getLanguage``).

    Returns an empty set if nothing could be determined.
    """
    codes = set()
    # 1. subtitles.languages — the authoritative subtitle preference
    result = _jsonrpc('Settings.GetSettingValue',
                      {'setting': 'subtitles.languages'})
    langs = (result.get('result') or {}).get('value') or []
    if isinstance(langs, str):
        langs = [s.strip() for s in langs.split(',') if s.strip()]
    for name in langs:
        code = _to_iso639_2(name)
        if code:
            codes.add(code)
    if codes:
        return codes
    # 2. Fallback: Kodi UI locale
    try:
        ui_code = xbmc.getLanguage(xbmc.ISO_639_2, False)
        if ui_code:
            codes.add(ui_code.lower())
    except Exception:
        pass
    return codes


def _stream_for_index(streams, index):
    """Return the subtitle stream with the given Kodi index, or None."""
    if index is None:
        return None
    for stream in streams:
        if stream.get('index') == index:
            return stream
    return None


def select_signs_songs_track():
    """Main entry point. Called on playback start.

    Returns:
      'selected'  — found and selected a matching track
      'no_match'  — scanned streams, none matched in preferred language
      'disabled'  — addon disabled in settings
      'no_player' — no active video player
      'skip'      — user/Kodi already has a preferred-language subtitle active
      'retry'     — no streams available yet (caller should retry)
    """
    if not _get_setting_bool('enabled'):
        xbmc.log('Smart Subtitles: disabled, skipping', xbmc.LOGINFO)
        return 'disabled'

    playerid = _get_active_player_id()
    if playerid is None:
        xbmc.log('Smart Subtitles: no active video player', xbmc.LOGINFO)
        return 'no_player'

    streams, current, sub_enabled = _get_subtitle_streams(playerid)
    if not streams:
        return 'retry'

    respect_lang = _get_setting_bool('respect_language', default=True)
    preferred = _get_preferred_language_codes() if respect_lang else set()
    if respect_lang and not preferred:
        xbmc.log('Smart Subtitles: could not determine preferred subtitle '
                 'language from Kodi settings — language filter inactive',
                 xbmc.LOGWARNING)

    keywords = _get_keywords()

    # --- Stale-index guard + non-preferred override ---
    # Validate the reported current index against the actual stream list.
    # Kodi's ``currentsubtitle.index`` can carry a stale value from the
    # previous file for tens of ms after onAVStarted. If the index doesn't
    # resolve to a real stream in THIS file's list, treat it as unresolved.
    current_index = current.get('index')
    current_stream = _stream_for_index(streams, current_index)

    if sub_enabled and current_stream is not None:
        current_lang = _to_iso639_2(current_stream.get('language', ''))
        in_preferred = (not (respect_lang and preferred)) or current_lang in preferred
        if in_preferred:
            xbmc.log('Smart Subtitles: subtitle already active in preferred '
                     'language (index=%s, lang=%s), not overriding'
                     % (current_index, current_lang), xbmc.LOGINFO)
            return 'skip'
        # Current track is active but NOT in a preferred language.
        # If it's forced-flagged, Kodi's forced_only auto-picked it (first
        # forced by stream index, regardless of language — e.g. Portuguese
        # (Forced) at idx 0 on a multi-language anime mux). That is not a
        # user choice — disable it so we can try to find a preferred match.
        # If it's NOT forced-flagged, it may be a deliberate user choice on
        # this file — respect it.
        if current_stream.get('isforced'):
            xbmc.log('Smart Subtitles: disabling non-preferred forced '
                     'subtitle (index=%s, lang=%s) auto-picked by Kodi'
                     % (current_index, current_lang), xbmc.LOGINFO)
            _disable_subtitles(playerid)
        else:
            xbmc.log('Smart Subtitles: non-preferred non-forced subtitle '
                     'active (index=%s, lang=%s), respecting as user choice'
                     % (current_index, current_lang), xbmc.LOGINFO)
            return 'skip'

    # --- Scan for a keyword match in a preferred language ---
    xbmc.log('Smart Subtitles: scanning %d streams (preferred=%s, keywords=%s)'
             % (len(streams), sorted(preferred) if preferred else 'any',
                keywords), xbmc.LOGINFO)
    for stream in streams:
        name = stream.get('name', '')
        index = stream.get('index')
        stream_lang = _to_iso639_2(stream.get('language', ''))
        xbmc.log('Smart Subtitles: stream[%s] lang=%s name=%r'
                 % (index, stream_lang or stream.get('language'), name),
                 xbmc.LOGDEBUG)
        if respect_lang and preferred and stream_lang not in preferred:
            continue
        if _matches_keywords(name, keywords):
            xbmc.log('Smart Subtitles: SELECTING index=%d lang=%s name=%s'
                     % (index, stream_lang, name), xbmc.LOGINFO)
            _set_subtitle(playerid, index)
            _notify_selected(name)
            return 'selected'

    xbmc.log('Smart Subtitles: no matching track in preferred languages '
             'found (keywords=%s)' % keywords, xbmc.LOGINFO)
    return 'no_match'
