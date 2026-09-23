# One-page resume rendering rules

## Fixed base and completion invariant

- Start every render cycle from the untouched `themes/default.json`.
- Keep its Harvard/Perfect Art font, colors, page size, and margins fixed.
- Require exactly one A4 page.
- Require `fill_percent >= 97`, calculated over the working height without page
  margins. More than 3% free working height is not a finished render.
- Treat large page/background rectangles as decoration, not visible content.
- Reject clipped, overlapping, or out-of-bounds content.
- Keep every employer/title/date heading on one visual line.
- Keep every `CORE SKILLS` group on one visual line. Every rendered line inside
  that section must begin with a short category label followed by a colon; an
  unlabeled continuation line is a failed render.

## Measure

```bash
python3 ../scripts/inspect_pdf.py resume.pdf \
  --theme themes/default.json
```

Use unrounded values for decisions. A passing measurement has one page,
`fill_percent >= 97`, `free_percent <= 3`, and `passes=true`.
The `core_skills.passes` field must also be `true`.

## Fit loop

Repeat:

1. Render with a fresh copy of `default.json`.
2. If the PDF has one page and `fill_percent >= 97`, finish.
   Finish only when `core_skills.passes=true`.
3. If the PDF has more than one page:
   - count all visual lines after page one;
   - request a full text regeneration removing at least that many lines;
   - discard working spacing changes and restart from `default.json`.
4. If the PDF has one page and `free_percent > 15`:
   - record current visual-line count and fill percentage;
   - calculate
     `lines_to_add = ceil(current_lines * 95 / fill_percent) - current_lines`;
   - request a full rewrite adding approximately that many relevant,
     allowlisted lines;
   - restart from `default.json`; 95% is only an intermediate target.
5. If the PDF has one page and `3 < free_percent <= 15`, expand spacing using
   the band selected from the table below.
6. If any `CORE SKILLS` group wraps, request a `core-skills-rewrite`: preserve
   4–6 labeled groups, shorten lower-priority technology lists, and render again
   from `default.json`. Do not reduce the font or hide overflow.
7. Stop after three ineffective content revisions and classify the package as
   `need-review`.

Spacing bands and starting parameters:

| Free working height | Start with |
|---|---|
| `[15%, 13%)` | `li.lineHeight` |
| `[13%, 11%)` | `paragraph.lineHeight` |
| `[11%, 9%)` | `li.marginTop` + `li.marginBottom` |
| `[9%, 7%)` | `h2.marginTop` + `h2.marginBottom` |
| `[7%, 5%)` | `h3.marginTop` + `h3.marginBottom` |
| `[5%, 4%)` | `paragraph.marginTop` + `paragraph.marginBottom` |
| `[4%, 3%)` | `h1.marginTop` + `h1.marginBottom` |

Use `+0.01` for unitless line height, `+0.05em` for `em`, and `+2px` for
pixel values. Re-render after every increment:

- if the PDF remains one page with more than 3% free height, increment the same
  parameter again;
- if it reaches at least 97%, finish;
- if it becomes two pages, roll back that increment and continue with the next
  parameter in the table;
- if every parameter is exhausted below 97%, request a precise content
  expansion to 97% and restart from `default.json`.

## Visual QA

Render the final page to PNG:

```bash
pdftoppm -png -f 1 -singlefile resume.pdf resume-preview
```

Inspect the PNG for clipping, overlap, broken glyphs, inconsistent hierarchy,
an employer heading wrapping to a second line, or an unlabeled continuation in
`CORE SKILLS`. Measurement alone is not visual approval.
