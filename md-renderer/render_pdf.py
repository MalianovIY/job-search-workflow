#!/usr/bin/env python3
"""Render Markdown with embedded HTML to a styled PDF."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

import markdown
from playwright.sync_api import sync_playwright


SELECTORS = {
    "general": ".markdown-body",
    "h1": ".markdown-body h1",
    "h2": ".markdown-body h2",
    "h3": ".markdown-body h3",
    "h4": ".markdown-body h4",
    "h5": ".markdown-body h5",
    "h6": ".markdown-body h6",
    "paragraph": ".markdown-body p",
    "blockquote": ".markdown-body blockquote",
    "codeInline": ".markdown-body :not(pre) > code",
    "codeBlock": ".markdown-body pre",
    "listUl": ".markdown-body ul",
    "listOl": ".markdown-body ol",
    "li": ".markdown-body li",
    "link": ".markdown-body a",
    "hr": ".markdown-body hr",
    "table": ".markdown-body table",
    "tableCell": ".markdown-body th, .markdown-body td",
    "img": ".markdown-body img",
}

NAMED_PAGE_FORMATS = {
    "A0", "A1", "A2", "A3", "A4", "A5", "A6",
    "Letter", "Legal", "Tabloid", "Ledger",
}
CSS_LENGTH = re.compile(r"^(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|in|cm|mm|pt|pc))$")
CSS_SIZE = re.compile(
    r"^(?:\d+(?:\.\d+)?|\.\d+)(?:px|in|cm|mm|pt|pc)"
    r"\s+(?:\d+(?:\.\d+)?|\.\d+)(?:px|in|cm|mm|pt|pc)$"
)

BASE_CSS = """
* {
    box-sizing: border-box;
}

html,
body {
    margin: 0;
    padding: 0;
}

