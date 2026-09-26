"""Report whether the *currently running* interpreter has everything the
digitize-firm-map pipeline needs.

Usage:
    <python> env_check.py

Prints a JSON report to stdout:
    {"ok": true, "python": "C:\\...\\python.exe", "missing": []}
  or
    {"ok": false, "python": "...", "missing": ["cv2", "fiona"]}

Why this exists: on Windows, plain `python`/`python3` on PATH often resolve to
the Microsoft Store stub rather than a real interpreter, while a full
GIS-capable Python (e.g. an Anaconda install) may be present but not on PATH.
Claude should try a short list of candidate interpreters (see SKILL.md) and
run this script under each one until it finds one that reports "ok": true,
then use that same interpreter path for every other script in this skill.
"""
import json
import sys

from firm_common import REQUIRED_PACKAGES


def main():
    missing = []
    for mod in REQUIRED_PACKAGES:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    report = {
        "ok": len(missing) == 0,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "missing": missing,
    }
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
