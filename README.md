# Smart Subtitles

Smart Subtitles checks available subtitle streams when Kodi starts video playback. The service can select a preferred-language Signs, Songs, or forced stream when a matching stream is ready.

The service selects streams already exposed by Kodi. Smart Subtitles does not download subtitle files. The add-on manifest imports Kodi's `xbmc.python` API at version `3.0.0`.

## Install

1. Install the [Primez repository ZIP](https://primez-x.github.io/kodi.addons/repository.primez.addons/repository.primez.addons-1.0.0.zip) in Kodi.
2. Open **Add-ons → Install from repository → Primez Kodi Add-ons → Services**.
3. Install **Smart Subtitles** and open the add-on settings from Kodi's Services list.

See the [Primez repository site](https://primez-x.github.io/kodi.addons/) and the [central repository guide](https://github.com/primez-x/kodi.addons/blob/main/README.md) for repository setup.

## How selection works

The service watches Kodi's active video player. After playback starts, Smart Subtitles retries stream checks for up to 10 seconds while the player exposes stream data.

Kodi's forced flag ranks first. Track-name matches rank next: Signs and Songs terms rank above other configured keywords. Name matching ignores case and matches whole words. The built-in `signs` and `songs` terms also match singular forms.

The default keywords are `signs,songs,forced`. An empty keyword setting disables name matching, while Kodi-forced streams remain eligible. Full-dialogue tracks do not match by name unless the track name also identifies a forced stream.

With **Only select tracks in my preferred subtitle language** enabled, the service uses Kodi's preferred subtitle language. For special values such as `forced_only`, the service also reads Kodi's subtitle download-language setting. Kodi's interface language also counts for `forced_only` and supplies the final fallback. If the setting is off, the service can match streams in any language.

Smart Subtitles leaves an existing subtitle choice in place when Kodi or the user already selected a track. The service can replace a non-preferred forced track only when Kodi settings provide an authoritative preferred language. No matching or ready stream means no new selection.

## Settings

- **Enable Smart Subtitles:** On by default.
- **Track name keywords:** `signs,songs,forced` by default. Use comma-separated words. Leave the field empty to disable name matching.
- **Only select tracks in my preferred subtitle language:** On by default. The service reads Kodi's language settings; this add-on has no separate language list.
- **Show notification when a track is selected:** On by default. The toast appears after Kodi confirms the selected stream.

## PlexKodiConnect

Smart Subtitles writes a `SmartSubtitles.Result` property on Kodi's home window for PlexKodiConnect (PKC). PKC can apply the Plex subtitle choice after a no-match or disabled result.

PKC versions before `3.12.60` may overwrite Smart Subtitles' choice when PKC's `subtitleStreamPick` setting is `0`; Smart Subtitles logs a warning for that case. PKC version `3.12.60` and later leaves the choice to Smart Subtitles when the add-on is enabled.

## Source layout

- `resources/lib/service_entry.py` starts the service.
- `resources/lib/service.py` runs the Kodi monitor.
- `resources/lib/player.py` watches video playback and retries stream selection.
- `resources/lib/subtitles.py` reads Kodi streams and applies selection rules.
- `resources/settings.xml` defines add-on settings.
- `tests/` contains Python unit tests and settings-schema checks.

## Tests and maintenance

The configured publish test is:

```sh
python3 -m unittest discover -s tests
```

The tracked branch is `main`. A configured source webhook forwards tracked-branch pushes to the central publish workflow. If the webhook is absent or rejected, use the manual workflow described in the central guide. Enable local publish checks with `git config core.hooksPath .githooks`.

README-only edits do not need an add-on version bump. Changes to shipped behavior need a new `addon.xml` version and a matching first line in `<news>`. Follow the central [versioning rules](https://github.com/primez-x/kodi.addons/blob/main/VERSIONING.md).

The Primez source is maintained in [primez-x/service.smartsubtitles](https://github.com/primez-x/service.smartsubtitles). The add-on manifest declares GPL-2.0-only.