body {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

.markdown-body {
    margin: 0 auto;
    overflow-wrap: anywhere;
}

.markdown-body pre {
    overflow-wrap: normal;
    white-space: pre-wrap;
}

.markdown-body img {
    height: auto;
}

.markdown-body table {
    page-break-inside: auto;
}

.markdown-body tr,
.markdown-body img,
.markdown-body pre,
.markdown-body blockquote {
    break-inside: avoid;
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a Markdown file (raw HTML allowed) to PDF."
    )
    parser.add_argument("input", type=Path, help="Input .md file")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path(__file__).with_name("themes") / "default.json",
        help="JSON configuration (default: themes/default.json)",
    )
    parser.add_argument("--output", "-o", type=Path, help="Output .pdf file")
    return parser.parse_args()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_config(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    path = path.resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError(f"Circular theme inheritance detected at: {path}")
    seen.add(path)

    with path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError("The root JSON value must be an object")

    parent_name = config.pop("extends", None)
    if parent_name is not None:
        if not isinstance(parent_name, str):
            raise ValueError("'extends' must be a JSON filename")
        parent = read_config(path.parent / parent_name, seen)
        config = deep_merge(parent, config)

    if not isinstance(config.get("page"), dict):
        raise ValueError("Config must contain a 'page' object")
    if not isinstance(config.get("styles"), dict):
        raise ValueError("Config must contain a 'styles' object")
    return config


def css_page_format(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("'page.format' must be a string")
    normalized = value.strip()
    named = next(
        (item for item in NAMED_PAGE_FORMATS if item.lower() == normalized.lower()),
        None,
    )
    if named:
        return named
    if CSS_SIZE.fullmatch(normalized):
        return normalized
    raise ValueError(
        "Unsupported page format. Use A4, Letter, another common named format, "
        "or a size such as '210mm 297mm'."
    )


def css_length(value: Any, key: str) -> str:
    if isinstance(value, (int, float)) and value == 0:
        return "0"
    if not isinstance(value, str) or not CSS_LENGTH.fullmatch(value.strip()):
        raise ValueError(
            f"'{key}' must be a CSS length such as '0.2in', '10mm', '12px', or 0"
        )
    return value.strip()


def page_margins(value: Any) -> dict[str, str]:
    if isinstance(value, (str, int, float)):
        common = css_length(value, "page.margins")
        return dict.fromkeys(("top", "right", "bottom", "left"), common)
    if not isinstance(value, dict):
        raise ValueError("'page.margins' must be one CSS length or an object")

    common = css_length(value.get("all", "0"), "page.margins.all")
    return {
        side: css_length(value.get(side, common), f"page.margins.{side}")
        for side in ("top", "right", "bottom", "left")
    }


def camel_to_kebab(value: str) -> str:
    return re.sub(r"([A-Z])", r"-\1", value).lower()


def style_value(property_name: str, value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"CSS property '{property_name}' has an invalid value")
    if isinstance(value, (int, float)):
        if property_name == "baseFontSize":
            return f"{value}px"
        if property_name == "fontSize":
            return f"{value}em"
        return str(value)
    return str(value)


def build_style_css(styles: dict[str, Any]) -> str:
    rules: list[str] = []
    general = styles.get("general")
    if isinstance(general, dict) and "backgroundColor" in general:
        background = style_value("backgroundColor", general["backgroundColor"])
        rules.append(f"html, body {{ background-color: {background}; }}")

    for group_name, properties in styles.items():
        if group_name not in SELECTORS:
            raise ValueError(f"Unknown style group: '{group_name}'")
        if not isinstance(properties, dict):
            raise ValueError(f"Style group '{group_name}' must be an object")

        declarations: list[str] = []
        for property_name, raw_value in properties.items():
            if property_name == "textDecoration" and raw_value == "underline hover":
                declarations.append("text-decoration: none")
                continue
            css_property = (
                "font-size"
                if property_name == "baseFontSize"
                else camel_to_kebab(property_name)
            )
            declarations.append(
                f"{css_property}: {style_value(property_name, raw_value)}"
            )

        rules.append(
            f"{SELECTORS[group_name]} {{\n"
            + ";\n".join(f"    {item}" for item in declarations)
            + ";\n}"
        )
        if group_name == "link" and properties.get("textDecoration") == "underline hover":
            rules.append(".markdown-body a:hover { text-decoration: underline; }")
    return "\n\n".join(rules)


def build_html(markdown_path: Path, config: dict[str, Any]) -> str:
    source = markdown_path.read_text(encoding="utf-8")
    source = source.replace(
        '<div style="text-align: center">',
        '<div style="text-align: center" markdown="1">',
    )
    rendered = markdown.markdown(
        source,
        extensions=[
            "extra",
            "md_in_html",
            "sane_lists",
            "smarty",
        ],
        output_format="html5",
    )

    page = config["page"]
    page_format = css_page_format(page.get("format", "A4"))
    margins = page_margins(page.get("margins", "0"))
    title = str(config.get("title") or markdown_path.stem)
    base_url = markdown_path.resolve().parent.as_uri().rstrip("/") + "/"
    custom_css = config.get("customCss", "")
    if not isinstance(custom_css, str):
        raise ValueError("'customCss' must be a string")

    page_css = (
        "@page {\n"
        f"    size: {page_format};\n"
        f"    margin: {margins['top']} {margins['right']} "
        f"{margins['bottom']} {margins['left']};\n"
        "}"
    )
    style_css = build_style_css(config["styles"])

    return f"""<!doctype html>
<html lang="{html.escape(str(config.get("language", "ru")), quote=True)}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <base href="{html.escape(base_url, quote=True)}">
    <title>{html.escape(title)}</title>
    <style>
{page_css}
{BASE_CSS}
{style_css}
{custom_css}
    </style>
</head>
<body>
    <main class="markdown-body">
{rendered}
    </main>
</body>
</html>
"""


def render_pdf(markdown_path: Path, config: dict[str, Any], output_path: Path) -> None:
    document = build_html(markdown_path, config)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.emulate_media(media="print")
        page.set_content(document, wait_until="load")
        page.evaluate(
            """async () => {
                await document.fonts.ready;
                const pending = [...document.images]
                    .filter(image => !image.complete)
                    .map(image => new Promise(resolve => {
                        image.addEventListener('load', resolve, { once: true });
                        image.addEventListener('error', resolve, { once: true });
                    }));
                await Promise.all(pending);
            }"""
        )
        page.pdf(
            path=str(output_path),
            print_background=bool(config["page"].get("printBackground", True)),
            prefer_css_page_size=True,
        )
        browser.close()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    config_path = args.config.resolve()
    output_path = (
        args.output.resolve()
        if args.output
        else input_path.with_suffix(".pdf")
    )

    try:
        if input_path.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError("Input file must have a .md or .markdown extension")
        if not input_path.is_file():
            raise ValueError(f"Input file does not exist: {input_path}")
        config = read_config(config_path)
        render_pdf(input_path, config, output_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
