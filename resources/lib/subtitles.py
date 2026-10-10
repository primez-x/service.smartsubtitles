# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.html)
"""Smart Subtitles — auto-select preferred-language Signs/Songs/Forced tracks.

Extends Kodi's ``forced_only`` subtitle mode: picks the preferred-language
track with Kodi's forced flag, or else one named "signs", "songs", or
"forced" (whole words) that isn't flagged with the forced bit in the MKV
container. Language-aware: only selects tracks in the user's
preferred subtitle language, sourced from Kodi's own settings (single source
of truth, no separate addon setting to drift out of sync): Kodi's
``locale.subtitlelanguage`` ("Preferred subtitle language") when it names a
real language, otherwise ``subtitles.languages`` ("Languages to download
subtitles for"), otherwise the Kodi UI language.
"""
from __future__ import absolute_import, division, unicode_literals

import json
import re

import xbmc
import xbmcgui


# ISO 639-2 normalization fallback for common languages. Kodi's JSON-RPC
# subtitle stream ``language`` field returns ISO 639-2 codes (eng, por, spa),
# but Kodi's ``locale.subtitlelanguage`` and ``subtitles.languages`` settings
# hold English display names ("English", "Portuguese (Brazil)").
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

# Special (non-language) values of Kodi's ``locale.subtitlelanguage``.
_NON_LANGUAGE_SUBTITLE_PREFS = ('', 'original', 'default', 'forced_only', 'none')

# PlexKodiConnect re-applies Plex's own subtitle choice a few seconds after
# playback starts unless its ``subtitleStreamPick`` setting is '1'. From
# 3.12.60 on it leaves the choice to this add-on when it is enabled.
_PKC_ADDON_ID = 'plugin.video.plexkodiconnect'
_PKC_DEFERS_VERSION = (3, 12, 60)
_pkc_warning_checked = False


def _notify_selected(name):
    """Show a toast notification confirming the subtitle selection, unless
    the user turned notifications off in the addon settings."""
    if not _get_setting_bool('notify_selected', default=True):
        return
    header = xbmc.getLocalizedString(287) or 'Subtitles'  # 287 = "Subtitles"
    xbmcgui.Dialog().notification(header, name, xbmcgui.NOTIFICATION_INFO, 3000)


def warn_plexkodiconnect_interop():
    """Log a one-time warning when PlexKodiConnect is enabled with its
    subtitle stream pick set to Plex ('0'): PKC will then overwrite the
    track chosen here. Informational only; behaviour does not change."""
    global _pkc_warning_checked
    if _pkc_warning_checked:
        return
    _pkc_warning_checked = True
    try:
        if not xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % _PKC_ADDON_ID):
            return
        import xbmcaddon
        pkc = xbmcaddon.Addon(_PKC_ADDON_ID)
        pick = pkc.getSetting('subtitleStreamPick')
        version = tuple(int(part) for part in
                        pkc.getAddonInfo('version').split('.')[:3])
    except Exception:
        return
    if pick == '0' and version < _PKC_DEFERS_VERSION:
        xbmc.log('Smart Subtitles: PlexKodiConnect is enabled with its '
                 'subtitle stream pick set to Plex (subtitleStreamPick=0); it '
                 're-applies Plex\'s subtitle choice a few seconds after '
                 'playback starts and may override this addon\'s selection',
                 xbmc.LOGWARNING)


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


DEFAULT_KEYWORDS = 'signs,songs,forced'

# Keywords in the "signs/songs" family. A name matching one of these ranks
# above a name matching any other keyword (e.g. "forced").
_SIGNS_SONGS_KEYWORDS = ('sign', 'signs', 'song', 'songs')

# "Non-Forced" / "non forced" / "NonForced" must never count as "forced".
_NON_FORCED_RE = re.compile(r'\bnon[\s_.-]*forced\b')
# Full dialogue tracks that merely mention signs/songs, e.g.
# "Full Subtitles (Dialogue + Signs & Songs)", are not signs/songs tracks.
_FULL_TRACK_RE = re.compile(r'\b(?:full|dialog(?:ue)?)\b')
_FORCED_WORD_RE = re.compile(r'\bforced\b')

# Candidate ranks (lower wins; ties go to the earlier stream).
RANK_FORCED_FLAG = 0   # Kodi's ``isforced`` stream flag
RANK_SIGNS_SONGS = 1   # name matches a signs/songs keyword
RANK_KEYWORD = 2       # name matches any other keyword


def _get_keywords():
    raw = _get_setting('keywords') or DEFAULT_KEYWORDS
    return [k.strip().lower() for k in raw.split(',') if k.strip()]


def _keyword_pattern(keyword):
    """Whole-word regex for a keyword. "sign"/"signs" and "song"/"songs"
    match both singular and plural; everything else matches as-is, so
    "sign" no longer matches "Designed" and "forced" no longer matches
    "Unforced"."""
    if keyword in ('sign', 'signs'):
        body = r'signs?'
    elif keyword in ('song', 'songs'):
        body = r'songs?'
    else:
        body = re.escape(keyword)
    # Lookarounds instead of \b so keywords like "[forced]" still work.
    return re.compile(r'(?<!\w)' + body + r'(?!\w)')


