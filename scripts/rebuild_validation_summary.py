#!/usr/bin/env python3
"""Rebuild a completed package summary and manifest from saved validation reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline import build_manifest, load_json, write_json


FAILED_VALIDATION_REASON = (
    "One or more content, rendering, or visual validations did not pass."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    job_dir = args.job_dir.resolve()

    markdown = load_json(job_dir / "markdown-validation.json")
    disclosure = load_json(job_dir / "public-disclosure-validation.json")
    model = load_json(job_dir / "model-validation.json")
    visual = load_json(job_dir / "visual-validation.json")
    render = load_json(job_dir / "render-report.json")
    combined = {
        "passed": bool(
            markdown.get("passed")
            and disclosure.get("passed")
            and model.get("passed")
            and visual.get("passed")
            and render.get("status") == "pass"
        ),
        "markdown": markdown,
        "public_disclosure": disclosure,
        "model": model,
        "visual": visual,
        "render": {
            "status": render.get("status"),
            "metrics": render.get("metrics", {}),
        },
    }
    write_json(job_dir / "validation-summary.json", combined)

    decision = load_json(job_dir / "decision.json")
    if combined["passed"]:
        decision["review_reasons"] = [
            reason
            for reason in decision.get("review_reasons", [])
            if reason != FAILED_VALIDATION_REASON
        ]
        write_json(job_dir / "decision.json", decision)
    build_manifest(job_dir)
    print(f"passed={combined['passed']} job_dir={job_dir}")
    return 0 if combined["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
