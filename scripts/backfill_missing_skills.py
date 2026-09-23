#!/usr/bin/env python3
"""Backfill normalized technical gaps from completed pipeline packages."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from pipeline import load_json, now_iso, select_missing_skills


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "state/jobs.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--historical-manifest",
        type=Path,
        help=(
            "Use archived packages from a requeue manifest for historical skip "
            "jobs that are currently queued."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary: dict[str, Any] = {
        "deleted_rows": 0,
        "eligible_jobs": 0,
        "historical_queued_fallback_jobs": 0,
        "packages_read": 0,
        "jobs_with_selected_skills": 0,
        "selected_skill_rows": 0,
        "inserted_rows": 0,
        "skipped_incomplete_packages": 0,
    }
    required = (
        "metadata.json",
        "vacancy.json",
        "avg-match.json",
        "red-team-match.json",
        "decision.json",
    )
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        existing = connection.execute(
            "SELECT COUNT(*) FROM missing_skills"
        ).fetchone()[0]
        connection.execute("DELETE FROM missing_skills")
        summary["deleted_rows"] = int(existing)
        rows = connection.execute(
            """
            SELECT job_id, status, current_path
            FROM jobs
            WHERE status IN ('skip', 'need-review')
            ORDER BY created_at
            """
        ).fetchall()
        packages = {
            row["job_id"]: Path(row["current_path"])
            for row in rows
        }
        if args.historical_manifest is not None:
            manifest = load_json(args.historical_manifest.resolve())
            queued_ids = {
                row["job_id"]
                for row in connection.execute(
                    "SELECT job_id FROM jobs WHERE status = 'queued'"
                )
            }
            for item in manifest.get("jobs", []):
                job_id = str(item["job_id"])
                if job_id not in queued_ids or job_id in packages:
                    continue
                packages[job_id] = Path(item["archive"])
                summary["historical_queued_fallback_jobs"] += 1

        summary["eligible_jobs"] = len(packages)
        for job_id, job_dir in packages.items():
            if not all((job_dir / name).is_file() for name in required):
                summary["skipped_incomplete_packages"] += 1
                continue
            summary["packages_read"] += 1
            metadata = load_json(job_dir / "metadata.json")
            if metadata["job_id"] != job_id:
                raise RuntimeError(
                    f"Package job_id mismatch: expected {job_id}, "
                    f"found {metadata['job_id']} in {job_dir}"
                )
            skills = select_missing_skills(
                load_json(job_dir / "vacancy.json"),
                load_json(job_dir / "avg-match.json"),
                load_json(job_dir / "red-team-match.json"),
                load_json(job_dir / "decision.json"),
            )
            if not skills:
                continue
            summary["jobs_with_selected_skills"] += 1
            summary["selected_skill_rows"] += len(skills)
            for skill in skills:
                result = connection.execute(
                    """
                    INSERT OR IGNORE INTO missing_skills (
                        job_id, company, title, source_url, skill, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata["job_id"],
                        metadata["company"],
                        metadata["title"],
                        metadata["source_url"],
                        skill,
                        now_iso(),
                    ),
                )
                summary["inserted_rows"] += max(0, result.rowcount)
        connection.commit()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
