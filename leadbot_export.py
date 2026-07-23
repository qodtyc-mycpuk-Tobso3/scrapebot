#!/usr/bin/env python3
"""
Converts generic scraper output into LeadBot Pro's lead format.

IMPORTANT: I don't have access to LeadBot Pro's actual source files in this
session (they were built in an earlier conversation and weren't uploaded
here), so this can't literally be "wired in" to that codebase directly.
What this does instead: reads any CSV/JSON produced by scraper.py or
hn_scraper.py, pulls out emails/phones/names via regex (same approach as
LeadBot Pro's contact extractor tab), and writes output in the same three
formats LeadBot Pro exports (CSV, JSON, vCard) so you can import it there
with zero manual reformatting.

Usage:
    python3 leadbot_export.py scraped_data.csv --format csv
    python3 leadbot_export.py scraped_data.json --format vcard --output leads.vcf
"""

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("leadbot_export")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Matches common US/international formats: (555) 123-4567, 555-123-4567, +1 555 123 4567
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def load_rows(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    if path.endswith(".json"):
        data = json.loads(p.read_text())
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON array of records")
        return data
    else:
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def extract_lead(row: dict, source_url: str = "") -> dict | None:
    """Pull a lead-shaped record out of any scraped row. Returns None if the
    row has no extractable contact info (so we don't produce junk leads)."""
    blob = " ".join(str(v) for v in row.values() if v)

    emails = EMAIL_RE.findall(blob)
    phones = PHONE_RE.findall(blob)

    if not emails and not phones:
        return None  # no contact info - not a usable lead

    # Best-effort name/company guess: look for common field names first
    name = row.get("name") or row.get("title") or row.get("author") or ""
    company = row.get("company") or row.get("organization") or ""

    return {
        "name": name.strip(),
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "company": company.strip(),
        "source_url": row.get("url") or row.get("repo_url") or source_url,
        "notes": blob[:200],  # truncated context for the leads table
    }


def to_leads(rows: list[dict], source_url: str = "") -> list[dict]:
    leads = []
    skipped = 0
    for row in rows:
        lead = extract_lead(row, source_url)
        if lead:
            leads.append(lead)
        else:
            skipped += 1
    if skipped:
        log.info(f"Skipped {skipped} row(s) with no email or phone found")
    return leads


def save_csv(leads: list[dict], path: str) -> None:
    if not leads:
        log.warning("No leads to save")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(leads[0].keys()))
        writer.writeheader()
        writer.writerows(leads)


def save_json(leads: list[dict], path: str) -> None:
    Path(path).write_text(json.dumps(leads, indent=2, ensure_ascii=False))


def save_vcard(leads: list[dict], path: str) -> None:
    """Standard .vcf format - importable into Contacts, LeadBot Pro, most CRMs."""
    lines = []
    for lead in leads:
        lines.append("BEGIN:VCARD")
        lines.append("VERSION:3.0")
        if lead["name"]:
            lines.append(f"FN:{lead['name']}")
            lines.append(f"N:{lead['name']};;;;")
        if lead["company"]:
            lines.append(f"ORG:{lead['company']}")
        if lead["email"]:
            lines.append(f"EMAIL:{lead['email']}")
        if lead["phone"]:
            lines.append(f"TEL:{lead['phone']}")
        if lead["source_url"]:
            lines.append(f"URL:{lead['source_url']}")
        if lead["notes"]:
            # vCard NOTE fields need literal newlines escaped
            lines.append(f"NOTE:{lead['notes'].replace(chr(10), ' ')}")
        lines.append("END:VCARD")
    Path(path).write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert scraped data into LeadBot Pro lead format")
    parser.add_argument("input", help="Path to scraped .csv or .json file")
    parser.add_argument("--format", choices=["csv", "json", "vcard"], default="csv")
    parser.add_argument("--output", help="Output path (default: leads.<format>)")
    parser.add_argument("--source-url", default="", help="Fallback source URL if rows have none")
    args = parser.parse_args()

    try:
        rows = load_rows(args.input)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        log.error(str(e))
        sys.exit(1)

    leads = to_leads(rows, args.source_url)
    if not leads:
        log.warning(
            "No leads extracted - none of the scraped rows contained an "
            "email or phone number. Nothing was written."
        )
        sys.exit(0)

    ext = {"csv": "csv", "json": "json", "vcard": "vcf"}[args.format]
    output = args.output or f"leads.{ext}"

    if args.format == "csv":
        save_csv(leads, output)
    elif args.format == "json":
        save_json(leads, output)
    else:
        save_vcard(leads, output)

    log.info(f"Extracted {len(leads)} lead(s) -> {output}")


if __name__ == "__main__":
    main()
