#!/usr/bin/env python3
"""Prepare the pinned Logto release; never seed a database or start a listener.

Run only on hklite-lv4 as allira. The upstream MPL-2.0 LICENSE and all bundled
third-party notices remain inside the release. Only the listener host is patched.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import stat
import tarfile
import urllib.request
from pathlib import Path

VERSION = "1.46.0"
COMMIT = "d179bf9d2d51104236a342b90c2841df66e4ba5b"
SIZE = 291001448
SHA256 = "140ca60719d60866782527aeb4cf72caee6d3fba3c730b9214e0a43326a96382"
URL = f"https://github.com/logto-io/logto/releases/download/v{VERSION}/logto.tar.gz"
BASE = Path("/var/lib/allira/logto")
BUNDLE = Path("packages/core/build/main-5SWK6CWG.js")
BUNDLE_SHA256 = "e58ee9a9451d8e4847fa7c60c235f1c96ae33dcf293aa2440a7b5ae7e0a9b7d7"
SOURCE = Path("packages/core/src/app/init.ts")
REPLACEMENTS = (
    (".listen(urlSet.port, () => {", ".listen(urlSet.port, '127.0.0.1', () => {"),
    (".listen(adminUrlSet.port, () => {", ".listen(adminUrlSet.port, '127.0.0.1', () => {"),
)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def assert_private_dir(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path:
        raise RuntimeError(f"Refuse non-canonical directory: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"Wrong directory owner/type: {path}")
    path.chmod(0o700)


def patch_listener(root: Path, relative: Path, evidence: Path) -> dict[str, str]:
    target = root / relative
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"Missing regular listener source: {relative}")
    before = target.read_text()
    if relative == BUNDLE and digest(target) != BUNDLE_SHA256:
        raise RuntimeError("Unexpected compiled bundle digest")
    after = before
    for old, new in REPLACEMENTS:
        if after.count(old) != 2:
            raise RuntimeError(f"Expected exactly two pinned listener forms: {relative}")
        after = after.replace(old, new)
    if sum(".listen(" in line for line in after.splitlines()) != 4:
        raise RuntimeError("Unexpected additional listener in patched file")
    if any("'127.0.0.1'" not in line for line in after.splitlines() if ".listen(" in line):
        raise RuntimeError("A listener remains non-loopback")
    name = "core-bundle" if relative == BUNDLE else "core-source"
    original = evidence / f"{name}.original"
    original.write_text(before)
    original.chmod(0o600)
    target.write_text(after)
    patch = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{relative}", tofile=f"b/{relative}",
    ))
    (evidence / f"{name}.patch").write_text(patch)
    return {"path": str(relative), "beforeSha256": digest(original), "afterSha256": digest(target)}


def verify_prepared(release: Path) -> None:
    marker = json.loads((release / "allira-preparation.json").read_text())
    if marker["releaseSha256"] != SHA256 or marker["commit"] != COMMIT:
        raise RuntimeError("Unexpected prepared-release provenance")
    for entry in marker["patches"]:
        target = release / entry["path"]
        if digest(target) != entry["afterSha256"]:
            raise RuntimeError("Prepared listener changed after verification")
        listeners = [line for line in target.read_text().splitlines() if ".listen(" in line]
        if len(listeners) != 4 or any("'127.0.0.1'" not in line for line in listeners):
            raise RuntimeError("Prepared listener no longer loopback-only")
    if not (release / "LICENSE").is_file():
        raise RuntimeError("Missing MPL license")


def prepare() -> None:
    if os.getuid() == 0:
        raise RuntimeError("Run as low-privilege allira, not root")
    os.umask(0o077)
    for path in (BASE, BASE / "downloads", BASE / "releases"):
        assert_private_dir(path)
    archive = BASE / "downloads" / f"logto-v{VERSION}.tar.gz"
    if archive.is_symlink():
        raise RuntimeError("Refuse symlink archive")
    if not archive.exists():
        part = archive.with_suffix(".gz.part")
        with part.open("xb") as output, urllib.request.urlopen(URL, timeout=300) as response:
            while block := response.read(1024 * 1024):
                output.write(block)
        if part.stat().st_size != SIZE or digest(part) != SHA256:
            raise RuntimeError("Pinned download checksum or size mismatch; partial file retained")
        if part.resolve() != BASE / "downloads" / f"logto-v{VERSION}.tar.gz.part":
            raise RuntimeError("Unexpected partial archive target")
        if archive.exists() or archive.is_symlink():
            raise RuntimeError("Archive appeared concurrently")
        part.rename(archive)
    if archive.stat().st_size != SIZE or digest(archive) != SHA256:
        raise RuntimeError("Pinned archive checksum or size mismatch")
    release = BASE / "releases" / f"v{VERSION}"
    if release.exists():
        if release.is_symlink() or release.resolve() != release:
            raise RuntimeError("Refuse non-canonical release")
        verify_prepared(release)
        print(json.dumps({"prepared": True, "alreadyPrepared": True, "version": VERSION}))
        return
    stage = BASE / "releases" / f".v{VERSION}.prepare-{os.getpid()}"
    stage.mkdir(mode=0o700)
    with tarfile.open(archive) as package:
        if any(member.name.split("/")[0] != "logto" for member in package.getmembers()):
            raise RuntimeError("Unexpected archive root")
        package.extractall(stage, filter="data")
    root = stage / "logto"
    evidence = root / "allira-patches"
    evidence.mkdir(mode=0o700)
    required = [
        "LICENSE", "packages/cli/bin/logto.js", "packages/core/build/index.js",
        "packages/connectors/connector-mock-sms/lib/index.js",
        "packages/connectors/connector-mock-social/lib/index.js",
        "packages/connectors/connector-http-sms/lib/index.js",
    ]
    for relative in required:
        if not (root / relative).is_file():
            raise RuntimeError(f"Missing pinned runtime component: {relative}")
    patches = [patch_listener(root, relative, evidence) for relative in (SOURCE, BUNDLE)]
    (root / "allira-preparation.json").write_text(json.dumps({
        "version": VERSION, "commit": COMMIT, "releaseUrl": URL,
        "releaseSize": SIZE, "releaseSha256": SHA256, "patches": patches,
        "note": "All four HTTP/HTTPS core/admin listens explicitly bind IPv4 loopback.",
    }, indent=2) + "\n")
    verify_prepared(root)
    if root.resolve() != stage / "logto" or release.exists() or release.is_symlink():
        raise RuntimeError("Unexpected release publish target")
    root.rename(release)
    stage.rmdir()  # Empty, known staging directory only.
    print(json.dumps({"prepared": True, "alreadyPrepared": False, "version": VERSION,
                      "mockSmsIncluded": True, "mockSocialIncluded": True, "listeners": 4}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.verify_only:
        verify_prepared(BASE / "releases" / f"v{VERSION}")
        print(json.dumps({"verified": True, "version": VERSION, "listeners": 4}))
    else:
        prepare()
