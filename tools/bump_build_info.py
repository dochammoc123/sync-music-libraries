from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _local_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class BuildInfo:
    version: str = "0.1.0"
    last_commit: str = ""
    last_built: str = ""  # "deploy" / runtime stamp; kept separate from last_commit

    def to_lines(self) -> list[str]:
        return [
            f"version={self.version}",
            f"last_commit={self.last_commit}",
            f"last_built={self.last_built}",
        ]


_VER_RE = re.compile(r"^\s*version\s*=\s*(.+?)\s*$", re.IGNORECASE)
_COMMIT_RE = re.compile(r"^\s*last_commit\s*=", re.IGNORECASE)
_BUILT_RE = re.compile(r"^\s*last_built\s*=", re.IGNORECASE)


def _parse(path: Path) -> BuildInfo:
    bi = BuildInfo()
    if not path.exists():
        return bi
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _VER_RE.match(line)
        if m:
            bi.version = (m.group(1) or "").strip() or bi.version
            continue
        if _COMMIT_RE.match(line):
            # handled by full-line parse elsewhere
            if "=" in line:
                bi.last_commit = line.split("=", 1)[1].strip()
            continue
        if _BUILT_RE.match(line):
            if "=" in line:
                bi.last_built = line.split("=", 1)[1].strip()
            continue
    return bi


def _write(path: Path, bi: BuildInfo) -> None:
    path.write_text("\n".join(bi.to_lines()) + "\n", encoding="utf-8")


_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:(?:[-+.])([0-9A-Za-z._-]+))?$"
)


def bump_semver(ver: str) -> str:
    """
    Bump the PATCH component.
    - If the version is not a simple X.Y.Z semver, return ver unchanged and let humans edit.
    """
    v = (ver or "").strip()
    m = _SEMVER_RE.match(v)
    if not m:
        return v
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    patch += 1
    return f"{major}.{minor}.{patch}"


def main(argv: list[str]) -> int:
    # Repo root: .../sync-music-libraries/tools/bump_build_info.py
    root = Path(__file__).resolve().parents[1]
    p = root / "build_info.txt"
    if os_env_disabled():
        return 0

    bi = _parse(p)
    if "--set" in argv:
        # tools/bump_build_info.py --set 1.2.3
        try:
            idx = argv.index("--set")
            bi.version = argv[idx + 1].strip()
        except Exception:
            print("ERROR: --set requires a version string", file=sys.stderr)
            return 2
    elif "--bump-patch" in argv:
        bi.version = bump_semver(bi.version) or bi.version
    else:
        # Default: do NOT change semver automatically. `version=` is expected to be edited
        # manually when you want a release bump; we only stamp when this code last changed in git.
        pass
    bi.last_commit = _local_now_iso()
    # Preserve last_built/last deploy stamp; deploy script updates it independently.
    _write(p, bi)
    return 0


def os_env_disabled() -> bool:
    v = (os.environ.get("BUMP_BUILD_INFO_ON_COMMIT") or "").strip().lower()
    return v in {"0", "false", "no", "off"}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

