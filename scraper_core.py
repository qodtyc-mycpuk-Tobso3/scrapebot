#!/usr/bin/env python3
"""
General-purpose web scraper & automation toolkit.

Two fetch modes:
  - "static"  : requests + BeautifulSoup. Works anywhere, including iSH on iPhone.
  - "dynamic" : Playwright (headless Chromium). Needs a real server
                (Railway / Oracle Cloud) — will NOT run inside iSH.

Usage:
    python3 scraper.py run configs/example.json
    python3 scraper.py schedule configs/example.json --every 30
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _get_session() -> requests.Session:
    """A requests session with automatic retries on transient failures."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.5,  # 1.5s, 3s, 6s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _get_session()


def is_allowed_by_robots(url: str, user_agent: str = "*") -> bool:
    """Check robots.txt before scraping. Fails open (allows) if robots.txt
    is unreachable, since that's the common/permissive default for scrapers."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    if robots_url not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.read()
        except Exception:
            log.warning(f"Could not fetch {robots_url}, proceeding cautiously")
            return True
        _robots_cache[robots_url] = rp

    return _robots_cache[robots_url].can_fetch(user_agent, url)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

@dataclass
class FieldSpec:
    name: str
    selector: str
    attr: str | None = None      # e.g. "href", "src". None = text content
    regex: str | None = None     # optional post-processing regex (first group)


@dataclass
class ScrapeConfig:
    name: str
    url: str
    mode: str = "static"          # "static" or "dynamic"
    item_selector: str | None = None   # CSS selector for repeating items (e.g. product cards)
    fields: list[FieldSpec] = field(default_factory=list)
    output: str = "output.csv"    # .csv or .json
    headers: dict[str, str] = field(default_factory=dict)
    wait_selector: str | None = None   # dynamic mode: CSS selector to wait for
    paginate_param: str | None = None  # e.g. "page" -> url?page=2
    page_url_template: str | None = None  # e.g. "https://site.com/page/{page}/" - takes priority over paginate_param
    max_pages: int = 1
    respect_robots: bool = True
    timeout: int = 20

    @staticmethod
    def load(path: str) -> "ScrapeConfig":
        data = json.loads(Path(path).read_text())
        data["fields"] = [FieldSpec(**f) for f in data.get("fields", [])]
        cfg = ScrapeConfig(**data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        errors = []
        if not self.url.startswith(("http://", "https://")):
            errors.append(f"url must start with http:// or https://, got: {self.url!r}")
        if self.mode not in ("static", "dynamic"):
            errors.append(f"mode must be 'static' or 'dynamic', got: {self.mode!r}")
        if not self.fields:
            errors.append("fields cannot be empty - define at least one field to extract")
        for f in self.fields:
            if f.attr and f.regex:
                # sanity check the regex compiles
                try:
                    re.compile(f.regex)
                except re.error as e:
                    errors.append(f"field '{f.name}': invalid regex {f.regex!r} ({e})")
        if self.max_pages < 1:
            errors.append(f"max_pages must be >= 1, got: {self.max_pages}")
        if errors:
            raise ValueError(
                f"Config '{self.name}' has {len(errors)} error(s):\n  - " + "\n  - ".join(errors)
            )


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

def fetch_static(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    respect_robots: bool = True,
) -> str:
    if respect_robots and not is_allowed_by_robots(url):
        raise PermissionError(
            f"robots.txt disallows fetching {url}. "
            f"Set \"respect_robots\": false in the config to override "
            f"(only do this if you've confirmed you're authorized to scrape this site)."
        )

    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    try:
        resp = _SESSION.get(url, headers=h, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise TimeoutError(f"Request to {url} timed out after {timeout}s and 3 retries")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP error fetching {url}: {e}")
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"Could not connect to {url}: {e}")
    return resp.text


def fetch_dynamic(url: str, wait_selector: str | None = None) -> str:
    """Requires Playwright. Only works on a real server, not iSH."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright not installed. Dynamic mode needs a server "
            "(Railway/Oracle), not iSH on iPhone. Run:\n"
            "  pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        raise

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, timeout=30000)
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=15000)
        html = page.content()
        browser.close()
        return html


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_value(node, spec: FieldSpec, base_url: str | None = None) -> str:
    target = node.select_one(spec.selector) if spec.selector else node
    if target is None:
        return ""
    if spec.attr:
        raw = target.get(spec.attr, "") or ""
        value = " ".join(raw) if isinstance(raw, list) else raw
        # Auto-resolve relative URLs for link/src-like attributes
        if base_url and spec.attr in ("href", "src") and value:
            value = urljoin(base_url, value)
    else:
        value = target.get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", value).strip()
    if spec.regex and value:
        m = re.search(spec.regex, value)
        value = m.group(1) if m else ""
    return value


