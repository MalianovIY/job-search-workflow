#!/usr/bin/env python3
"""Fetch public vacancy URLs, save auditable Markdown, and enqueue them."""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import re
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "state/jobs.sqlite"
PIPELINE = ROOT / "scripts/pipeline.py"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0 Safari/537.36"
)
SYSTEM_CA_FILE = Path("/etc/ssl/cert.pem")


def canonical_url(raw_url: str) -> str:
    parts = urlsplit(raw_url.strip())
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = parts.query
    if parts.hostname in {"getmatch.ru", "nofluffjobs.com"}:
        query = ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9а-я]+", "", value.casefold())


def slugify(value: str, maximum: int = 90) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9а-я]+", "-", value, flags=re.IGNORECASE)
    value = value.strip("-")
    return (value or "vacancy")[:maximum].rstrip("-")


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._chunks: list[str] = []
        self.scripts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attrs_dict = dict(attrs)
        if tag == "script" and "ld+json" in (attrs_dict.get("type") or ""):
            self._capture = True
            self._chunks = []
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.scripts.append("".join(self._chunks))
            self._capture = False
            self._chunks = []
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)
        if self._in_title:
            self.title += data


class TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
    SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._list_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in {"ul", "ol"}:
            self._list_depth += 1
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n" + "  " * max(0, self._list_depth - 1) + "- ")
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def markdown(self) -> str:
        text = html.unescape("".join(self._parts))
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def is_job_posting(value: dict[str, Any]) -> bool:
    types = value.get("@type", "")
    if isinstance(types, str):
        return types.casefold() == "jobposting"
    return any(str(item).casefold() == "jobposting" for item in types or [])


def organization_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def location_text(value: Any) -> str:
    entries = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address", entry)
        if isinstance(address, str):
            locations.append(address)
            continue
        if not isinstance(address, dict):
            continue
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        rendered = ", ".join(str(item).strip() for item in parts if item)
        if rendered:
            locations.append(rendered)
    return "; ".join(dict.fromkeys(locations))


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def fetch_url(url: str, *, timeout: int, retries: int) -> tuple[str, str]:
    last_error: Exception | None = None
    ssl_context = (
        ssl.create_default_context(cafile=str(SYSTEM_CA_FILE))
        if SYSTEM_CA_FILE.is_file()
        else ssl.create_default_context()
    )
    for attempt in range(retries + 1):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urlopen(request, timeout=timeout, context=ssl_context) as response:
                content_type = response.headers.get_content_charset() or "utf-8"
                raw = response.read()
                return raw.decode(content_type, errors="replace"), response.geturl()
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error) if last_error else "unknown fetch error")


@dataclass
class VacancyRecord:
    source_site: str
    source_url: str
    final_url: str
    company: str
    title: str
    description: str
    location: str
    work_mode: str
    employment_type: str
    date_posted: str
    valid_through: str
    active: bool | None
    extraction: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def extract_record(item: dict[str, Any], page: str, final_url: str) -> VacancyRecord:
    collector = ScriptCollector()
    collector.feed(page)
    manifest_description = str(item.get("description") or "").strip()
    job: dict[str, Any] | None = None
    for script in collector.scripts:
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        job = next((obj for obj in iter_json_objects(parsed) if is_job_posting(obj)), None)
        if job:
            break

    if job:
        description_html = str(job.get("description") or "")
        extractor = TextExtractor()
        extractor.feed(description_html)
        description = extractor.markdown()
        title = str(job.get("title") or item.get("title") or "").strip()
        company = organization_name(job.get("hiringOrganization"))
        location = location_text(job.get("jobLocation"))
        work_mode = str(job.get("jobLocationType") or "").strip()
        employment_type_value = job.get("employmentType")
        if isinstance(employment_type_value, list):
            employment_type = ", ".join(map(str, employment_type_value))
        else:
            employment_type = str(employment_type_value or "").strip()
        date_posted = str(job.get("datePosted") or "").strip()
        valid_through = str(job.get("validThrough") or "").strip()
        extraction = "json-ld"
    else:
        if manifest_description:
            description = manifest_description
            extraction = "manifest-description-fallback"
        else:
            extractor = TextExtractor()
            extractor.feed(page)
            description = extractor.markdown()
            extraction = "visible-text-fallback"
        title = str(item.get("title") or collector.title).strip()
        company = str(item.get("company") or "").strip()
        location = str(item.get("location") or "").strip()
        work_mode = ""
        employment_type = ""
        date_posted = str(item.get("date_posted") or "").strip()
        valid_through = ""

    valid_date = parse_date(valid_through)
    active = None if valid_date is None else valid_date >= datetime.now(timezone.utc).date()
    return VacancyRecord(
        source_site=str(item.get("site") or urlsplit(final_url).hostname or ""),
        source_url=canonical_url(str(item["url"])),
        final_url=canonical_url(final_url),
        company=company or str(item.get("company") or "").strip() or infer_company(item, title),
        title=clean_title(title, item),
        description=description,
        location=location or str(item.get("location") or "").strip(),
        work_mode=work_mode,
        employment_type=employment_type,
        date_posted=date_posted,
        valid_through=valid_through,
        active=active,
        extraction=extraction,
    )


