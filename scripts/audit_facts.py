#!/usr/bin/env python3
"""Check fact-registry structure and every retained evidence pointer."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FACT_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
REQUIRED_FACT_KEYS = {
    "id",
    "category",
    "claim_en",
    "claim_ru",
    "status",
    "allowed_for_generation",
    "requires_confirmation",
    "evidence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "registry",
        type=Path,
        nargs="?",
        default=Path("master/facts.json"),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def audit(path: Path) -> dict[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    statuses = set(registry.get("status_definitions", {}))
    facts = registry.get("facts")
    if not isinstance(facts, list):
        return {
            "registry": str(path.resolve()),
            "errors": ["Top-level facts must be an array."],
            "warnings": [],
            "passed": False,
        }

    seen: set[str] = set()
    evidence_count = 0
    confirmation_count = 0
    for index, fact in enumerate(facts):
        label = fact.get("id", f"facts[{index}]") if isinstance(fact, dict) else f"facts[{index}]"
        if not isinstance(fact, dict):
            errors.append(f"{label}: fact must be an object.")
            continue
        missing = sorted(REQUIRED_FACT_KEYS - set(fact))
        if missing:
            errors.append(f"{label}: missing keys: {', '.join(missing)}.")
            continue
        fact_id = fact["id"]
        if not isinstance(fact_id, str) or not FACT_ID.fullmatch(fact_id):
            errors.append(f"{label}: invalid fact ID.")
        if fact_id in seen:
            errors.append(f"{label}: duplicate fact ID.")
        seen.add(fact_id)
        if fact["status"] not in statuses:
            errors.append(f"{label}: unknown status {fact['status']!r}.")
        for key in ("category", "claim_en", "claim_ru"):
            if not isinstance(fact[key], str) or not fact[key].strip():
                errors.append(f"{label}: {key} must be a non-empty string.")
        for key in ("allowed_for_generation", "requires_confirmation"):
            if not isinstance(fact[key], bool):
                errors.append(f"{label}: {key} must be boolean.")
        if fact["requires_confirmation"]:
            confirmation_count += 1
            if not str(fact.get("notes", "")).strip():
                errors.append(f"{label}: confirmation-required fact needs notes.")

        evidence = fact["evidence"]
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{label}: evidence must be a non-empty array.")
            continue
        for pointer in evidence:
            evidence_count += 1
            if not isinstance(pointer, dict):
                errors.append(f"{label}: evidence pointer must be an object.")
                continue
            source = pointer.get("path")
            line = pointer.get("line")
            if not isinstance(source, str) or not source.strip():
                errors.append(f"{label}: evidence path is missing.")
                continue
            source_path = (base / source).resolve()
            if base not in source_path.parents:
                errors.append(f"{label}: evidence escapes master/: {source}.")
                continue
            if not source_path.is_file():
                errors.append(f"{label}: evidence file is missing: {source}.")
                continue
            if not isinstance(line, int) or line < 1:
                errors.append(f"{label}: evidence line must be a positive integer.")
                continue
            line_count = len(source_path.read_text(encoding="utf-8").splitlines())
            if line > line_count:
                errors.append(
                    f"{label}: evidence line {line} exceeds {source} ({line_count})."
                )

    if confirmation_count:
        warnings.append(
            f"{confirmation_count} fact(s) still require candidate confirmation."
        )
    return {
        "registry": str(path.resolve()),
        "registry_version": registry.get("registry_version"),
        "fact_count": len(facts),
        "evidence_pointer_count": evidence_count,
        "confirmation_required_count": confirmation_count,
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }


def main() -> int:
    args = parse_args()
    try:
        result = audit(args.registry.resolve())
    except (OSError, json.JSONDecodeError) as error:
        result = {
            "registry": str(args.registry.resolve()),
            "errors": [str(error)],
            "warnings": [],
            "passed": False,
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
