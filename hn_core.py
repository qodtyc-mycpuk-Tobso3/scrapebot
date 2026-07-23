#!/usr/bin/env python3
"""
Hacker News front-page scraper.

HN's HTML puts each story's title in one <tr class="athing"> and its
score/author/comments in the *next* sibling <tr>. That doesn't fit the
generic single-selector config format in scraper.py, so this is a small
standalone script that reuses the same fetch/save helpers.

Usage:
    python3 hn_scraper.py                # front page, save to hn.csv
    python3 hn_scraper.py --pages 3       # first 3 pages
"""

import argparse
import re

from bs4 import BeautifulSoup

from scraper_core import fetch_static, save


def scrape_hn(pages: int = 1) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for page_num in range(1, pages + 1):
        url = "https://news.ycombinator.com/news" + (
            f"?p={page_num}" if page_num > 1 else ""
        )
        print(f"[hn] fetching page {page_num}: {url}")
        html = fetch_static(url)
        soup = BeautifulSoup(html, "lxml")

        for title_row in soup.select("tr.athing"):
            title_link = title_row.select_one("span.titleline a")
            if not title_link:
                continue
            site_span = title_row.select_one("span.sitestr")

            subtext_row = title_row.find_next_sibling("tr")
            subtext = subtext_row.select_one("td.subtext") if subtext_row else None

            points = ""
            author = ""
            age = ""
            comments = ""
            if subtext:
                score_span = subtext.select_one("span.score")
                points = score_span.get_text(strip=True).replace(" points", "") if score_span else "0"
                author_link = subtext.select_one("a.hnuser")
                author = author_link.get_text(strip=True) if author_link else ""
                age_span = subtext.select_one("span.age")
                age = age_span.get_text(strip=True) if age_span else ""
                # comments link is the last <a> in subtext
                links = subtext.select("a")
                comments_text = links[-1].get_text(strip=True) if links else ""
                m = re.search(r"(\d+)", comments_text)
                comments = m.group(1) if m else "0"

            rows.append(
                {
                    "title": title_link.get_text(strip=True),
                    "url": title_link.get("href", ""),
                    "site": site_span.get_text(strip=True) if site_span else "",
                    "points": points,
                    "author": author,
                    "age": age,
                    "comments": comments,
                }
            )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Hacker News front page")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages to scrape")
    parser.add_argument("--output", default="hn.csv", help="Output file (.csv or .json)")
    args = parser.parse_args()

    rows = scrape_hn(args.pages)
    save(rows, args.output)
    print(f"Saved {len(rows)} stories -> {args.output}")


if __name__ == "__main__":
    main()
