# Repository Guidelines

service.smartsubtitles is published to the Primez Kodi repository from this repository.

## Primez Publish Rules

The tracked branch is `main`: every push to it is published to the Primez Kodi repository. Each push must bump the root `addon.xml` version and add a `<news>` entry on top whose first line names the new version (match the existing format). Kodi auto-update follows the repository version, not the Git SHA.

`.githooks/pre-push` enforces this before the push leaves the machine (version above the branch tip, news entry, and the `tests` in `.primez-publish.json` passing on the pushed commit); enable it with `git config core.hooksPath .githooks` (Claude Code sessions do this automatically). `.github/workflows/publish-check.yml` runs the same check on GitHub, and the `kodi.addons` publish refuses commits that fail it. `.githooks/publish_check.py` is a copy of `primez-x/kodi.addons` `tools/publish_check.py`; change it there and re-copy it. Keep `.primez-publish.json` tests in sync with how this repository's tests are run.