def clean_title(title: str, item: dict[str, Any]) -> str:
    site = str(item.get("site") or "")
    value = re.sub(r"\s+", " ", html.unescape(title)).strip()
    suffixes = {
        "jobspresso.co": r"\s*\|\s*Jobspresso$",
        "jobstash.xyz": r"\s*\|\s*JobStash$",
    }
    if site in suffixes:
        value = re.sub(suffixes[site], "", value, flags=re.IGNORECASE).strip()
    return value or "Unknown vacancy title"


def infer_company(item: dict[str, Any], title: str) -> str:
    raw_title = str(item.get("title") or title)
    site = str(item.get("site") or "")
    if site == "jobstash.xyz" and " at " in raw_title:
        return raw_title.rsplit(" at ", 1)[-1].split("|", 1)[0].strip()
    if " - " in raw_title:
        return raw_title.rsplit(" - ", 1)[-1].split("|", 1)[0].strip()
    return site or "Unknown company"


def record_markdown(record: VacancyRecord) -> str:
    fields = [
        ("Source URL", record.source_url),
        ("Final URL", record.final_url),
        ("Source site", record.source_site),
        ("Company", record.company),
        ("Title", record.title),
        ("Location", record.location or "not mentioned"),
        ("Work mode", record.work_mode or "not mentioned"),
        ("Employment type", record.employment_type or "not mentioned"),
        ("Date posted", record.date_posted or "not mentioned"),
        ("Valid through", record.valid_through or "not mentioned"),
        (
            "Active status",
            "active" if record.active is True else "expired" if record.active is False else "unknown",
        ),
        ("Extraction", record.extraction),
    ]
    metadata = "\n".join(f"- **{key}:** {value}" for key, value in fields)
    return f"# {record.title}\n\n{metadata}\n\n## Description\n\n{record.description}\n"


def load_existing_jobs() -> tuple[set[str], set[tuple[str, str]]]:
    if not DB_PATH.exists():
        return set(), set()
    connection = sqlite3.connect(DB_PATH)
    try:
        rows = connection.execute("SELECT company, title, source_url FROM jobs").fetchall()
    finally:
        connection.close()
    urls = {canonical_url(row[2]) for row in rows if row[2]}
    pairs = {
        (normalized_key(row[0]), normalized_key(row[1]))
        for row in rows
        if row[0] and row[1]
    }
    return urls, pairs


def should_skip_duplicate(
    record: VacancyRecord,
    existing_urls: set[str],
    existing_pairs: set[tuple[str, str]],
) -> str | None:
    if record.source_url in existing_urls or record.final_url in existing_urls:
        return "existing-source-url"
    pair = (normalized_key(record.company), normalized_key(record.title))
    if all(pair) and pair in existing_pairs:
        return "existing-company-title"
    return None


