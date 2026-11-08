#!/usr/bin/env python3
"""Download the pinned zkagent release, verify its checksum, install it.

    python3 scripts/install-zkagent.py            # install to ~/.local/bin
    python3 scripts/install-zkagent.py --dir DEST

The checksums below are the SHA256SUMS published with the release, pasted here so the
plugin's install step is reproducible without trusting the network twice. A mismatch is a
hard stop: nothing is written to DEST unless the digest matches.

This is a fetch at setup from the upstream project's own release, pinned by checksum and
version, as the catalog rules allow. No binary is stored in this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import stat
import sys
import urllib.request
from pathlib import Path

VERSION = "v0.4.1+2026-11-08"
REPO = "MCPVOT/zkagent"

# name -> sha256, from the release's SHA256SUMS
ASSETS = {
    "linux-x86_64": ("zkagent-linux-x86_64", "4f9d18b4ee9bbe4790a2b922fcf348a125937dd09a3322d8179842377ff2d5d5"),
    "macos-aarch64": ("zkagent-macos-aarch64", "72f5a0635f6651cc7f5b4f685b4e5cf58bb5422b38cefd1e144adb0dd00c6db8"),
    "windows-x86_64": ("zkagent-windows-x86_64.exe", "3c8f8823b87eb92634ffa80d15d5f5eac13a654e672d876ec21c8cf301fd79ab"),
}


def current_asset() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    if system == "linux" and machine in ("amd64", "x86_64"):
        return "linux-x86_64"
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-aarch64"
    raise SystemExit(f"no pinned asset for {system}/{machine}; build from source instead: "
                     f"https://github.com/{REPO}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(Path.home() / ".local" / "bin"))
    parser.add_argument("--asset", default=None, help="override the detected asset key")
    parser.add_argument("--check", action="store_true", help="verify only, do not install")
    args = parser.parse_args()

    key = args.asset or current_asset()
    if key not in ASSETS:
        raise SystemExit(f"unknown asset {key!r}; choose from {', '.join(ASSETS)}")
    name, expected = ASSETS[key]

    url = f"https://github.com/{REPO}/releases/download/{VERSION}/{name}"
    print(f"fetch  {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()

    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        print(f"FAIL   checksum mismatch\n       expected {expected}\n       got      {digest}")
        return 1
    print(f"ok     sha256 {digest}")

    if args.check:
        return 0

    dest = Path(args.dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / ("zkagent.exe" if key.startswith("windows") else "zkagent")
    tmp = target.with_suffix(".download")
    tmp.write_bytes(data)
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp, target)
    print(f"install {target}")
    print(f"set ZKAGENT_BIN={target} if {dest} is not on PATH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