def _clean_name(name):
    """Lowercase a track name and blank out "non-forced" phrases."""
    if not name:
        return ''
    return _NON_FORCED_RE.sub(' ', name.lower())


def _is_full_track(clean_name):
    """True for full-dialogue tracks ("Full", "Dialogue") that don't also
    say they are forced (e.g. "Forced (foreign dialogue)" is kept)."""
    return bool(_FULL_TRACK_RE.search(clean_name)
                and not _FORCED_WORD_RE.search(clean_name))


def _keyword_rank(clean_name, keywords):
    """Best (lowest) rank of any keyword matching the cleaned name, or None."""
    best = None
    for kw in keywords:
        if _keyword_pattern(kw).search(clean_name):
            rank = RANK_SIGNS_SONGS if kw in _SIGNS_SONGS_KEYWORDS else RANK_KEYWORD
            if best is None or rank < best:
                best = rank
    return best


def _candidate_rank(stream, keywords):
    """Rank a subtitle stream as a Signs/Songs/Forced candidate, or None.

    Kodi's ``isforced`` flag is the strongest signal, then names that are
    signs/songs tracks, then any other keyword match. A track named as a
    full dialogue track is never a candidate, whatever its flag says."""
    clean = _clean_name(stream.get('name', ''))
    if _is_full_track(clean):
        return None
    if stream.get('isforced'):
        return RANK_FORCED_FLAG
    return _keyword_rank(clean, keywords)


def _to_iso639_2(language):
    """Normalize a language string (display name, ISO 639-1, or ISO 639-2) to
    a lowercase ISO 639-2 code. Returns '' for empty/unknown."""
    if not isinstance(language, str):
        return ''
    value = language.strip().lower().replace('_', '-')
    # Kodi language names may carry a region suffix, e.g. "Portuguese (Brazil)"
    value = re.sub(r'\s*\(.*\)$', '', value)
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


def _get_global_setting(setting):
    """Return the value of a Kodi (global) setting via JSON-RPC, or None."""
    result = _jsonrpc('Settings.GetSettingValue', {'setting': setting})
    return (result.get('result') or {}).get('value')


def _get_ui_language_code():
    """ISO 639-2 code of the Kodi UI language, or ''."""
    try:
        return _to_iso639_2(xbmc.getLanguage(xbmc.ISO_639_2, False))
    except Exception:
        return ''


def _get_preferred_languages():
    """Return ``(codes, authoritative)``: two sets of ISO 639-2 codes for the
    user's preferred subtitle languages. Sourced from Kodi's OWN settings so
    there is a single source of truth (no separate addon setting to drift):

      1. ``locale.subtitlelanguage`` — Kodi UI: Player > Language >
         Preferred subtitle language. Holds an English language name such
         as "English", or one of the special values ``original``,
         ``default``, ``forced_only`` or ``none``. With ``forced_only`` the
         Kodi UI language is also preferred (see below).
      2. ``subtitles.languages`` — Kodi UI: Player > Subtitles > Languages
         to download subtitles for. Holds display names like ["English"].
         Kodi pre-fills this (usually with English), so it is a weak
         signal: these languages are used to pick a track when none is
         active, but never to replace a forced track Kodi chose itself.
      3. Fallback: Kodi UI locale (``xbmc.getLanguage``).

    ``codes`` is every preferred language; ``authoritative`` is the subset
    that did NOT come only from the download-language list (it may be empty).
    Both are empty if nothing could be determined.
    """
    # 1. locale.subtitlelanguage — the preferred subtitle language, when set
    #    to an actual language
    preferred = _get_global_setting('locale.subtitlelanguage')
    pref_value = preferred.strip().lower() if isinstance(preferred, str) else ''
    if pref_value not in _NON_LANGUAGE_SUBTITLE_PREFS:
        code = _to_iso639_2(preferred)
        if code:
            return {code}, {code}
    # forced_only carries no language itself; Kodi's forced pick is for the
    # user's own language, so the UI language counts as preferred.
    authoritative = set()
    if pref_value == 'forced_only':
        ui_code = _get_ui_language_code()
        if ui_code:
            authoritative.add(ui_code)
    # 2. subtitles.languages — the subtitle download languages
    codes = set(authoritative)
    langs = _get_global_setting('subtitles.languages') or []
    if isinstance(langs, str):
        langs = [s.strip() for s in langs.split(',') if s.strip()]
    for name in langs:
        code = _to_iso639_2(name)
        if code:
            codes.add(code)
    if codes:
        return codes, authoritative
    # 3. Fallback: Kodi UI locale
    ui_code = _get_ui_language_code()
    if ui_code:
        return {ui_code}, {ui_code}
    return set(), set()


def _get_preferred_language_codes():
    """Return the set of ISO 639-2 codes for the user's preferred subtitle
    languages (see ``_get_preferred_languages``)."""
    return _get_preferred_languages()[0]


