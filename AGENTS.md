# Repository Guidelines

service.smartsubtitles is published to the Primez Kodi repository from this repository.

## Primez Publish Rules

The tracked branch is `main`: every push to it is published to the Primez Kodi repository, and every push is exactly one release. Kodi auto-update follows the repository version, not the Git SHA.

Each push raises the root `addon.xml` version by exactly one step from the branch tip — raise one component by 1 and reset the ones after it — and adds a `<news>` entry on top whose first line names the new version (match the existing format). Which component to raise is decided by `VERSIONING.md` in `primez-x/kodi.addons`: **major** when users or other add-ons must act or something breaks, **minor** for new or changed user-visible behaviour, **patch** for fixes, performance, tooling, tests and docs (take the highest level that applies). A push may hold several commits; only its tip version counts. Adopting an upstream version is the only allowed jump and needs a `Version-Jump: <reason>` line in the tip commit message.

`.githooks/pre-push` enforces this before the push leaves the machine (version above the branch tip, news entry, and the `tests` in `.primez-publish.json` passing on the pushed commit); enable it with `git config core.hooksPath .githooks` (Claude Code sessions do this automatically). `.github/workflows/publish-check.yml` runs the same check on GitHub, and the `kodi.addons` publish refuses commits that fail it. `.githooks/publish_check.py` is a copy of `primez-x/kodi.addons` `tools/publish_check.py`; change it there and re-copy it. Keep `.primez-publish.json` tests in sync with how this repository's tests are run.
