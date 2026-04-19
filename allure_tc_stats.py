#!/usr/bin/env python3
"""
allure_tc_stats.py

Generate a CSV of test-case statistics (Total Runs, Failed, Success Rate)
from one or more Allure report URLs.

Example:
    python allure_tc_stats.py \
        http://host:4446/allure-docker-service/projects/my-project/reports/21/index.html \
        --out ./reports
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------- HTTP session with retries ----------------

def build_session(timeout: int = 30) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def _get_json(session: requests.Session, url: str) -> Any:
    resp = session.get(url, timeout=getattr(session, "request_timeout", 30))
    resp.raise_for_status()
    return resp.json()


# ---------------- URL helpers ----------------

def normalize_report_base(url: str) -> str:
    """Return the report base URL ending with '/', e.g.
       http://host/.../reports/21/  from any form of the report URL."""
    # Strip fragment (#...) and anything after index.html
    cleaned = url.split("#", 1)[0]
    cleaned = re.sub(r"index\.html.*$", "", cleaned)
    if not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned


def report_slug(base_url: str) -> str:
    """Build a filesystem-safe slug like 'my-project_report-21'."""
    m = re.search(r"/projects/([^/]+)/reports/(\d+)/?", base_url)
    if m:
        return f"{m.group(1)}_report-{m.group(2)}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", base_url).strip("_")


# ---------------- Suite traversal ----------------

def collect_test_cases(suites_tree: dict) -> list[dict]:
    """Walk the suites.json tree and return a flat list of test cases.
       Each item is a leaf node (no 'children') with a status and uid."""
    tcs: list[dict] = []

    def walk(node: dict) -> None:
        if "children" in node and node["children"]:
            for ch in node["children"]:
                walk(ch)
        elif node.get("uid") and node.get("status"):
            tcs.append({
                "uid": node["uid"],
                "name": node.get("name", node["uid"]),
                "status": node["status"],
            })

    walk(suites_tree)
    return tcs


# ---------------- Per-TC statistics ----------------

def compute_stat(tc_payload: dict) -> dict[str, int]:
    """Combine current-run status with historical statistic."""
    extra = tc_payload.get("extra") or {}
    history = extra.get("history") or {}
    hist_stat = (history.get("statistic") or {}).copy()
    counters = {k: int(hist_stat.get(k, 0)) for k in ("passed", "failed", "broken", "skipped", "unknown")}
    counters["total"] = int(hist_stat.get("total", sum(counters.values())))

    current_status = tc_payload.get("status")
    counters["total"] += 1
    if current_status in counters:
        counters[current_status] += 1
    else:
        counters["unknown"] += 1

    # Treat "broken" as a failure for the reporting columns
    counters["failed_total"] = counters["failed"] + counters["broken"]
    return counters


def format_failed_cell(c: dict[str, int]) -> str:
    total = c["total"]
    failed = c["failed_total"]
    skipped = c["skipped"]
    if total > 0 and failed == total:
        return "AF"
    if total > 0 and skipped == total:
        return "SKP"
    return str(failed)


def success_rate(c: dict[str, int]) -> str:
    total = c["total"]
    if total <= 0:
        return "0.0%"
    return f"{c['passed'] / total * 100:.1f}%"


# ---------------- Main per-report pipeline ----------------

def process_report(
    report_url: str,
    session: requests.Session,
    workers: int,
) -> list[dict[str, str]]:
    base = normalize_report_base(report_url)
    suites_url = urljoin(base, "data/suites.json")
    print(f"[+] Fetching {suites_url}")
    suites = _get_json(session, suites_url)
    tcs = collect_test_cases(suites)
    print(f"    Found {len(tcs)} test cases. Fetching history...")

    rows: list[dict[str, str]] = []

    def fetch_one(tc: dict) -> dict[str, str] | None:
        tc_url = urljoin(base, f"data/test-cases/{tc['uid']}.json")
        try:
            payload = _get_json(session, tc_url)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {tc['name']} ({tc['uid']}): {exc}", file=sys.stderr)
            return None
        c = compute_stat(payload)
        return {
            "TC": tc["name"],
            "Total Runs": str(c["total"]),
            "Failed": format_failed_cell(c),
            "Success Rate": success_rate(c),
            "_sort_rank": _sort_rank(c),
            "_sort_rate": f"{(c['passed'] / c['total']) if c['total'] else 0:.6f}",
        }

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(fetch_one, tcs):
            if row is not None:
                rows.append(row)

    rows.sort(key=lambda r: (r["_sort_rank"], float(r["_sort_rate"]), r["TC"]))
    for r in rows:
        r.pop("_sort_rank", None)
        r.pop("_sort_rate", None)
    return rows


def _sort_rank(c: dict[str, int]) -> int:
    # 0 = AF, 1 = SKP, 2 = partial failures, 3 = always passing
    if c["total"] > 0 and c["failed_total"] == c["total"]:
        return 0
    if c["total"] > 0 and c["skipped"] == c["total"]:
        return 1
    if c["failed_total"] > 0:
        return 2
    return 3


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["TC", "Total Runs", "Failed", "Success Rate"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[+] Wrote {len(rows)} rows -> {path}")


# ---------------- CLI ----------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate CSV statistics from Allure report URLs.",
    )
    p.add_argument(
        "urls",
        nargs="*",
        help="One or more Allure report URLs (index.html or report root).",
    )
    p.add_argument(
        "--urls-file",
        help="Optional path to a text file with one URL per line (blank lines / lines starting with '#' are ignored).",
    )
    p.add_argument(
        "--out",
        default="./reports",
        help="Directory to write CSV files into (default: ./reports).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Parallel fetch workers per report (default: 16).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout per request in seconds (default: 30).",
    )
    return p.parse_args()


def read_urls_file(path: str) -> list[str]:
    urls = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def main() -> int:
    args = parse_args()
    urls = list(args.urls)
    if args.urls_file:
        urls.extend(read_urls_file(args.urls_file))
    if not urls:
        print("No URLs provided. Pass them as arguments or via --urls-file.", file=sys.stderr)
        return 2

    session = build_session(timeout=args.timeout)
    out_dir = Path(args.out)

    exit_code = 0
    for url in urls:
        try:
            rows = process_report(url, session, workers=args.workers)
            base = normalize_report_base(url)
            csv_path = out_dir / f"{report_slug(base)}.csv"
            write_csv(rows, csv_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Failed to process {url}: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
    