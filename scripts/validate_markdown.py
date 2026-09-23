#!/usr/bin/env python3
"""Run deterministic structural checks on a tailored resume Markdown file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from check_public_disclosure import find_violations


REQUIRED_HEADINGS = [
    "## PROFESSIONAL SUMMARY",
    "## CORE SKILLS",
    "## PROFESSIONAL EXPERIENCE",
]
PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}|\bTODO\b|\bTBD\b", re.IGNORECASE)
HTML_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
EXPERIENCE_HEADING = re.compile(r"^### .+ — .+   \|   \*\*.+\*\*$")
CORE_SKILL_LINE = re.compile(r"^\*\*[^*:\n]{2,60}:\*\*\s+\S.+$")
NUMERIC_ANCHOR = re.compile(
    r"(?<![A-Za-z0-9])"
    r"\d+(?:[.,]\d+)?"
    r"(?:\+|%|[–—-]\d+(?:[.,]\d+)?(?:\+|%)?)?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def validate(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    errors: list[str] = []
    warnings: list[str] = []

    if "```" in text:
        errors.append("Resume must not contain a fenced code block.")
    for violation in find_violations(text):
        errors.append(
            "Public disclosure policy violation "
            f"{violation['rule']} on line {violation['line']}: "
            f"{violation['match']}"
        )
    if PLACEHOLDER.search(text):
        errors.append("Resume contains a placeholder or TODO marker.")
    if not text.lstrip().startswith('<div style="text-align: center">'):
        errors.append("Resume must start with the centered header div.")
    if "</div>\n\n---" not in text and "</div>\r\n\r\n---" not in text:
        errors.append("Header div must be followed by a horizontal rule.")

    heading_positions = []
    for heading in REQUIRED_HEADINGS:
        try:
            heading_positions.append(lines.index(heading))
        except ValueError:
            errors.append(f"Missing heading: {heading}")
    if len(heading_positions) == len(REQUIRED_HEADINGS):
        if heading_positions != sorted(heading_positions):
            errors.append("Required resume sections are out of order.")

    try:
        summary_start = lines.index("## PROFESSIONAL SUMMARY") + 1
        summary_end = lines.index("## CORE SKILLS")
    except ValueError:
        summary_text = ""
    else:
        summary_text = " ".join(
            line.strip() for line in lines[summary_start:summary_end] if line.strip()
        )
    summary_numeric_anchors = NUMERIC_ANCHOR.findall(summary_text)
    profile_path = Path(__file__).resolve().parents[1] / "config/candidate-profile.json"
    profile = {}
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    expected_name = profile.get("name_en")
    expected_email = profile.get("email")
    if not expected_name or not expected_email:
        errors.append("Local candidate profile is missing a name or email.")
    else:
        if f"# {expected_name}" not in text:
            errors.append("Configured candidate name is missing.")
        if expected_email not in text:
            errors.append("Configured email is missing.")

    allowed_tags = {"div"}
    for tag in HTML_TAG.findall(text):
        if tag.lower() not in allowed_tags:
            errors.append(f"Raw HTML tag is not allowlisted: {tag}")

    experience_headings = [line for line in lines if line.startswith("### ")]
    if not experience_headings:
        errors.append("No experience headings found.")
    for line in experience_headings:
        normalized_heading = line.replace("&nbsp;", " ")
        if not EXPERIENCE_HEADING.fullmatch(normalized_heading):
            errors.append(
                "Experience heading does not match the one-line company/title/date "
                f"format: {line}"
            )

    try:
        core_start = lines.index("## CORE SKILLS") + 1
        core_end = lines.index("## PROFESSIONAL EXPERIENCE")
    except ValueError:
        core_skill_lines: list[str] = []
    else:
        core_skill_lines = [
            line.strip() for line in lines[core_start:core_end] if line.strip()
        ]
        if not 1 <= len(core_skill_lines) <= 6:
            errors.append(
                "CORE SKILLS must contain 1–6 labeled one-line groups; "
                f"found {len(core_skill_lines)}."
            )
        for line in core_skill_lines:
            if not CORE_SKILL_LINE.fullmatch(line):
                errors.append(
                    "Each CORE SKILLS source line must start with a bold "
                    f"category and colon: {line}"
                )

    plain_text = re.sub(r"<[^>]+>|[#*_`\[\](){}]", " ", text)
    word_count = len(re.findall(r"\b[\w’'+/-]+\b", plain_text, re.UNICODE))
    if word_count < 420:
        warnings.append(f"Resume is unusually short for the one-page target: {word_count} words.")
    if word_count > 760:
        warnings.append(f"Resume is unusually long for the one-page target: {word_count} words.")

    if profile.get("name_en_requires_confirmation"):
        warnings.append(
            "Confirm name spelling against passport and official documents before sending."
        )
    return {
        "path": str(path.resolve()),
        "word_count": word_count,
        "experience_heading_count": len(experience_headings),
        "core_skill_group_count": len(core_skill_lines),
        "summary_numeric_anchor_count": len(summary_numeric_anchors),
        "summary_numeric_anchors": summary_numeric_anchors,
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }


def main() -> int:
    args = parse_args()
    try:
        result = validate(args.markdown.resolve())
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
