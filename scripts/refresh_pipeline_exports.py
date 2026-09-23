#!/usr/bin/env python3
"""Refresh queue, package, and checkpoint exports from pipeline state."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "state/jobs.sqlite"
EXPORT_DIR = ROOT / "workspace/chrome-searches/20260729"
STATUS_PATH = EXPORT_DIR / "pipeline-status.json"
CORRECTED_REPORT = EXPORT_DIR / "pipeline-corrected-20260729.json"
RESET_TIME = "Aug 5th, 2026 11:08 AM"
RESUME_COMMAND = (
    "CODEX_HOME=/private/tmp/codex-job-search-runtime "
    "JOB_SEARCH_CODEX_SANDBOX=danger-full-access "
    "JOB_SEARCH_PDF_RENDERER=reportlab "
    ".venv/bin/python scripts/process_queue_parallel.py "
    "--workers 6 --batch-size 10 --timeout 3600 "
    "--report workspace/chrome-searches/20260729/"
    "pipeline-resume-after-20260805.json"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_issues(validation: dict[str, Any]) -> list[str]:
    if validation.get("passed"):
        return []
    issues = []
    render_status = validation.get("render", {}).get("status")
    if render_status != "pass":
        issues.append(f"render:{render_status or 'missing'}")
    checks = (
        ("markdown", "markdown"),
        ("public_disclosure", "public-disclosure"),
        ("model", "model"),
        ("visual", "visual"),
    )
    for key, label in checks:
        value = validation.get(key)
        if not isinstance(value, dict) or value.get("passed") is not True:
            issues.append(label)
    if validation.get("summary"):
        issues.append(str(validation["summary"]))
    return list(dict.fromkeys(issues))


def verify_manifest(package: Path) -> list[str]:
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    errors = []
    manifest = load_json(manifest_path)
    for item in manifest.get("files", []):
        relative = Path(item["path"])
        path = package / relative
        if not path.is_file():
            errors.append(f"{relative}: missing")
            continue
        if sha256_file(path) != item.get("sha256"):
            errors.append(f"{relative}: sha256 mismatch")
        if path.stat().st_size != item.get("bytes"):
            errors.append(f"{relative}: byte-size mismatch")
    return errors


def queue_stage(job_dir: Path) -> str:
    present = {path.name for path in job_dir.iterdir() if path.is_file()}
    evaluation = {"vacancy.json", "avg-match.json", "red-team-match.json", "decision.json"}
    if evaluation <= present:
        if {"resume.md", "cover-letter.md"} <= present:
            return "evaluated-partial-validation"
        return "evaluated-partial-materials"
    if "avg-match.json" in present or "red-team-match.json" in present:
        return "partial-evaluation"
    if "vacancy.json" in present:
        return "normalized-awaiting-evaluation"
    return "queued-unprocessed"


def latest_error(job_dir: Path) -> str:
    logs = job_dir / "logs"
    if not logs.is_dir():
        return ""
    candidates = sorted(
        logs.glob("*.stderr.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        limit = re.search(
            r"You've hit your usage limit\..*?try again at [^.]+\.",
            text,
            flags=re.DOTALL,
        )
        if limit:
            return re.sub(r"\s+", " ", limit.group(0)).strip()
        return re.sub(r"\s+", " ", text.splitlines()[-1]).strip()[:500]
    return ""


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    previous = load_json(STATUS_PATH) if STATUS_PATH.is_file() else {}
    corrected = load_json(CORRECTED_REPORT)
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        jobs = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at"
        ).fetchall()
        missing_skill_counts = connection.execute(
            """
            SELECT COUNT(*) AS rows, COUNT(DISTINCT job_id) AS jobs
            FROM missing_skills
            """
        ).fetchone()

    database_counts = Counter(str(row["status"]) for row in jobs)
    queued_rows = [row for row in jobs if row["status"] == "queued"]
    remaining = []
    stage_counts: Counter[str] = Counter()
    for row in queued_rows:
        job_dir = Path(row["current_path"])
        stage = queue_stage(job_dir)
        stage_counts[stage] += 1
        remaining.append(
            {
                "job_id": row["job_id"],
                "company": row["company"],
                "title": row["title"],
                "source_url": row["source_url"],
                "stage": stage,
                "last_error": latest_error(job_dir),
                "current_path": str(job_dir),
            }
        )

    packages = []
    ready_packages = []
    manifest_errors = []
    for row in jobs:
        if row["status"] not in {"apply", "need-review"}:
            continue
        package = Path(row["current_path"])
        validation_path = package / "validation-summary.json"
        validation = load_json(validation_path) if validation_path.is_file() else {}
        decision = load_json(package / "decision.json")
        issues = package_issues(validation)
        manifest_issues = verify_manifest(package)
        if manifest_issues:
            manifest_errors.append(
                {
                    "job_id": row["job_id"],
                    "package_path": str(package),
                    "errors": manifest_issues,
                }
            )
        item = {
            "job_id": row["job_id"],
            "company": row["company"],
            "title": row["title"],
            "category": row["status"],
            "source_url": row["source_url"],
            "avg_match": decision.get("avg_match", ""),
            "red_team_match": decision.get("red_team_match", ""),
            "validation_passed": validation.get("passed") is True,
            "validation_issues": "; ".join(issues),
            "resume_pdf": str(package / "resume.pdf"),
            "cover_letter": str(package / "cover-letter.md"),
            "package_path": str(package),
        }
        packages.append(item)
        if item["validation_passed"] and not manifest_issues:
            ready_packages.append(item)

    ready_packages.sort(
        key=lambda item: (
            0 if item["category"] == "apply" else 1,
            -int(item["avg_match"] or 0),
            -int(item["red_team_match"] or 0),
            item["company"].casefold(),
        )
    )
    packages.sort(
        key=lambda item: (
            not item["validation_passed"],
            0 if item["category"] == "apply" else 1,
            item["company"].casefold(),
        )
    )

    write_csv(
        EXPORT_DIR / "remaining-queue.csv",
        remaining,
        [
            "job_id",
            "company",
            "title",
            "source_url",
            "stage",
            "last_error",
            "current_path",
        ],
    )
    ready_fields = [
        "job_id",
        "company",
        "title",
        "category",
        "source_url",
        "avg_match",
        "red_team_match",
        "validation_passed",
        "resume_pdf",
        "cover_letter",
        "package_path",
    ]
    write_csv(
        EXPORT_DIR / "ready-packages.csv",
        [{key: item[key] for key in ready_fields} for item in ready_packages],
        ready_fields,
    )
    write_csv(
        EXPORT_DIR / "review-packages.csv",
        packages,
        ready_fields[:8] + ["validation_issues"] + ready_fields[8:],
    )

    package_validation_counts = Counter(
        "passed" if item["validation_passed"] else "failed" for item in packages
    )
    status = {
        "generated_at": now_iso(),
        "search": previous.get("search", {}),
        "import": previous.get("import", {}),
        "pipeline": {
            "database_counts": dict(sorted(database_counts.items())),
            "corrected_run_summary": corrected.get("summary", {}),
            "corrected_run_stopped_reason": corrected.get("stopped_reason", ""),
            "ready_package_count": len(ready_packages),
            "ready_packages": ready_packages,
            "application_package_counts": {
                "total": len(packages),
                **dict(sorted(package_validation_counts.items())),
            },
            "remaining_queue_count": len(remaining),
            "remaining_stage_counts": dict(sorted(stage_counts.items())),
            "missing_skills": {
                "rows": int(missing_skill_counts["rows"]),
                "jobs": int(missing_skill_counts["jobs"]),
            },
            "manifest_errors": manifest_errors,
        },
        "blocker": {
            "type": "codex-chatgpt-usage-limit",
            "message": (
                "You've hit your usage limit. Try again at "
                f"{RESET_TIME}."
            ),
            "reset_time_as_displayed": RESET_TIME,
            "timezone_note": "The Codex CLI message did not include a timezone.",
            "resume_command": RESUME_COMMAND,
        },
        "remaining_queue": remaining,
    }
    STATUS_PATH.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary_lines = [
        "# Vacancy pipeline checkpoint",
        "",
        f"Generated: {status['generated_at']}",
        "",
        "## Current state",
        "",
        f"- Database statuses: {dict(sorted(database_counts.items()))}",
        f"- Remaining queue: {len(remaining)}",
        f"- Ready packages: {len(ready_packages)}",
        (
            "- Packages requiring review or repair: "
            f"{package_validation_counts.get('failed', 0)}"
        ),
        (
            "- Missing-skills rows: "
            f"{missing_skill_counts['rows']} across {missing_skill_counts['jobs']} jobs"
        ),
        f"- Manifest errors: {len(manifest_errors)}",
        "",
        "## Blocker",
        "",
        (
            "Codex ChatGPT usage limit. The CLI displayed a retry time of "
            f"{RESET_TIME}; no timezone was shown."
        ),
        "",
        "## Resume",
        "",
        f"`{RESUME_COMMAND}`",
        "",
        "No application was submitted, uploaded, or sent.",
    ]
    (EXPORT_DIR / "pipeline-run-summary.md").write_text(
        "\n".join(summary_lines) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "database_counts": dict(sorted(database_counts.items())),
                "remaining_queue": len(remaining),
                "ready_packages": len(ready_packages),
                "package_validation": dict(sorted(package_validation_counts.items())),
                "missing_skills": {
                    "rows": int(missing_skill_counts["rows"]),
                    "jobs": int(missing_skill_counts["jobs"]),
                },
                "manifest_errors": len(manifest_errors),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