def _stream_for_index(streams, index):
    """Return the subtitle stream with the given Kodi index, or None."""
    if index is None:
        return None
    for stream in streams:
        if stream.get('index') == index:
            return stream
    return None


def select_signs_songs_track(should_stop=None):
    """Main entry point. Called on playback start.

    should_stop, when given, is checked right before subtitles are changed so
    an attempt for a file that is no longer playing never touches the next one.

    Returns:
      'selected'  — found and selected a matching track
      'no_match'  — scanned streams, none matched in preferred language
      'disabled'  — addon disabled in settings
      'no_player' — no active video player
      'skip'      — user/Kodi already has a preferred-language subtitle active
      'retry'     — no streams available yet (caller should retry)
      'cancelled' — should_stop() turned true before anything was changed
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
    if respect_lang:
        preferred, authoritative = _get_preferred_languages()
    else:
        preferred, authoritative = set(), set()
    if respect_lang and not preferred:
        xbmc.log('Smart Subtitles: could not determine preferred subtitle '
                 'language from Kodi settings — language filter inactive',
                 xbmc.LOGWARNING)
    lang_filter = preferred if respect_lang else set()

    keywords = _get_keywords()

    # --- Stale-index guard + non-preferred override ---
    # Validate the reported current index against the actual stream list.
    # Kodi's ``currentsubtitle.index`` can carry a stale value from the
    # previous file for tens of ms after onAVStarted. If the index doesn't
    # resolve to a real stream in THIS file's list, treat it as unresolved.
    current_index = current.get('index')
    current_stream = _stream_for_index(streams, current_index)
    replacing = False

    if sub_enabled and current_stream is not None:
        current_lang = _to_iso639_2(current_stream.get('language', ''))
        in_preferred = not lang_filter or current_lang in lang_filter
        if in_preferred:
            xbmc.log('Smart Subtitles: subtitle already active in preferred '
                     'language (index=%s, lang=%s), not overriding'
                     % (current_index, current_lang), xbmc.LOGINFO)
            return 'skip'
        # Current track is active but NOT in a preferred language.
        # If it's NOT forced-flagged, it may be a deliberate user choice on
        # this file — respect it.
        if not current_stream.get('isforced'):
            xbmc.log('Smart Subtitles: non-preferred non-forced subtitle '
                     'active (index=%s, lang=%s), respecting as user choice'
                     % (current_index, current_lang), xbmc.LOGINFO)
            return 'skip'
        # It's forced-flagged: Kodi's forced_only auto-picked it (first
        # forced by stream index, regardless of language — e.g. Portuguese
        # (Forced) at idx 0 on a multi-language anime mux). Replace it, but
        # only with a track in a language that did not come solely from
        # Kodi's pre-filled download-language list.
        if not authoritative:
            xbmc.log('Smart Subtitles: forced subtitle (index=%s, lang=%s) '
                     'picked by Kodi; preferred languages %s come only from '
                     'the subtitle download languages, not overriding'
                     % (current_index, current_lang, sorted(preferred)),
                     xbmc.LOGINFO)
            return 'skip'
        lang_filter = authoritative
        replacing = True

    # --- Scan for the best candidate in a preferred language ---
    xbmc.log('Smart Subtitles: scanning %d streams (preferred=%s, keywords=%s)'
             % (len(streams), sorted(lang_filter) if lang_filter else 'any',
                keywords), xbmc.LOGINFO)
    best = None
    for position, stream in enumerate(streams):
        name = stream.get('name', '')
        index = stream.get('index')
        stream_lang = _to_iso639_2(stream.get('language', ''))
        rank = _candidate_rank(stream, keywords)
        xbmc.log('Smart Subtitles: stream[%s] lang=%s name=%r forced=%s rank=%s'
                 % (index, stream_lang or stream.get('language'), name,
                    bool(stream.get('isforced')), rank),
                 xbmc.LOGDEBUG)
        if lang_filter and stream_lang not in lang_filter:
            continue
        if rank is None:
            continue
        if best is None or (rank, position) < best[:2]:
            best = (rank, position, stream, stream_lang)

    if best is None:
        # Never leave the user with less than Kodi selected.
        xbmc.log('Smart Subtitles: no matching track in preferred languages '
                 'found (keywords=%s), leaving subtitles as they are'
                 % keywords, xbmc.LOGINFO)
        return 'no_match'

    _rank, _position, stream, stream_lang = best
    index = stream.get('index')
    name = stream.get('name', '')
    if should_stop is not None and should_stop():
        return 'cancelled'
    if replacing:
        xbmc.log('Smart Subtitles: replacing non-preferred forced subtitle '
                 '(index=%s) auto-picked by Kodi' % current_index, xbmc.LOGINFO)
        _disable_subtitles(playerid)
    xbmc.log('Smart Subtitles: SELECTING index=%s lang=%s name=%s'
             % (index, stream_lang, name), xbmc.LOGINFO)
    _set_subtitle(playerid, index)
    _notify_selected(name or stream_lang or str(index))
    return 'selected'
