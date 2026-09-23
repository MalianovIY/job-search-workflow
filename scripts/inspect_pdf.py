#!/usr/bin/env python3
"""Measure page count, visible lines, bounds, and working-height fill of a PDF."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import pdfplumber
except ImportError as error:  # pragma: no cover - environment diagnostic
    raise SystemExit(
        "pdfplumber is required. Install md-renderer/requirements.txt"
    ) from error


CSS_LENGTH = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?|\.\d+)(?P<unit>px|in|cm|mm|pt|pc)$"
)
LABELED_SKILL_LINE = re.compile(r"^[^:]{2,60}:\s+\S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--theme", type=Path, required=True)
    parser.add_argument("--minimum-fill", type=float, default=97.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_theme(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    path = path.resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError(f"Circular theme inheritance: {path}")
    seen.add(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    parent = config.pop("extends", None)
    if parent:
        return deep_merge(read_theme(path.parent / parent, seen), config)
    return config


def css_points(value: Any) -> float:
    if isinstance(value, (int, float)) and value == 0:
        return 0.0
    if not isinstance(value, str):
        raise ValueError(f"Unsupported CSS length: {value!r}")
    match = CSS_LENGTH.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported CSS length: {value!r}")
    number = float(match.group("value"))
    unit = match.group("unit")
    factors = {
        "pt": 1.0,
        "in": 72.0,
        "cm": 72.0 / 2.54,
        "mm": 72.0 / 25.4,
        "px": 72.0 / 96.0,
        "pc": 12.0,
    }
    return number * factors[unit]


def margins_from_theme(theme: dict[str, Any]) -> dict[str, float]:
    value = theme["page"].get("margins", "0")
    if isinstance(value, dict):
        common = value.get("all", "0")
        return {
            side: css_points(value.get(side, common))
            for side in ("top", "right", "bottom", "left")
        }
    points = css_points(value)
    return dict.fromkeys(("top", "right", "bottom", "left"), points)


def visible_objects(page: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = list(page.chars)
    for collection_name in ("lines", "curves", "images"):
        for item in getattr(page, collection_name, []) or []:
            if "top" in item and "bottom" in item:
                objects.append(item)
    # Chromium emits large white rectangles for page/body backgrounds. They
    # are not resume content and must not make an underfilled page appear full.
    # Keep only thin rectangles such as section rules or vertical separators.
    for item in getattr(page, "rects", []) or []:
        if "top" not in item or "bottom" not in item:
            continue
        width = float(item.get("width", float(item.get("x1", 0)) - float(item.get("x0", 0))))
        height = float(
            item.get("height", float(item["bottom"]) - float(item["top"]))
        )
        if width <= 5.0 or height <= 5.0:
            objects.append(item)
    return objects


def count_visual_lines(page: Any, tolerance: float = 2.5) -> int:
    chars = [char for char in page.chars if str(char.get("text", "")).strip()]
    if not chars:
        return 0
    tops = sorted(float(char["top"]) for char in chars)
    groups: list[float] = []
    for top in tops:
        if not groups or abs(top - groups[-1]) > tolerance:
            groups.append(top)
        else:
            groups[-1] = (groups[-1] + top) / 2
    return len(groups)


def visual_text_lines(page: Any, tolerance: float = 2.5) -> list[dict[str, Any]]:
    words = page.extract_words(
        use_text_flow=False,
        keep_blank_chars=False,
        extra_attrs=["fontname"],
    )
    lines: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if not lines or abs(top - float(lines[-1]["top"])) > tolerance:
            lines.append({"top": top, "words": [word]})
        else:
            lines[-1]["words"].append(word)
            lines[-1]["top"] = (
                float(lines[-1]["top"]) + top
            ) / 2
    for line in lines:
        line["words"].sort(key=lambda item: float(item["x0"]))
        line["text"] = " ".join(str(word["text"]) for word in line["words"])
    return lines


def inspect_core_skills(page: Any) -> dict[str, Any]:
    lines = visual_text_lines(page)
    core_index = next(
        (
            index
            for index, line in enumerate(lines)
            if str(line["text"]).strip().upper() == "CORE SKILLS"
        ),
        None,
    )
    experience_index = next(
        (
            index
            for index, line in enumerate(lines)
            if str(line["text"]).strip().upper() == "PROFESSIONAL EXPERIENCE"
        ),
        None,
    )
    if (
        core_index is None
        or experience_index is None
        or experience_index <= core_index
    ):
        return {
            "found": False,
            "visual_line_count": 0,
            "lines": [],
            "invalid_lines": ["Could not locate the rendered CORE SKILLS section."],
            "passes": False,
        }

    rendered = lines[core_index + 1 : experience_index]
    rendered_text = [str(line["text"]).strip() for line in rendered]
    invalid_lines = []
    for line, text in zip(rendered, rendered_text):
        first_word = line["words"][0] if line["words"] else {}
        first_font = str(first_word.get("fontname", "")).lower()
        if not LABELED_SKILL_LINE.match(text) or "bold" not in first_font:
            invalid_lines.append(text)

    return {
        "found": True,
        "visual_line_count": len(rendered_text),
        "lines": rendered_text,
        "invalid_lines": invalid_lines,
        "passes": bool(rendered_text) and not invalid_lines,
    }


def inspect(pdf_path: Path, theme_path: Path, minimum_fill: float) -> dict[str, Any]:
    theme = read_theme(theme_path)
    margins = margins_from_theme(theme)
    with pdfplumber.open(pdf_path) as document:
        if not document.pages:
            raise ValueError("PDF contains no pages")

        core_skills = inspect_core_skills(document.pages[0])
        page_metrics = []
        total_out_of_bounds = 0
        for index, page in enumerate(document.pages, start=1):
            objects = visible_objects(page)
            content_bottom = max(
                (float(item["bottom"]) for item in objects),
                default=margins["top"],
            )
            work_bottom = float(page.height) - margins["bottom"]
            work_right = float(page.width) - margins["right"]
            work_height = float(page.height) - margins["top"] - margins["bottom"]
            free_height = max(0.0, work_bottom - content_bottom)
            free_percent = (free_height / work_height * 100) if work_height else 100.0

            out_of_bounds = 0
            for item in objects:
                top = float(item.get("top", margins["top"]))
                bottom = float(item.get("bottom", top))
                x0 = float(item.get("x0", margins["left"]))
                x1 = float(item.get("x1", x0))
                if (
                    top < margins["top"] - 1.5
                    or bottom > work_bottom + 1.5
                    or x0 < margins["left"] - 1.5
                    or x1 > work_right + 1.5
                ):
                    out_of_bounds += 1
            total_out_of_bounds += out_of_bounds
            page_metrics.append(
                {
                    "page": index,
                    "width_pt": float(page.width),
                    "height_pt": float(page.height),
                    "visual_lines": count_visual_lines(page),
                    "content_bottom_pt": content_bottom,
                    "work_bottom_pt": work_bottom,
                    "free_height_pt": free_height,
                    "free_percent": free_percent,
                    "fill_percent": 100.0 - free_percent,
                    "out_of_bounds_objects": out_of_bounds,
                }
            )

    first = page_metrics[0]
    page_count = len(page_metrics)
    result = {
        "pdf": str(pdf_path.resolve()),
        "theme": str(theme_path.resolve()),
        "page_count": page_count,
        "margins_pt": margins,
        "visual_lines_first_page": first["visual_lines"],
        "overflow_visual_lines": sum(
            page["visual_lines"] for page in page_metrics[1:]
        ),
        "free_percent": first["free_percent"],
        "fill_percent": first["fill_percent"],
        "out_of_bounds_objects": total_out_of_bounds,
        "core_skills": core_skills,
        "pages": page_metrics,
        "passes": (
            page_count == 1
            and first["fill_percent"] >= minimum_fill
            and total_out_of_bounds == 0
            and core_skills["passes"]
        ),
    }
    return result


def main() -> int:
    args = parse_args()
    try:
        result = inspect(args.pdf.resolve(), args.theme.resolve(), args.minimum_fill)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
