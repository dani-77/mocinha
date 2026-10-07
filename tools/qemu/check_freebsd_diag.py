#!/usr/bin/env python3
"""Compares single-user diagnostics of an installed FreeBSD target with expectations.

    check_freebsd_diag.py SERIAL_LOG EXPECT_JSON
"""

import json
import re
import sys
from pathlib import Path


def sections(log: str) -> dict:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", log).replace("\r", "")
    start = text.rfind("\n===DIAG_START===")
    end = text.rfind("\n===DIAG_END===")
    if start < 0 or end < start:
        raise SystemExit("DIAGNOSTICS MISSING: single-user shell did not run the diagnostics")
    result, current = {}, None
    for line in text[start:end].splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            result[current] = []
        elif current and not re.match(r"^\S*@\S*[:/].*# ", line):  # skip echoed prompt lines
            result[current].append(line.rstrip())
    return result


def main() -> int:
    s = sections(Path(sys.argv[1]).read_text(errors="replace"))
    expect = json.loads(Path(sys.argv[2]).read_text())
    problems = []
    for section, needles in expect.get("contains", {}).items():
        body = "\n".join(s.get(section, []))
        for needle in needles:
            if needle not in body:
                problems.append(f"{section}: missing {needle!r}")
    for section, needles in expect.get("absent", {}).items():
        body = "\n".join(s.get(section, []))
        for needle in needles:
            if needle in body:
                problems.append(f"{section}: unexpected {needle!r}")
    for section, lines in expect.get("exact_lines", {}).items():
        got = sorted(tok for l in s.get(section, []) for tok in l.split())
        if got != sorted(lines):
            problems.append(f"{section}: {got} != {sorted(lines)}")
    Path(sys.argv[1]).with_name("diagnostics.json").write_text(json.dumps(s, indent=2))
    if problems:
        print(f"EQUIVALENCE FAILED ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"EQUIVALENCE PASSED: installed system matches {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
