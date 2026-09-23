#!/usr/bin/env python3
"""Render a resume and deterministically expand spacing toward a one-page fit."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from inspect_pdf import deep_merge, inspect, read_theme


NUMBER_WITH_UNIT = re.compile(r"^(?P<number>\d+(?:\.\d+)?)(?P<unit>em|px)$")
PARAMETERS = [
    (("li", "lineHeight"),),
    (("paragraph", "lineHeight"),),
    (("li", "marginTop"), ("li", "marginBottom")),
    (("h2", "marginTop"), ("h2", "marginBottom")),
    (("h3", "marginTop"), ("h3", "marginBottom")),
    (("paragraph", "marginTop"), ("paragraph", "marginBottom")),
    (("h1", "marginTop"), ("h1", "marginBottom")),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--base-theme", type=Path, required=True)
    parser.add_argument("--working-theme", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-fill", type=float, default=97.0)
    parser.add_argument("--maximum-spacing-iterations", type=int, default=80)
    return parser.parse_args()


def render(markdown: Path, theme: Path, pdf: Path) -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    renderer = os.environ.get("JOB_SEARCH_PDF_RENDERER", "auto")
    browser_error = ""
    info = {"requested": renderer}
    if renderer not in {"auto", "reportlab"}:
        raise RuntimeError("JOB_SEARCH_PDF_RENDERER must be auto or reportlab.")
    if renderer == "auto":
        command = [
            sys.executable,
            str(root / "md-renderer/render_pdf.py"),
            str(markdown),
            "--config",
            str(theme),
            "--output",
            str(pdf),
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if not result.returncode:
            info["used"] = "chromium"
            return info
        browser_error = result.stderr.strip() or result.stdout.strip()
        info["browser_error"] = browser_error

    configured_python = os.environ.get("JOB_SEARCH_REPORTLAB_PYTHON")
    candidates = [
        configured_python,
        sys.executable,
        shutil.which("python3"),
    ]
    reportlab_python = next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
        None,
    )
    if not reportlab_python:
        raise RuntimeError(browser_error or "Could not locate a ReportLab Python runtime.")
    fallback = subprocess.run(
        [
            reportlab_python,
            str(root / "md-renderer/render_pdf_reportlab.py"),
            str(markdown),
            "--config",
            str(theme),
            "--output",
            str(pdf),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if fallback.returncode:
        detail = fallback.stderr.strip() or fallback.stdout.strip()
        if browser_error:
            detail = f"Chromium renderer failed: {browser_error}\nReportLab failed: {detail}"
        raise RuntimeError(detail)
    info["used"] = "reportlab"
    return info


def increment(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value) + 0.01, 4)
    if not isinstance(value, str):
        raise ValueError(f"Cannot increment spacing value: {value!r}")
    stripped = value.strip()
    if stripped == "0":
        return "0.05em"
    match = NUMBER_WITH_UNIT.fullmatch(stripped)
    if not match:
        raise ValueError(f"Unsupported spacing value: {value!r}")
    number = float(match.group("number"))
    unit = match.group("unit")
    step = 0.05 if unit == "em" else 2.0
    result = number + step
    rendered = f"{result:.4f}".rstrip("0").rstrip(".")
    return rendered + unit


def get_value(theme: dict[str, Any], path: tuple[str, str]) -> Any:
    return theme["styles"][path[0]][path[1]]


def set_value(theme: dict[str, Any], path: tuple[str, str], value: Any) -> None:
    theme["styles"].setdefault(path[0], {})[path[1]] = value


def starting_index(free_percent: float) -> int:
    if free_percent >= 13:
        return 0
    if free_percent >= 11:
        return 1
    if free_percent >= 9:
        return 2
    if free_percent >= 7:
        return 3
    if free_percent >= 5:
        return 4
    if free_percent >= 4:
        return 5
    return 6


def write_theme(path: Path, theme: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(theme, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    markdown = args.markdown.resolve()
    pdf = args.pdf.resolve()
    base_theme_path = args.base_theme.resolve()
    working_theme = args.working_theme.resolve()
    report_path = args.report.resolve()
    history: list[dict[str, Any]] = []

    try:
        base_theme = read_theme(base_theme_path)
        write_theme(working_theme, base_theme)
        render_info = render(markdown, working_theme, pdf)
        metrics = inspect(pdf, working_theme, args.minimum_fill)
        history.append({"action": "base-render", "renderer": render_info, "metrics": metrics})

        if metrics["page_count"] > 1:
            status = "needs-shorten"
        elif not metrics["core_skills"]["passes"]:
            status = "needs-core-skills-rewrite"
        elif metrics["passes"]:
            status = "pass"
        elif metrics["free_percent"] > 15:
            status = "needs-expand"
        elif metrics["free_percent"] <= 3:
            status = "failed-bounds"
        else:
            status = "spacing"
            theme = copy.deepcopy(base_theme)
            iteration = 0
            for paths in PARAMETERS[starting_index(metrics["free_percent"]):]:
                while iteration < args.maximum_spacing_iterations:
                    iteration += 1
                    previous = copy.deepcopy(theme)
                    changes = []
                    for path in paths:
                        old = get_value(theme, path)
                        new = increment(old)
                        set_value(theme, path, new)
                        changes.append({"path": ".".join(path), "old": old, "new": new})
                    write_theme(working_theme, theme)
                    render_info = render(markdown, working_theme, pdf)
                    candidate = inspect(pdf, working_theme, args.minimum_fill)
                    history.append(
                        {
                            "action": "spacing",
                            "iteration": iteration,
                            "changes": changes,
                            "renderer": render_info,
                            "metrics": candidate,
                        }
                    )
                    if candidate["passes"]:
                        metrics = candidate
                        status = "pass"
                        break
                    if candidate["page_count"] > 1:
                        theme = previous
                        write_theme(working_theme, theme)
                        render_info = render(markdown, working_theme, pdf)
                        metrics = inspect(pdf, working_theme, args.minimum_fill)
                        history.append(
                            {
                                "action": "rollback",
                                "iteration": iteration,
                                "renderer": render_info,
                                "metrics": metrics,
                            }
                        )
                        break
                    metrics = candidate
                if status == "pass" or iteration >= args.maximum_spacing_iterations:
                    break
            if status != "pass":
                status = "needs-precise-expand"

        report = {
            "status": status,
            "markdown": str(markdown),
            "pdf": str(pdf),
            "base_theme": str(base_theme_path),
            "working_theme": str(working_theme),
            "renderer": render_info,
            "metrics": metrics,
            "history": history,
        }
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        report = {"status": "error", "error": str(error), "history": history}

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
