# Local Codex job-search workflow

This folder contains a local, review-first job-search pipeline. It uses the
installed `codex` CLI with the saved ChatGPT login and does not use the OpenAI
Platform API.

The architectural rationale, state machine, safety boundary, and limit-control
strategy are documented in `ARCHITECTURE.ru.md`.

The workflow never applies automatically. It produces reviewable folders:

```text
results/
├── apply/<company>/<vacancy>/
├── need-review/<company>/<vacancy>/
└── skip/<company>/<vacancy>/
```

Every processed vacancy keeps its source, normalized record, independent
average-fit and red-team reports, decision, logs, and validation. `apply` and
`need-review` also receive a tailored one-page resume and cover-letter draft.

A timezone, GMT, or UTC offset mismatch alone never lowers fit scores or forces
`skip`. The deterministic merge keeps any mistakenly reported timezone-only
blocker as an audit note. Missing-skill tracking combines both evaluations for
`skip` and `need-review`, stores up to eight relevant capability gaps, and
excludes hiring filters such as geography, visa, language, citizenship,
schedule, and clearance.

The local runtime profile in `config/codex-runtime.json` pins `gpt-5.4` because
the currently installed Codex CLI cannot run the newer model selected by the
user-level global configuration. This still uses ChatGPT/Codex plan limits and
does not use an API key.

Non-search stages also ignore the user-level Codex config and disable unrelated
plugins, apps, browser, computer-use, image generation, and subagents. Repo
instructions and repo skills remain available. This keeps each isolated stage
from paying the context cost of globally installed capabilities.

## Quick Setup

Initialize your local configuration and candidate profile from the `.example` templates:

```bash
cp config/candidate-profile.json.example config/candidate-profile.json
cp config/public-disclosure.json.example config/public-disclosure.json
cp config/search-profile.json.example config/search-profile.json
cp master/master-cv-en.md.example master/master-cv-en.md
cp master/master-cv-ru.md.example master/master-cv-ru.md
cp master/facts.json.example master/facts.json
cp master/fact-audit.md.example master/fact-audit.md
```

Before preparing real application materials, replace the fictional disclosure
example with private rules approved for the candidate. The deterministic
validator stops when this local policy is missing.

## Important source files

- `master/master-cv-en.md` and `master/master-cv-ru.md`: narrative master CVs (initialized from `.example` templates).
- `master/facts.json`: allowlisted facts used by automation.
- `master/fact-audit.md`: conflicts and evidence quality.
- `config/candidate-profile.json`: candidate contact details and preferences.
- `config/search-profile.json`: target roles, locations, and exclusions.
- `config/thresholds.json`: deterministic classification gates.
- `.agents/skills/`: reusable Codex workflows.
- `.codex/agents/`: optional focused agent roles for interactive Codex work.

## View local results

The dashboard reads the current `results/` directory when requested; no result
records are stored in the web assets. Run it on your machine with:

```bash
python3 scripts/dashboard.py
```

Open `http://127.0.0.1:8000`. Use `--port` to choose another port.

## Check the local environment

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r md-renderer/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/pipeline.py check
```

The check fails when API-key environment variables are present or Codex is not
logged in through ChatGPT. It also audits all fact IDs and every retained
`path:line` evidence pointer. Run the fact check directly with:

```bash
.venv/bin/python scripts/audit_facts.py
```

## Process an existing vacancy

```bash
.venv/bin/python scripts/pipeline.py ingest path/to/vacancy.md \
  --company "ExampleCo" \
  --title "IT Specialist" \
  --source-url "https://example.com/job"

.venv/bin/python scripts/pipeline.py process <job-id>
```

Use `.venv/bin/python scripts/pipeline.py list` to see queued and completed job IDs.

To override an automated skip after human review and intentionally prepare a
draft package:

```bash
.venv/bin/python scripts/pipeline.py prepare <job-id> \
  --category need-review
```

This explicit command is the only path that generates application material for
an automated `skip`.

## Search for vacancies

```bash
.venv/bin/python scripts/pipeline.py search --limit 5
```

Search needs web access in the active Codex environment. When a job board blocks
automation, save the complete vacancy text and use `ingest`; the remaining
pipeline is identical.

## Render or inspect a resume directly

```bash
.venv/bin/python md-renderer/render_pdf.py resume.md \
  --config md-renderer/themes/default.json \
  --output resume.pdf

.venv/bin/python scripts/inspect_pdf.py resume.pdf \
  --theme md-renderer/themes/default.json
```

The one-page rules are in `md-renderer/ONE_PAGE_RESUME_RULES.md`. The copied
historical algorithm remains in `md-renderer/RESUME_RENDERING_AGENT.md`.
The active workflow implements the historical 97% fill rule and renders from
`default.json`. It uses a neutral one-page visual system with Times New
Roman 9 pt body text. The PDF inspector ignores Chromium's invisible
page-background rectangles.

## Human review boundary

Before applying, manually verify:

1. the role is still open and the source URL is correct;
2. name spelling, work authorization, location, and compensation;
3. every gap and qualifier in `decision.json`;
4. the resume PDF visually;
5. the cover letter and any employer-specific questions.
