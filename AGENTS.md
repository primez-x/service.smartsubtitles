# Repository Guidelines

service.smartsubtitles is published to the Primez Kodi repository from this repository.

## Primez Publish Rules

Pushes to tracked branch `main` run the Primez publish check. Kodi auto-update follows the add-on version, not the Git SHA. Read [VERSIONING.md](https://github.com/primez-x/kodi.addons/blob/main/VERSIONING.md) for release levels and non-release cases.

A change to a shipped file that a user or another add-on can notice needs a release. Raise exactly one version component by 1 from the branch tip. Reset every component to the right to 0, and keep the same component count. Add a `<news>` entry at the top. Name the new version in the first line, using the add-on's existing format.

Changing the version in `addon.xml` always makes a release. A push that changes only non-shipped files needs no version bump or news entry. Add `No-Release: <reason>` to the tip commit message for a change to a shipped file without an effect.

Adopting an upstream version is the only allowed version jump. Add `Version-Jump: <reason>` to the tip commit message. Keep the adopted version higher than the current version. Run every command in `.primez-publish.json` on every push, including non-release pushes.

`.githooks/pre-push` runs the publish check before each push leaves the machine. Enable the local hook with `git config core.hooksPath .githooks` (Claude Code sessions do this automatically). CI runs the same check through `.github/workflows/publish-check.yml`. `.githooks/publish_check.py` copies the master file `primez-x/kodi.addons/tools/publish_check.py`. Update the master file in `primez-x/kodi.addons`, then copy the file to this add-on's `.githooks/publish_check.py`.
