from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp(path: Path) -> None:
    if not path.exists():
        # Ensure file exists; version will be set by repo template; last_built added here.
        path.write_text("version=0.0.0-dev\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    out: list[str] = []
    version = "0.0.0-dev"
    found_built = False
    ver_re = re.compile(r"^\s*version\s*=\s*(.+?)\s*$", re.IGNORECASE)
    built_re = re.compile(r"^\s*last_built\s*=", re.IGNORECASE)
    for ln in lines:
        m = ver_re.match(ln)
        if m:
            version = m.group(1).strip() or version
            out.append(f"version={version}")
            continue
        if built_re.match(ln):
            out.append(f"last_built={_utc_now_iso()}")
            found_built = True
            continue
        if ln.strip():
            out.append(ln)
    if not any(ver_re.match(x) for x in out if x is not None):
        out.insert(0, f"version={version}")
    if not found_built:
        out.append(f"last_built={_utc_now_iso()}")
    path.write_text("\n".join([x for x in out if x is not None]) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: stamp_build_info.py <deploy_dir>", file=sys.stderr)
        return 2
    deploy_dir = Path(sys.argv[1])
    p = deploy_dir / "build_info.txt"
    stamp(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