def ingest_record(record: VacancyRecord, source_file: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(PIPELINE),
            "ingest",
            str(source_file),
            "--company",
            record.company,
            "--title",
            record.title,
            "--source-url",
            record.source_url,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return {
            "status": "ingest-error",
            "error": result.stderr.strip() or result.stdout.strip(),
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"raw_output": result.stdout.strip()}
    return {"status": "ingested", **payload}


def fetch_item(
    index: int,
    item: dict[str, Any],
    *,
    timeout: int,
    retries: int,
    host_locks: dict[str, threading.Lock],
    host_last_started: dict[str, float],
    min_host_interval: float,
) -> tuple[int, dict[str, Any], dict[str, Any], VacancyRecord | None]:
    url = canonical_url(str(item["url"]))
    result: dict[str, Any] = {
        "index": index,
        "source_site": item.get("site", ""),
        "source_url": url,
        "source_title": item.get("title", ""),
    }
    skip_fetch_reason = str(item.get("skip_fetch_reason") or "").strip()
    if skip_fetch_reason:
        result["status"] = "fetch-error"
        result["error"] = skip_fetch_reason
        return index, item, result, None
    try:
        host = urlsplit(url).hostname or ""
        with host_locks[host]:
            delay = min_host_interval - (
                time.monotonic() - host_last_started.get(host, 0.0)
            )
            if delay > 0:
                time.sleep(delay)
            host_last_started[host] = time.monotonic()
            page, final_url = fetch_url(url, timeout=timeout, retries=retries)
        record = extract_record(item, page, final_url)
        result["record"] = record.as_dict()
        return index, item, result, record
    except Exception as error:
        if str(item.get("description") or "").strip():
            record = extract_record(item, "", url)
            result["record"] = record.as_dict()
            result["fetch_warning"] = str(error)
            return index, item, result, record
        result["status"] = "fetch-error"
        result["error"] = str(error)
        return index, item, result, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--records-output", type=Path, required=True)
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-host-interval", type=float, default=0.0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = payload.get("vacancies", payload)
    if args.limit is not None:
        items = items[: args.limit]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.records_output.parent.mkdir(parents=True, exist_ok=True)
    existing_urls, existing_pairs = load_existing_jobs()
    output: list[dict[str, Any]] = []
    pending_items: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(items, start=1):
        url = canonical_url(str(item["url"]))
        if url not in existing_urls:
            pending_items.append((index, item))
            continue
        output.append(
            {
                "index": index,
                "source_site": item.get("site", ""),
                "source_url": url,
                "source_title": item.get("title", ""),
                "status": "duplicate",
                "duplicate_reason": "existing-source-url",
            }
        )

    by_host: dict[str, deque[tuple[int, dict[str, Any]]]] = {}
    for index, item in pending_items:
        host = urlsplit(canonical_url(str(item["url"]))).hostname or ""
        by_host.setdefault(host, deque()).append((index, item))
    fetch_queue: list[tuple[int, dict[str, Any]]] = []
    while by_host:
        for host in list(by_host):
            fetch_queue.append(by_host[host].popleft())
            if not by_host[host]:
                del by_host[host]

    worker_count = max(1, args.workers)
    hosts = {
        urlsplit(canonical_url(str(item["url"]))).hostname or ""
        for _, item in fetch_queue
    }
    host_locks = {host: threading.Lock() for host in hosts}
    host_last_started: dict[str, float] = {}
    executor = ThreadPoolExecutor(max_workers=worker_count)
    futures: dict[
        Future[tuple[int, dict[str, Any], dict[str, Any], VacancyRecord | None]],
        int,
    ] = {
        executor.submit(
            fetch_item,
            index,
            item,
            timeout=args.timeout,
            retries=args.retries,
            host_locks=host_locks,
            host_last_started=host_last_started,
            min_host_interval=max(0.0, args.min_host_interval),
        ): index
        for index, item in fetch_queue
    }
    completed = len(items) - len(pending_items)
    try:
        for future in as_completed(futures):
            index, item, result, record = future.result()
            completed += 1
            if record is None:
                output.append(result)
                print(
                    f"[{completed}/{len(items)}; item {index}] "
                    f"{result['status']}: {item.get('title', result['source_url'])}",
                    flush=True,
                )
                continue

            if len(record.description) < 250:
                result["status"] = "insufficient-description"
            elif record.active is False and not args.include_expired:
                result["status"] = "expired"
            else:
                digest = hashlib.sha256(record.source_url.encode("utf-8")).hexdigest()[:8]
                filename = f"{slugify(record.company, 35)}-{slugify(record.title, 55)}-{digest}.md"
                source_file = args.output_dir / filename
                source_file.write_text(record_markdown(record), encoding="utf-8")
                result["source_file"] = str(source_file.resolve())
                duplicate_reason = should_skip_duplicate(
                    record, existing_urls, existing_pairs
                )
                if duplicate_reason:
                    result["status"] = "duplicate"
                    result["duplicate_reason"] = duplicate_reason
                elif args.ingest:
                    result.update(ingest_record(record, source_file))
                    if result["status"] == "ingested":
                        existing_urls.add(record.source_url)
                        existing_urls.add(record.final_url)
                        existing_pairs.add(
                            (normalized_key(record.company), normalized_key(record.title))
                        )
                else:
                    result["status"] = "saved"
            output.append(result)
            print(
                f"[{completed}/{len(items)}; item {index}] "
                f"{result['status']}: {item.get('title', result['source_url'])}",
                flush=True,
            )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    output.sort(key=lambda item: item["index"])
    summary: dict[str, int] = {}
    for item in output:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": str(args.manifest.resolve()),
        "summary": summary,
        "records": output,
    }
    args.records_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary.get("ingest-error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
