#!/usr/bin/env python3
"""Run semantic queue batches concurrently and persist a resumable report."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "state/jobs.sqlite"
PIPELINE = ROOT / "scripts/pipeline.py"


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


def queued_job_ids(limit: int | None, job_ids_file: Path | None = None) -> list[str]:
    connection = sqlite3.connect(DB_PATH)
    try:
        if job_ids_file is not None:
            requested = [
                line.strip()
                for line in job_ids_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not requested:
                return []
            placeholders = ", ".join("?" for _ in requested)
            queued = {
                row[0]
                for row in connection.execute(
                    f"SELECT job_id FROM jobs WHERE status = 'queued' "
                    f"AND job_id IN ({placeholders})",
                    requested,
                )
            }
            selected = [job_id for job_id in requested if job_id in queued]
            return selected[:limit] if limit is not None else selected
        query = (
            "SELECT job_id FROM jobs WHERE status = 'queued' "
            "ORDER BY created_at"
        )
        parameters: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        return [row[0] for row in connection.execute(query, parameters)]
    finally:
        connection.close()


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def run_batch(index: int, job_ids: list[str], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            str(PIPELINE),
            "process-batch",
            *job_ids,
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    duration = round(time.monotonic() - started, 2)
    parsed: list[dict[str, Any]] | None = None
    parse_error = ""
    if result.stdout.strip():
        try:
            value = json.loads(result.stdout)
            if isinstance(value, list):
                parsed = value
            else:
                parse_error = "Pipeline output was not a JSON array."
        except json.JSONDecodeError as error:
            parse_error = str(error)
    return {
        "batch_index": index,
        "job_ids": job_ids,
        "returncode": result.returncode,
        "duration_seconds": duration,
        "results": parsed,
        "parse_error": parse_error,
        "stdout": result.stdout if parsed is None else "",
        "stderr": result.stderr,
        "completed_at": now_iso(),
    }


def summarize(batch_results: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    completed_jobs = 0
    job_errors = 0
    failed_batches = 0
    for batch in batch_results:
        values = batch.get("results")
        if batch.get("returncode") != 0 or values is None:
            failed_batches += 1
        if values is None:
            job_errors += len(batch["job_ids"])
            continue
        for item in values:
            if "error" in item:
                job_errors += 1
                continue
            completed_jobs += 1
            category = str(item.get("final_category") or "unknown")
            categories[category] = categories.get(category, 0) + 1
    return {
        "completed_jobs": completed_jobs,
        "job_errors": job_errors,
        "failed_batches": failed_batches,
        "categories": categories,
    }


def contains_usage_limit(batch_result: dict[str, Any]) -> bool:
    text = json.dumps(batch_result, ensure_ascii=False).casefold()
    return "usage limit" in text or "try again at" in text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--job-ids-file", type=Path)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    workers = max(1, args.workers)
    batch_size = max(1, args.batch_size)
    selected = queued_job_ids(args.limit, args.job_ids_file)
    job_batches = chunks(selected, batch_size)
    report: dict[str, Any] = {
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "workers": workers,
        "batch_size": batch_size,
        "job_ids_file": (
            str(args.job_ids_file.resolve()) if args.job_ids_file is not None else None
        ),
        "selected_jobs": len(selected),
        "total_batches": len(job_batches),
        "batches": [],
        "deferred_batches": [],
        "summary": {},
    }
    write_json_atomic(args.report, report)
    if not job_batches:
        report["completed_at"] = now_iso()
        report["summary"] = summarize([])
        write_json_atomic(args.report, report)
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0

    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Future[dict[str, Any]], int] = {
        executor.submit(run_batch, index, job_ids, args.timeout): index
        for index, job_ids in enumerate(job_batches, start=1)
    }
    completed = 0
    usage_limit_hit = False
    try:
        for future in as_completed(futures):
            index = futures[future]
            if future.cancelled():
                report["deferred_batches"].append(
                    {
                        "batch_index": index,
                        "job_ids": job_batches[index - 1],
                        "reason": "Deferred after Codex usage limit was detected.",
                    }
                )
                report["deferred_batches"].sort(
                    key=lambda item: item["batch_index"]
                )
                report["updated_at"] = now_iso()
                write_json_atomic(args.report, report)
                continue
            completed += 1
            try:
                batch_result = future.result()
            except Exception as error:
                batch_result = {
                    "batch_index": index,
                    "job_ids": job_batches[index - 1],
                    "returncode": -1,
                    "duration_seconds": 0,
                    "results": None,
                    "parse_error": "",
                    "stdout": "",
                    "stderr": str(error),
                    "completed_at": now_iso(),
                }
            report["batches"].append(batch_result)
            report["batches"].sort(key=lambda item: item["batch_index"])
            report["updated_at"] = now_iso()
            report["summary"] = summarize(report["batches"])
            if contains_usage_limit(batch_result) and not usage_limit_hit:
                usage_limit_hit = True
                report["stopped_reason"] = "Codex usage limit"
                for pending in futures:
                    if pending is not future:
                        pending.cancel()
            write_json_atomic(args.report, report)
            batch_categories = summarize([batch_result])["categories"]
            print(
                f"[{completed}/{len(job_batches)}] batch {index}: "
                f"returncode={batch_result['returncode']} "
                f"duration={batch_result['duration_seconds']}s "
                f"categories={json.dumps(batch_categories, ensure_ascii=False)}",
                flush=True,
            )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    report["completed_at"] = now_iso()
    report["summary"] = summarize(report["batches"])
    write_json_atomic(args.report, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed_batches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
