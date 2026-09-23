#!/usr/bin/env python3
"""Check public materials against a private, local disclosure policy."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/public-disclosure.json"


def load_patterns() -> tuple[list[tuple[str, re.Pattern[str]]], str | None]:
    if not POLICY_PATH.is_file():
        return [], "Local disclosure policy is missing."
    try:
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        entries = data["prohibited_patterns"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("prohibited_patterns must be a nonempty list")
        patterns = [
            (str(entry["id"]), re.compile(str(entry["pattern"]), re.IGNORECASE))
            for entry in entries
        ]
        return patterns, None
    except (OSError, ValueError, KeyError, TypeError, re.error) as error:
        return [], f"Invalid local disclosure policy: {error}"


def find_violations(text: str) -> list[dict[str, object]]:
    patterns, error = load_patterns()
    if error:
        return [{"rule": "policy-unavailable", "line": 0, "match": error}]
    violations = []
    for rule, pattern in patterns:
        for match in pattern.finditer(text):
            violations.append({
                "rule": rule,
                "line": text.count("\n", 0, match.start()) + 1,
                "match": match.group(0),
            })
    return violations


def validate(paths: list[Path]) -> dict[str, object]:
    files = []
    for path in paths:
        violations = find_violations(path.read_text(encoding="utf-8"))
        files.append({
            "path": str(path.resolve()),
            "violations": violations,
            "passed": not violations,
        })
    return {"passed": all(file["passed"] for file in files), "files": files}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate([path.resolve() for path in args.paths])
    except OSError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
