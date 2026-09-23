# Python PDF renderer

The renderer accepts Markdown with raw HTML and inline styles, a JSON theme, and
creates a PDF through Chromium's print engine.

## Install

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

## Run

```bash
python3 render_pdf.py document.md \
  --config themes/default.json \
  --output document.pdf
```

`--config` is optional and defaults to `themes/default.json`. If `--output` is
omitted, the PDF is written next to the Markdown file.

Raw HTML is intentionally enabled, so only render trusted Markdown.

## Page settings

A common margin for every side:

```json
{
  "page": {
    "format": "A4",
    "margins": "0.2in"
  }
}
```

A common margin with side-specific overrides:

```json
{
  "page": {
    "format": "A4",
    "margins": {
      "all": "0.2in",
      "left": "0.3in",
      "right": "0.2in"
    }
  }
}
```

All four sides can also be specified explicitly. Page format accepts common
names such as `A4`, `Letter`, and `Legal`, or dimensions such as
`210mm 297mm`.

Theme files may inherit another file located in the same directory:

```json
{
  "extends": "_web-base.json",
  "styles": {
    "general": {
      "fontFamily": "'Georgia', serif"
    }
  }
}
```