def scrape_page(html: str, cfg: ScrapeConfig, base_url: str | None = None) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, str]] = []
    base_url = base_url or cfg.url

    if cfg.item_selector:
        items = soup.select(cfg.item_selector)
        for item in items:
            row = {f.name: extract_value(item, f, base_url) for f in cfg.fields}
            rows.append(row)
    else:
        # single-record page (e.g. one product/article per URL)
        row = {f.name: extract_value(soup, f, base_url) for f in cfg.fields}
        rows.append(row)

    return rows


def build_url(base_url: str, cfg: ScrapeConfig, page_num: int) -> str:
    if cfg.page_url_template:
        return cfg.page_url_template.format(page=page_num)
    if not cfg.paginate_param or page_num == 1:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{cfg.paginate_param}={page_num}"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def run(cfg: ScrapeConfig) -> list[dict[str, str]]:
    all_rows: list[dict[str, str]] = []

    for page_num in range(1, cfg.max_pages + 1):
        url = build_url(cfg.url, cfg, page_num)
        log.info(f"[{cfg.name}] fetching page {page_num}/{cfg.max_pages}: {url}")

        try:
            if cfg.mode == "dynamic":
                html = fetch_dynamic(url, cfg.wait_selector)
            else:
                html = fetch_static(
                    url, cfg.headers, timeout=cfg.timeout, respect_robots=cfg.respect_robots
                )
        except PermissionError as e:
            log.error(str(e))
            raise
        except (TimeoutError, ConnectionError, RuntimeError) as e:
            log.error(f"[{cfg.name}] page {page_num} failed: {e}")
            if page_num == 1:
                raise  # first page failing is fatal - nothing to salvage
            log.warning(f"[{cfg.name}] skipping page {page_num}, continuing with what we have")
            break

        rows = scrape_page(html, cfg, base_url=url)

        if not rows:
            if page_num == 1:
                raise RuntimeError(
                    f"[{cfg.name}] item_selector {cfg.item_selector!r} matched 0 elements "
                    f"on the first page. The site's HTML structure likely changed, or "
                    f"the selector is wrong. Nothing was saved."
                )
            log.info(f"[{cfg.name}] no rows found on page {page_num}, stopping pagination.")
            break

        all_rows.extend(rows)
        log.info(f"[{cfg.name}] page {page_num}: extracted {len(rows)} rows")
        if page_num < cfg.max_pages:
            time.sleep(1)  # be polite between pages

    save(all_rows, cfg.output)
    log.info(f"[{cfg.name}] saved {len(all_rows)} rows -> {cfg.output}")
    return all_rows


def save(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        log.warning(f"No rows to save for {path} - skipping write")
        return
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        if path.endswith(".json"):
            tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        tmp.replace(target)  # atomic on POSIX - avoids partial/corrupt files
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------
# Scheduling (simple automation, no extra deps)
# --------------------------------------------------------------------------

def run_forever(cfg: ScrapeConfig, every_minutes: int) -> None:
    log.info(f"Scheduling '{cfg.name}' every {every_minutes} min. Ctrl+C to stop.")
    consecutive_failures = 0
    while True:
        try:
            run(cfg)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            log.error(f"[{cfg.name}] run failed ({consecutive_failures} in a row): {e}")
            if consecutive_failures >= 5:
                log.error(
                    f"[{cfg.name}] failed 5 times in a row - stopping schedule. "
                    f"Check the site structure or config before restarting."
                )
                return
        time.sleep(every_minutes * 60)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="General web scraper & automation tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run a scrape config once")
    p_run.add_argument("config", help="Path to JSON config file")

    p_sched = sub.add_parser("schedule", help="Run a scrape config repeatedly")
    p_sched.add_argument("config", help="Path to JSON config file")
    p_sched.add_argument("--every", type=int, default=30, help="Interval in minutes")

    p_validate = sub.add_parser("validate", help="Check a config file without scraping")
    p_validate.add_argument("config", help="Path to JSON config file")

    args = parser.parse_args()

    try:
        cfg = ScrapeConfig.load(args.config)
    except FileNotFoundError:
        log.error(f"Config file not found: {args.config}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"Config file is not valid JSON: {e}")
        sys.exit(1)
    except (ValueError, TypeError) as e:
        log.error(str(e))
        sys.exit(1)

    if args.command == "validate":
        log.info(f"Config '{cfg.name}' is valid.")
        return

    try:
        if args.command == "run":
            run(cfg)
        elif args.command == "schedule":
            run_forever(cfg, args.every)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    except Exception as e:
        log.error(f"Scrape failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
