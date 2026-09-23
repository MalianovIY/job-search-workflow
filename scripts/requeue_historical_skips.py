#!/usr/bin/env python3
"""Archive every completed skip package and queue a clean copy for reprocessing."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "state/jobs.sqlite"
QUEUE = ROOT / "workspace/queue"
RESULTS_SKIP = ROOT / "results/skip"
GENERATED_OUTPUTS = (
    "vacancy.json",
    "avg-match.json",
    "red-team-match.json",
    "decision.json",
    "manifest.json",
    "resume.md",
    "resume.pdf",
    "cover-letter.md",
    "resume-preview.png",
    "resume-theme.json",
    "markdown-validation.json",
    "model-validation.json",
    "public-disclosure-validation.json",
    "render-report.json",
    "validation-summary.json",
    "visual-validation.json",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def remove_generated_outputs(job_dir: Path) -> list[str]:
    removed: list[str] = []
    for name in GENERATED_OUTPUTS:
        path = job_dir / name
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(name)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(name)
    logs = job_dir / "logs"
    if logs.is_dir():
        shutil.rmtree(logs)
        removed.append("logs")
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--job-ids-output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_root = args.archive_root.resolve()
    job_ids_output = args.job_ids_output.resolve()
    manifest_path = args.manifest.resolve()

    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'skip'
            ORDER BY created_at, job_id
            """
        ).fetchall()

        planned: list[dict[str, Any]] = []
        seen_sources: set[Path] = set()
        for row in rows:
            source = Path(row["current_path"]).resolve()
            if source in seen_sources:
                raise RuntimeError(f"Duplicate result path in database: {source}")
            seen_sources.add(source)
            if not source.is_dir():
                raise FileNotFoundError(f"Result directory is missing: {source}")
            try:
                relative_source = source.relative_to(ROOT)
                source.relative_to(RESULTS_SKIP)
            except ValueError as error:
                raise RuntimeError(
                    f"Skip package is outside results/skip: {source}"
                ) from error

            target = (QUEUE / row["job_id"]).resolve()
            archived = archive_root / relative_source
            if target.exists():
                raise FileExistsError(f"Queue target already exists: {target}")
            if archived.exists():
                raise FileExistsError(f"Archive target already exists: {archived}")
            planned.append(
                {
                    "job_id": row["job_id"],
                    "company": row["company"],
                    "title": row["title"],
                    "source_url": row["source_url"],
                    "source": str(source),
                    "archive": str(archived),
                    "queue": str(target),
                }
            )

        manifest: dict[str, Any] = {
            "created_at": now_iso(),
            "dry_run": args.dry_run,
            "database": str(DATABASE),
            "job_count": len(planned),
            "jobs": planned,
        }
        if args.dry_run:
            print(json.dumps({"planned_jobs": len(planned)}, ensure_ascii=False))
            return 0

        completed: list[dict[str, Any]] = []
        try:
            for item in planned:
                source = Path(item["source"])
                archived = Path(item["archive"])
                target = Path(item["queue"])
                archived.parent.mkdir(parents=True, exist_ok=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(archived))
                try:
                    shutil.copytree(archived, target)
                    item["removed_from_queue_copy"] = remove_generated_outputs(target)
                except Exception:
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.move(str(archived), str(source))
                    raise
                completed.append(item)

            timestamp = now_iso()
            job_ids = [item["job_id"] for item in completed]
            connection.executemany(
                """
                UPDATE jobs
                SET status = 'queued', current_path = ?, updated_at = ?
                WHERE job_id = ? AND status = 'skip'
                """,
                [
                    (item["queue"], timestamp, item["job_id"])
                    for item in completed
                ],
            )
            connection.executemany(
                "DELETE FROM missing_skills WHERE job_id = ?",
                [(job_id,) for job_id in job_ids],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            for item in reversed(completed):
                source = Path(item["source"])
                archived = Path(item["archive"])
                target = Path(item["queue"])
                if target.exists():
                    shutil.rmtree(target)
                if archived.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(archived), str(source))
            raise

    manifest["completed_at"] = now_iso()
    write_json_atomic(manifest_path, manifest)
    job_ids_output.parent.mkdir(parents=True, exist_ok=True)
    job_ids_output.write_text(
        "".join(f"{item['job_id']}\n" for item in planned),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "requeued_jobs": len(planned),
                "job_ids_output": str(job_ids_output),
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
