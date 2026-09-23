#!/usr/bin/env python3
"""Render every resume Markdown under results/ and print fit metrics."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results"
DEFAULT_THEME = ROOT / "md-renderer/themes/default.json"
DEFAULT_OUTPUT = ROOT / "tmp/pdfs/results-rerender"
RESUME_NAME = re.compile(r"^resume(?:-revision-\d+)?\.md$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--theme", type=Path, default=DEFAULT_THEME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Write PDFs next to their Markdown sources. This replaces resume.pdf "
            "and creates/replaces resume-revision-N.pdf files."
        ),
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "chromium", "reportlab"),
        default="auto",
        help="auto tries Chromium first, then ReportLab fallback.",
    )
    parser.add_argument(
        "--minimum-fill",
        type=float,
        default=97.0,
        help="Passed through to inspect_pdf.py.",
    )
    parser.add_argument(
        "--fail-on-render-error",
        action="store_true",
        help="Return non-zero if any render or inspection fails.",
    )
    return parser.parse_args()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def find_resumes(results_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in results_dir.rglob("resume*.md")
        if path.is_file() and RESUME_NAME.fullmatch(path.name)
    )


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, env=env)


def reportlab_python() -> str:
    candidates = [
        os.environ.get("JOB_SEARCH_REPORTLAB_PYTHON"),
        sys.executable,
        shutil.which("python3"),
    ]
    selected = next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
        None,
    )
    if not selected:
        raise RuntimeError("Could not locate a ReportLab Python runtime.")
    return selected


def render_with_engine(markdown: Path, pdf: Path, theme: Path, engine: str) -> dict[str, Any]:
    chromium_command = [
        sys.executable,
        str(ROOT / "md-renderer/render_pdf.py"),
        str(markdown),
        "--config",
        str(theme),
        "--output",
        str(pdf),
    ]
    if engine in {"auto", "chromium"}:
        result = run(chromium_command)
        if result.returncode == 0:
            return {"used": "chromium"}
        if engine == "chromium":
            return {
                "error": result.stderr.strip() or result.stdout.strip() or "Chromium render failed.",
                "used": "chromium",
            }
        browser_error = result.stderr.strip() or result.stdout.strip()
    else:
        browser_error = ""

    rl_command = [
        reportlab_python(),
        str(ROOT / "md-renderer/render_pdf_reportlab.py"),
        str(markdown),
        "--config",
        str(theme),
        "--output",
        str(pdf),
    ]
    result = run(rl_command)
    if result.returncode == 0:
        info: dict[str, Any] = {"used": "reportlab"}
        if browser_error:
            info["browser_error"] = browser_error
        return info
    detail = result.stderr.strip() or result.stdout.strip() or "ReportLab render failed."
    if browser_error:
        detail = f"Chromium render failed: {browser_error}; ReportLab render failed: {detail}"
    return {"error": detail, "used": "reportlab"}


def inspect_pdf(pdf: Path, theme: Path, metrics_path: Path, minimum_fill: float) -> dict[str, Any]:
    result = run(
        [
            sys.executable,
            str(ROOT / "scripts/inspect_pdf.py"),
            str(pdf),
            "--theme",
            str(theme),
            "--minimum-fill",
            str(minimum_fill),
            "--output",
            str(metrics_path),
        ]
    )
    if metrics_path.exists():
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    return {"error": result.stderr.strip() or result.stdout.strip() or "Inspection failed."}


def table(rows: list[dict[str, Any]]) -> str:
    headers = ["file", "pass", "pages", "fill", "free", "overflow"]
    rendered = [
        [
            row["file"],
            row["pass"],
            row["pages"],
            row["fill"],
            row["free"],
            row["overflow"],
        ]
        for row in rows
    ]
    widths = [
        max(len(header), *(len(str(row[index])) for row in rendered))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for row in rendered:
        lines.append(
            "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    results_dir = args.results_dir.resolve()
    output_dir = args.output_dir.resolve()
    theme = args.theme.resolve()
    rows: list[dict[str, Any]] = []
    failed = False

    for markdown in find_resumes(results_dir):
        rel = markdown.resolve().relative_to(results_dir)
        target_dir = markdown.parent if args.in_place else output_dir / rel.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        if args.in_place:
            pdf = target_dir / f"{markdown.stem}.pdf"
            metrics_path = target_dir / f"{markdown.stem}.metrics.json"
        else:
            suffix = args.engine if args.engine != "auto" else "auto"
            pdf = target_dir / f"{markdown.stem}.{suffix}.pdf"
            metrics_path = target_dir / f"{markdown.stem}.{suffix}.metrics.json"

        render_info = render_with_engine(markdown, pdf, theme, args.engine)
        if "error" in render_info:
            failed = True
            rows.append(
                {
                    "file": relative(markdown),
                    "pass": "render-error",
                    "pages": "-",
                    "fill": "-",
                    "free": "-",
                    "overflow": "-",
                }
            )
            continue

        metrics = inspect_pdf(pdf, theme, metrics_path, args.minimum_fill)
        if "error" in metrics:
            failed = True
            rows.append(
                {
                    "file": relative(markdown),
                    "pass": "inspect-error",
                    "pages": "-",
                    "fill": "-",
                    "free": "-",
                    "overflow": "-",
                }
            )
            continue

        failed = failed or not bool(metrics["passes"])
        rows.append(
            {
                "file": relative(markdown),
                "pass": "yes" if metrics["passes"] else "no",
                "pages": metrics["page_count"],
                "fill": f"{metrics['fill_percent']:.2f}%",
                "free": f"{metrics['free_percent']:.2f}%",
                "overflow": metrics["overflow_visual_lines"],
            }
        )

    print(table(rows))
    target_label = results_dir if args.in_place else output_dir
    print(f"\nRendered files: {target_label}")
    if args.fail_on_render_error:
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
