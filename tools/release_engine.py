#!/usr/bin/env python3
"""Cut a new engine version: bump config.yaml engine.version and record it in engines.json.

Usage: python tools/release_engine.py <version> "<one-line summary>" ["<change>" ...]
   e.g. python tools/release_engine.py 2.1 "Tighter S3 band" "s3.band_pct 3.0 -> 2.5"

Commit the result together with the scanner/config change it describes. On push to main the
release-tag workflow creates the tag engine-v<version>, which freezes code + config + weights so
the version can be re-run later from the desk for comparison.
"""
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    version, summary, changes = argv[0].lstrip("v"), argv[1], argv[2:]
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        raise SystemExit(f"Version must look like 2.1 or 2.1.1, got {version!r}")

    reg_p = ROOT / "engines.json"
    reg = json.loads(reg_p.read_text())
    known = {v["version"] for v in reg["versions"]}
    if version in known:
        raise SystemExit(f"Engine v{version} already exists in engines.json")
    cur = tuple(map(int, reg["current"].split(".")))
    if tuple(map(int, version.split("."))) <= cur:
        raise SystemExit(f"v{version} is not newer than the current v{reg['current']}")

    cfg_p = ROOT / "config.yaml"
    cfg = cfg_p.read_text()
    new_cfg, n = re.subn(r'(?m)^(engine:\n  version: )"[^"]*"', rf'\1"{version}"', cfg)
    if n != 1:
        raise SystemExit("Could not find the engine.version line in config.yaml")
    cfg_p.write_text(new_cfg)

    reg["current"] = version
    reg["versions"].insert(0, {"version": version, "released": dt.date.today().isoformat(),
                               "tag": f"engine-v{version}", "summary": summary,
                               "changes": changes or [summary]})
    reg_p.write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n")
    print(f"engine v{version} recorded. Commit it with the change; the tag engine-v{version} is created on push to main.")


if __name__ == "__main__":
    main(sys.argv[1:])
