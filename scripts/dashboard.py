#!/usr/bin/env python3
"""Serve a local dashboard from the current workflow results."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
WEB_DIR = ROOT / "web"
CATEGORIES = ("apply", "need-review", "skip")
TEXT_FILES = frozenset({
    "vacancy-source.md", "vacancy.json", "avg-match.json",
    "red-team-match.json", "resume.md", "cover-letter.md",
})
PREVIEW_FILES = frozenset({"resume.pdf", "resume-preview.png"})


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def list_jobs() -> list[dict]:
    jobs = []
    for category in CATEGORIES:
        category_dir = RESULTS_DIR / category
        if not category_dir.is_dir():
            continue
        for company_dir in sorted(category_dir.iterdir()):
            if not company_dir.is_dir() or company_dir.name.startswith("."):
                continue
            for job_dir in sorted(company_dir.iterdir()):
                if not job_dir.is_dir() or job_dir.name.startswith("."):
                    continue
                metadata_path = job_dir / "metadata.json"
                if not metadata_path.is_file():
                    continue
                meta = read_json(metadata_path)
                decision = read_json(job_dir / "decision.json")
                source_url = meta.get("source_url")
                try:
                    parsed_url = urlsplit(source_url) if isinstance(source_url, str) else None
                    if not parsed_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                        source_url = ""
                except ValueError:
                    source_url = ""
                jobs.append({
                    "id": str(meta.get("job_id") or job_dir.name),
                    "category": category,
                    "company": str(meta.get("company") or company_dir.name),
                    "title": str(meta.get("title") or "Untitled"),
                    "source_url": source_url,
                    "avg_match": decision.get("avg_match"),
                    "red_team_match": decision.get("red_team_match"),
                    "dir_path": job_dir.relative_to(RESULTS_DIR).as_posix(),
                    "files": {
                        "resume_pdf": (job_dir / "resume.pdf").is_file(),
                        "resume_png": (job_dir / "resume-preview.png").is_file(),
                        "resume_md": (job_dir / "resume.md").is_file(),
                        "cover_letter": (job_dir / "cover-letter.md").is_file(),
                    },
                })
    return jobs


def result_file(path: str, prefix: str, allowed: frozenset[str]) -> Path | None:
    relative = unquote(path.removeprefix(prefix)).strip("/")
    parts = relative.split("/")
    if len(parts) != 4 or parts[0] not in CATEGORIES or parts[-1] not in allowed:
        return None
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        return None
    candidate = (RESULTS_DIR / Path(*parts)).resolve()
    try:
        candidate.relative_to(RESULTS_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/jobs":
            data = json.dumps(list_jobs(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path.startswith("/api/job/"):
            self.send_result(path, "/api/job/", TEXT_FILES)
        elif path.startswith("/api/preview/"):
            self.send_result(path, "/api/preview/", PREVIEW_FILES)
        else:
            super().do_GET()

    def send_result(self, path: str, prefix: str, allowed: frozenset[str], head: bool = False) -> None:
        file_path = result_file(path, prefix, allowed)
        if file_path is None:
            self.send_error(404, "File not found")
            return
        content = file_path.read_bytes()
        content_type = self.guess_type(file_path.name)
        if file_path.suffix in {".md", ".json"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if not head:
            self.wfile.write(content)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/api/preview/"):
            self.send_result(path, "/api/preview/", PREVIEW_FILES, head=True)
        else:
            super().do_HEAD()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), DashboardHandler) as server:
        print(f"Dashboard: http://{args.host}:{server.server_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
