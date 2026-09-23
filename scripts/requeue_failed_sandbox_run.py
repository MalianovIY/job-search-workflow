#!/usr/bin/env python3
"""Requeue jobs whose nested Codex run could not read workspace files."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "state/jobs.sqlite"
QUEUE = ROOT / "workspace/queue"
ACCESS_FAILURE_MARKERS = (
    "sandbox_apply: Operation not permitted",
    "session cannot read local files",
    "unable to read the vacancy file",
    "could not access `vacancy.json`",
    "workspace read sandbox is failing",
    "this session cannot read local files",
)
EVALUATION_OUTPUTS = (
    "avg-match.json",
    "red-team-match.json",
    "decision.json",
    "manifest.json",
)
MATERIAL_VALIDATION_OUTPUTS = (
    "manifest.json",
    "markdown-validation.json",
    "model-validation.json",
    "public-disclosure-validation.json",
    "render-report.json",
    "resume-preview.png",
    "resume-theme.json",
    "validation-summary.json",
    "visual-validation.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--extra-job-id", action="append", default=[])
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def has_access_failure(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace").casefold()
    return any(marker.casefold() in text for marker in ACCESS_FAILURE_MARKERS)


def remove_paths(job_dir: Path, names: tuple[str, ...]) -> None:
    for name in names:
        path = job_dir / name
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def move_to_queue(source: Path, job_id: str) -> Path:
    target = QUEUE / job_id
    if target.exists():
        raise FileExistsError(f"Queue target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def update_job(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    target: Path,
) -> None:
    updated = connection.execute(
        """
        UPDATE jobs
        SET status = 'queued', current_path = ?, updated_at = ?
        WHERE job_id = ?
        """,
        (
            str(target.resolve()),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            job_id,
        ),
    )
    if updated.rowcount != 1:
        raise RuntimeError(f"Could not update database row for {job_id}")


def clean_resumed_job(job_dir: Path) -> bool:
    invalid_materials = any(
        has_access_failure(job_dir / name)
        for name in ("resume.md", "cover-letter.md")
    )
    remove_paths(job_dir, MATERIAL_VALIDATION_OUTPUTS)
    if invalid_materials:
        remove_paths(
            job_dir,
            (
                "resume.md",
                "resume.pdf",
                "cover-letter.md",
                "resume-revision-1.md",
                "resume-revision-2.md",
                "resume-revision-3.md",
            ),
        )
    return invalid_materials


def main() -> int:
    args = parse_args()
    report = load_json(args.report.resolve())
    entries: dict[str, dict[str, Any]] = {}
    for batch in report.get("batches", []):
        for result in batch.get("results", []):
            entries[result["job_id"]] = result

    summary = {
        "semantic_requeued": 0,
        "resumed_requeued": 0,
        "invalid_material_sets_removed": 0,
    }
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        for job_id, result in entries.items():
            source = Path(result["result"])
            if not source.is_dir():
                raise FileNotFoundError(f"Result directory is missing: {source}")
            target = move_to_queue(source, job_id)
            if result.get("resumed"):
                if clean_resumed_job(target):
                    summary["invalid_material_sets_removed"] += 1
                summary["resumed_requeued"] += 1
            else:
                remove_paths(target, EVALUATION_OUTPUTS)
                summary["semantic_requeued"] += 1
            update_job(connection, job_id=job_id, target=target)

        for job_id in args.extra_job_id:
            row = connection.execute(
                "SELECT current_path FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"Unknown separate resumed job: {job_id}")
            source = Path(row["current_path"])
            if not source.is_dir():
                raise FileNotFoundError(f"Result directory is missing: {source}")
            target = move_to_queue(source, job_id)
            if clean_resumed_job(target):
                summary["invalid_material_sets_removed"] += 1
            summary["resumed_requeued"] += 1
            update_job(connection, job_id=job_id, target=target)

        connection.commit()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
