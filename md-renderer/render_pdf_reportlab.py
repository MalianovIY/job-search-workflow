#!/usr/bin/env python3
"""Render the pipeline's constrained resume Markdown with ReportLab."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, inch, mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


LENGTH = re.compile(r"^(?P<number>\d+(?:\.\d+)?|\.\d+)(?P<unit>em|px|pt|in|cm|mm)$")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
INLINE_CODE = re.compile(r"`([^`]+)`")
CSS_PX_TO_PT = 72.0 / 96.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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


def points(value: Any, em_base: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "0":
        return 0.0
    match = LENGTH.fullmatch(text)
    if not match:
        raise ValueError(f"Unsupported length: {value!r}")
    number = float(match.group("number"))
    unit = match.group("unit")
    factors = {
        "em": em_base,
        "px": 72.0 / 96.0,
        "pt": 1.0,
        "in": inch,
        "cm": cm,
        "mm": mm,
    }
    return number * factors[unit]


def line_height(value: Any, font_size: float) -> float:
    if isinstance(value, (int, float)):
        return float(value) * font_size
    return points(value, font_size)


def base_font_size_points(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value) * CSS_PX_TO_PT
    return points(value, 12.0)


def normalize_text(value: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2007": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u2003": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    text = html.unescape(value)
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def inline_markup(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    tokens: list[str] = []

    def stash(markup: str) -> str:
        token = f"@@RL_TOKEN_{len(tokens)}@@"
        tokens.append(markup)
        return token

    def link_replacement(match: re.Match[str]) -> str:
        label = escape(normalize_text(match.group(1)))
        target = escape(normalize_text(match.group(2)), {'"': "&quot;"})
        return stash(f'<link href="{target}" color="#0366D6">{label}</link>')

    text = LINK.sub(link_replacement, text)
    text = escape(text)
    text = BOLD.sub(r"<b>\1</b>", text)
    text = INLINE_CODE.sub(r'<font name="Courier">\1</font>', text)
    text = text.replace("\n", "<br/>")
    for index, markup in enumerate(tokens):
        text = text.replace(f"@@RL_TOKEN_{index}@@", markup)
    return text


def color(value: str, default: colors.Color) -> colors.Color:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{8}", text):
        text = text[:7]
    try:
        return colors.HexColor(text)
    except (TypeError, ValueError):
        return default


def margins(theme: dict[str, Any]) -> dict[str, float]:
    value = theme["page"].get("margins", "0")
    if isinstance(value, dict):
        common = value.get("all", "0")
        return {
            side: points(value.get(side, common), 12.0)
            for side in ("top", "right", "bottom", "left")
        }
    common = points(value, 12.0)
    return dict.fromkeys(("top", "right", "bottom", "left"), common)


def style_value(
    styles: dict[str, Any],
    name: str,
    key: str,
    default: Any,
) -> Any:
    return styles.get(name, {}).get(key, default)


def paragraph_style(
    *,
    name: str,
    font_size: float,
    leading: float,
    text_color: colors.Color,
    alignment: int = TA_LEFT,
    font_name: str = "Times-Roman",
    space_before: float = 0.0,
    space_after: float = 0.0,
    left_indent: float = 0.0,
    first_line_indent: float = 0.0,
    bullet_indent: float = 0.0,
    keep_with_next: bool = False,
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font_name,
        fontSize=font_size,
        leading=leading,
        textColor=text_color,
        alignment=alignment,
        spaceBefore=space_before,
        spaceAfter=space_after,
        leftIndent=left_indent,
        firstLineIndent=first_line_indent,
        bulletIndent=bullet_indent,
        keepWithNext=keep_with_next,
        allowWidows=0,
        allowOrphans=0,
        splitLongWords=1,
        wordWrap="LTR",
        letterSpacing=0,
    )


def build_styles(theme: dict[str, Any]) -> dict[str, ParagraphStyle]:
    styles = theme["styles"]
    general = styles["general"]
    base_size = base_font_size_points(general.get("baseFontSize", 12))
    base_color = color(general.get("color", "#24292E"), colors.HexColor("#24292E"))

    def heading(name: str, fallback_em: float) -> ParagraphStyle:
        spec = styles.get(name, {})
        font_size = points(spec.get("fontSize", f"{fallback_em}em"), base_size)
        border = spec.get("borderBottom", "")
        return paragraph_style(
            name=name,
            font_size=font_size,
            leading=line_height(spec.get("lineHeight", 1.1), font_size),
            text_color=color(spec.get("color", "#000000"), colors.black),
            font_name="Times-Bold",
            space_before=points(spec.get("marginTop", "0"), font_size),
            space_after=(
                0.0
                if border and not str(border).startswith("0")
                else points(spec.get("marginBottom", "0"), font_size)
            ),
            keep_with_next=True,
        )

    paragraph = styles.get("paragraph", {})
    paragraph_leading = line_height(paragraph.get("lineHeight", 1.4), base_size)
    li = styles.get("li", {})
    li_leading = line_height(li.get("lineHeight", 1.1), base_size)
    return {
        "h1": heading("h1", 1.7),
        "h2": heading("h2", 1.3),
        "h3": heading("h3", 1.1),
        "paragraph": paragraph_style(
            name="paragraph",
            font_size=base_size,
            leading=paragraph_leading,
            text_color=base_color,
            space_before=points(paragraph.get("marginTop", "0"), base_size),
            space_after=points(paragraph.get("marginBottom", "0"), base_size),
        ),
        "center": paragraph_style(
            name="center",
            font_size=base_size,
            leading=paragraph_leading,
            text_color=base_color,
            alignment=TA_CENTER,
            space_before=points(paragraph.get("marginTop", "0"), base_size),
            space_after=points(paragraph.get("marginBottom", "0"), base_size),
        ),
        "bullet": paragraph_style(
            name="bullet",
            font_size=base_size,
            leading=li_leading,
            text_color=base_color,
            space_before=points(li.get("marginTop", "0"), base_size),
            space_after=points(li.get("marginBottom", "0"), base_size),
            left_indent=1.5 * base_size,
            first_line_indent=0,
            bullet_indent=0.45 * base_size,
        ),
    }


def page_size(theme: dict[str, Any]) -> tuple[float, float]:
    value = str(theme["page"].get("format", "A4")).upper()
    if value == "A4":
        return A4
    if value == "LETTER":
        return LETTER
    raise ValueError(f"Unsupported page format: {value}")


def parse_story(markdown: str, theme: dict[str, Any]) -> list[Any]:
    styles = build_styles(theme)
    theme_styles = theme["styles"]
    story: list[Any] = []
    centered = False
    paragraph_lines: list[str] = []
    paragraph_centered = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        text = " ".join(part.strip() for part in paragraph_lines)
        selected = styles["center"] if paragraph_centered else styles["paragraph"]
        story.append(Paragraph(inline_markup(text), selected))
        paragraph_lines = []

    def add_heading(level: int, value: str) -> None:
        key = f"h{level}"
        selected = styles[key]
        if centered:
            selected = ParagraphStyle(
                f"{key}-center",
                parent=selected,
                alignment=TA_CENTER,
            )
        story.append(Paragraph(inline_markup(value), selected))
        spec = theme_styles.get(key, {})
        border = spec.get("borderBottom", "")
        if border and not str(border).startswith("0"):
            border_color = colors.black if level == 1 else colors.HexColor("#CCCCCC")
            rule = HRFlowable(
                width="100%",
                thickness=0.75,
                color=border_color,
                spaceBefore=0,
                spaceAfter=points(
                    spec.get("marginBottom", "0"),
                    selected.fontSize,
                ),
            )
            rule.keepWithNext = True
            story.append(rule)

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if re.fullmatch(r"<div\s+style=[\"']text-align:\s*center[\"']>", stripped):
            flush_paragraph()
            centered = True
            continue
        if stripped == "</div>":
            flush_paragraph()
            centered = False
            continue
        if not stripped:
            flush_paragraph()
            continue
        if stripped == "---":
            flush_paragraph()
            story.append(Spacer(1, 3.75))
            continue
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            add_heading(len(heading_match.group(1)), heading_match.group(2))
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            story.append(
                Paragraph(
                    inline_markup(stripped[2:]),
                    styles["bullet"],
                    bulletText="\u2022",
                )
            )
            continue
        if not paragraph_lines:
            paragraph_centered = centered
        paragraph_lines.append(stripped)

    flush_paragraph()
    return story


def render(markdown_path: Path, theme_path: Path, output_path: Path) -> None:
    theme = read_theme(theme_path)
    page_margins = margins(theme)
    general = theme["styles"]["general"]
    base_size = base_font_size_points(general.get("baseFontSize", 12))
    padding = points(general.get("padding", "0"), base_size)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size(theme),
        leftMargin=page_margins["left"] + padding,
        rightMargin=page_margins["right"] + padding,
        topMargin=page_margins["top"] + padding,
        bottomMargin=page_margins["bottom"] + padding,
        title="Resume",
        author="Candidate",
        creator="job-search-workflow ReportLab fallback",
        subject="Tailored resume",
        allowSplitting=1,
    )
    story = parse_story(markdown_path.read_text(encoding="utf-8"), theme)
    document.build(story)


def main() -> int:
    args = parse_args()
    render(args.markdown.resolve(), args.config.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
