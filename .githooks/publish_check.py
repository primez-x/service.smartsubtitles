#!/usr/bin/env python3
"""Primez publish check for a Kodi add-on repository.

Copied unchanged into every published add-on repository as
``.githooks/publish_check.py`` (the master copy lives in
primez-x/kodi.addons ``tools/publish_check.py``). It enforces the rules the
repository publish (kodi.addons ``tools/build_repository.py``) applies, so an
update is rejected before it is pushed instead of failing the publish:

* the root ``addon.xml`` parses and has a dot-separated numeric version,
* that version is greater than the one it replaces,
* the first line of ``<news>`` names that version,
* every test command in ``.primez-publish.json`` passes on the exact commit.

Usage:
    publish_check.py pre-push <remote> <url>   (git pre-push hook, refs on stdin)
    publish_check.py ci                        (GitHub Actions: HEAD vs. published)
    publish_check.py check [<commit>]          (manual: commit vs. its tracked branch)

``.primez-publish.json`` (repository root)::

    {"branch": "main",
     "tests": [["python3", "-m", "unittest", "discover", "-s", "tests"]],
     "test_env": {"PYTHONPATH": "resources/lib"}}

A leading "python3" in a test command runs with the interpreter running this
script. Bypass in an emergency with ``git push --no-verify``; the repository
publish still enforces the same rules.
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CONFIG_FILE = ".primez-publish.json"
DEFAULT_PUBLISHED_BASE_URL = "https://primez-x.github.io/kodi.addons/"
TEST_TIMEOUT_SECONDS = int(os.environ.get("PRIMEZ_TEST_TIMEOUT", "900"))
ZERO_SHA = re.compile(r"^0+$")


class CheckError(Exception):
    pass


def git(*args, check=True):
    result = subprocess.run(
        ["git"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode != 0:
        raise CheckError("git %s failed: %s" % (" ".join(args), result.stderr.decode().strip()))
    return result


def show_file(commit, path):
    result = git("show", "%s:%s" % (commit, path), check=False)
    return result.stdout if result.returncode == 0 else None


def commit_exists(commit):
    return git("cat-file", "-e", "%s^{commit}" % commit, check=False).returncode == 0


def load_config(commit):
    raw = show_file(commit, CONFIG_FILE)
    if raw is None:
        raise CheckError("%s is missing in %s" % (CONFIG_FILE, commit[:12]))
    try:
        config = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise CheckError("%s is not valid JSON: %s" % (CONFIG_FILE, exc))
    if not config.get("branch"):
        raise CheckError('%s needs a "branch"' % CONFIG_FILE)
    return config


def parse_version(value):
    parts = (value or "").strip().split(".")
    if not parts or any(not part.isdecimal() for part in parts):
        raise CheckError("addon.xml version %r is not dot-separated numbers" % value)
    return tuple(int(part) for part in parts)


def compare_versions(left, right):
    left, right = parse_version(left), parse_version(right)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return (left > right) - (left < right)


def addon_info(raw_xml, where):
    if raw_xml is None:
        raise CheckError("addon.xml is missing in %s" % where)
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise CheckError("addon.xml in %s is not valid XML: %s" % (where, exc))
    if root.tag != "addon" or not root.get("id") or not root.get("version"):
        raise CheckError("addon.xml in %s needs an <addon> with id and version" % where)
    parse_version(root.get("version"))
    news = root.find("extension[@point='xbmc.addon.metadata']/news")
    first_line = ""
    if news is not None and news.text:
        first_line = next((line.strip() for line in news.text.splitlines() if line.strip()), "")
    return root.get("id"), root.get("version"), first_line


def check_news(version, first_line):
    if not re.search(r"(?<![\d.])v?%s(?![\d.]*\d)" % re.escape(version), first_line):
        raise CheckError(
            "the first line of <news> must name version %s (found %r); add a news entry "
            "for this release on top" % (version, first_line))


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "primez-publish-check"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def published_state(config, addon_id, repository):
    """Return (published version or None, published sha or None)."""
    base = config.get("published_base_url", DEFAULT_PUBLISHED_BASE_URL)
    base = base if base.endswith("/") else base + "/"
    try:
        addons = ET.fromstring(fetch(base + "addons.xml"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise CheckError("could not read the published addons.xml: HTTP %s" % exc.code)
    except (urllib.error.URLError, OSError, ET.ParseError) as exc:
        raise CheckError("could not read the published addons.xml: %s" % exc)
    version = next((addon.get("version") for addon in addons.findall("addon")
                    if addon.get("id") == addon_id), None)
    sha = None
    try:
        manifest = json.loads(fetch(base + "source-manifest.json").decode("utf-8"))
        for entry in manifest.get("addons", []):
            if entry.get("id") == addon_id and (
                    not repository or entry.get("repository") == repository):
                sha = entry.get("sha")
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return version, sha


def export_commit(commit, destination):
    archive = git("archive", "--format=tar", commit).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, filter="data")
        else:
            tar.extractall(destination)


def run_tests(commit, config, local):
    commands = config.get("tests") or []
    if not commands:
        print("publish check: no tests configured")
        return
    env = dict(os.environ)
    env.update({key: str(value) for key, value in config.get("test_env", {}).items()})
    env.pop("GITHUB_TOKEN", None)
    with tempfile.TemporaryDirectory(prefix="primez-publish-check-") as root:
        export_commit(commit, root)
        for command in commands:
            argv = [sys.executable if index == 0 and part == "python3" else part
                    for index, part in enumerate(command)]
            if local and len(argv) > 2 and argv[1] == "-m" \
                    and importlib.util.find_spec(argv[2]) is None:
                print("publish check: WARNING: skipping %r, Python module %r is not "
                      "installed here (the repository publish still runs it)"
                      % (" ".join(command), argv[2]))
                continue
            print("publish check: running %s" % " ".join(command))
            try:
                result = subprocess.run(
                    argv, cwd=root, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=TEST_TIMEOUT_SECONDS, check=False)
            except subprocess.TimeoutExpired:
                raise CheckError("tests timed out after %ss: %s"
                                 % (TEST_TIMEOUT_SECONDS, " ".join(command)))
            if result.returncode != 0:
                tail = "\n".join(result.stdout.decode("utf-8", "replace").splitlines()[-40:])
                raise CheckError("tests failed (exit %s): %s\n%s"
                                 % (result.returncode, " ".join(command), tail))


def check_commit(commit, config, previous_version, local, already_published=False):
    """Check one commit; previous_version is the version it replaces (or None)."""
    addon_id, version, news = addon_info(show_file(commit, "addon.xml"), commit[:12])
    if already_published:
        print("publish check: %s %s is already published from this commit" % (addon_id, version))
    elif previous_version is not None and compare_versions(version, previous_version) <= 0:
        raise CheckError(
            "%s version must increase: %s does not exceed %s. Bump the version in "
            "addon.xml (and add a news entry) in this push" % (addon_id, version, previous_version))
    check_news(version, news)
    run_tests(commit, config, local)
    print("publish check: %s %s OK" % (addon_id, version))


def pre_push(lines):
    checked = False
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = parts
        if ZERO_SHA.match(local_sha):
            continue  # deleting a ref
        config = load_config(local_sha)
        if remote_ref != "refs/heads/%s" % config["branch"]:
            continue
        previous = None
        if not ZERO_SHA.match(remote_sha):
            if not commit_exists(remote_sha):
                raise CheckError("the remote %s has commits you don't have; pull first"
                                 % config["branch"])
            previous = addon_info(show_file(remote_sha, "addon.xml"), remote_sha[:12])[1]
        print("publish check: checking %s -> %s" % (local_sha[:12], config["branch"]))
        check_commit(local_sha, config, previous, local=True)
        checked = True
    return checked


def ci():
    commit = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD").stdout.decode().strip()
    config = load_config(commit)
    addon_id, _version, _news = addon_info(show_file(commit, "addon.xml"), commit[:12])
    published_version, published_sha = published_state(
        config, addon_id, os.environ.get("GITHUB_REPOSITORY"))
    check_commit(commit, config, published_version, local=False,
                 already_published=published_sha == commit)


def manual(commit):
    commit = git("rev-parse", commit).stdout.decode().strip()
    config = load_config(commit)
    remote = "origin/%s" % config["branch"]
    previous = None
    if git("rev-parse", "--verify", "-q", remote, check=False).returncode == 0:
        remote_sha = git("rev-parse", remote).stdout.decode().strip()
        if remote_sha == commit:
            print("publish check: %s is the tip of %s; comparing with its parent" % (commit[:12], remote))
            remote_sha = git("rev-parse", "%s^" % commit, check=False).stdout.decode().strip()
        if remote_sha and commit_exists(remote_sha):
            previous = addon_info(show_file(remote_sha, "addon.xml"), remote_sha[:12])[1]
    check_commit(commit, config, previous, local=True)


def main(argv):
    mode = argv[1] if len(argv) > 1 else "check"
    try:
        if mode == "pre-push":
            pre_push(sys.stdin.read().splitlines())
        elif mode == "ci":
            ci()
        elif mode == "check":
            manual(argv[2] if len(argv) > 2 else "HEAD")
        else:
            raise CheckError("unknown mode %r" % mode)
    except CheckError as exc:
        print("publish check FAILED: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
