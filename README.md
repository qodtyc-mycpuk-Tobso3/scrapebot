# ScrapeBot

A standalone scraping & lead-extraction web app. **Separate project from
MailForge** — no shared code, no shared database, own repo.

Deploy it once, then everything happens through a browser: no more running
commands in iSH. Turn on auto-scheduling for a job and it scrapes itself in
the background, forever, with zero further involvement from you.

## What it does

- Scrapes 4 sources out of the box: GitHub Trending, Books to Scrape, Quotes
  to Scrape, Hacker News front page.
- Every successful scrape automatically extracts leads (emails/phones found
  in the data) — no extra step.
- Dashboard shows last run status, row counts, and lets you trigger a run or
  turn on a schedule (every 15 min / 30 min / hour / 6 hours / daily).
- Download results and leads as CSV, JSON, or vCard, straight from the browser.
- A background thread checks every minute whether any scheduled job is due
  and runs it — no external scheduling service needed.

## Deploying to Railway (free tier)

You've used Railway before for NEXUS, so this should feel familiar:

1. Push this folder to a new GitHub repo (e.g. `scrapebot` — keep it separate
   from `MailForge`).
2. In Railway: **New Project → Deploy from GitHub repo** → select `scrapebot`.
3. Railway auto-detects the `Procfile` and installs `requirements.txt` — no
   extra config needed.
4. Once deployed, Railway gives you a public URL (e.g.
   `https://scrapebot-production.up.railway.app`) — bookmark it on your phone.
5. Open it in Safari. That's the whole setup.

**Important**: Railway's free tier can sleep the app after inactivity. If you
want scheduled jobs to truly run 24/7 unattended, either:
- Use Railway's paid "always on" tier (a few dollars/month), or
- Ping the app's dashboard URL periodically from an external uptime monitor
  (e.g. UptimeRobot, free) — that keeps it awake AND doubles as a health check.

## Running locally first (optional, to see it work before deploying)

```bash
pip install -r requirements.txt
python3 app.py
```
Then open `http://localhost:5000` in a browser.

## How scheduling actually works

No external library (no APScheduler, no cron) — just a plain Python
background thread that wakes up once a minute, checks each job's
`schedule_minutes` against its `last_run` timestamp in the database, and
runs anything that's due. Simpler to reason about, fewer dependencies that
can fail on a fresh deploy, and one worker process is all that's needed
(the `Procfile` intentionally uses `--workers 1` so the schedule doesn't
run in duplicate across multiple processes).

## File overview

| File | Purpose |
|---|---|
| `app.py` | Flask app, routes, background scheduler thread |
| `db.py` | SQLite persistence (jobs, runs, scraped items, leads) |
| `scraper_core.py` | The scraping engine (config-driven, retries, robots.txt, etc.) |
| `hn_core.py` | Dedicated Hacker News scraper (its markup needs custom handling) |
| `leadbot_export.py` | Extracts emails/phones from scraped rows into lead records |
| `configs/*.json` | Site definitions (selectors, pagination, etc.) |
| `templates/` | Dashboard / results / leads pages |

## Adding a new site to scrape

Add a JSON config to `configs/` (see existing ones for the format), then
register it in `db.py`'s `DEFAULT_JOBS` list with a name, label, and path.
Restart the app — it'll pick up the new job automatically.

## Legal/ethical notes

Same as before: robots.txt is respected by default, there's a polite delay
between paginated requests, and this shouldn't be pointed at sites requiring
login/authorization you don't have, or used to scrape and resell personal
data.
