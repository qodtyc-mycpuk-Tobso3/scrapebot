"""
ScrapeBot - standalone scraping & lead-extraction web app.

A separate project from MailForge - no shared code or database.

Deploy once (Railway free tier), then everything happens through the
browser: run jobs on demand, or turn on auto-scheduling and it scrapes
itself in the background, forever, with zero further involvement.
"""

import csv
import io
import json
import logging
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, redirect, render_template, request, url_for

import db
import leadbot_export
from hn_core import scrape_hn
from scraper_core import ScrapeConfig, run as run_scrape_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scrapebot")

app = Flask(__name__)
_scheduler_started = False
_scheduler_lock = threading.Lock()


# --------------------------------------------------------------------------
# Core job execution (shared by manual "Run Now" and the scheduler)
# --------------------------------------------------------------------------

def execute_job(job_name: str) -> None:
    job = db.get_job(job_name)
    if job is None:
        log.error(f"Unknown job: {job_name}")
        return

    log.info(f"Running job: {job_name}")
    try:
        if job["kind"] == "hn":
            rows = scrape_hn(pages=1)
        else:
            cfg = ScrapeConfig.load(job["config_path"])
            rows = run_scrape_config(cfg)
        db.record_run_result(job_name, rows, status="success")
        log.info(f"Job {job_name} succeeded: {len(rows)} rows")

        # Auto-extract leads after every successful run - zero extra clicks needed
        leads = leadbot_export.to_leads(rows)
        db.save_leads(job_name, leads)
        log.info(f"Job {job_name}: extracted {len(leads)} leads")

    except Exception as e:
        log.error(f"Job {job_name} failed: {e}")
        db.record_run_result(job_name, [], status="failed", error=str(e))


def _minutes_since(iso_timestamp: str) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


def _scheduler_loop() -> None:
    """Checks once a minute whether any job is due, and runs it. No external
    scheduling library required - simpler to reason about and one fewer
    dependency that can fail to install on a fresh deploy."""
    log.info("Background scheduler thread started")
    while True:
        try:
            for job in db.list_jobs():
                minutes = job["schedule_minutes"]
                if not minutes or minutes <= 0:
                    continue
                due = job["last_run"] is None or _minutes_since(job["last_run"]) >= minutes
                if due:
                    execute_job(job["name"])
        except Exception as e:
            log.error(f"Scheduler loop error: {e}")
        time.sleep(60)


def start_scheduler() -> None:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()
        _scheduler_started = True


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def dashboard():
    jobs = db.list_jobs()
    return render_template("dashboard.html", jobs=jobs)


@app.route("/run/<job_name>", methods=["GET", "POST"])
def run_now(job_name):
    execute_job(job_name)
    return redirect(url_for("dashboard"))


@app.route("/schedule/<job_name>", methods=["POST"])
def set_schedule(job_name):
    minutes = int(request.form.get("minutes", 0))
    db.set_schedule(job_name, minutes)
    return redirect(url_for("dashboard"))


@app.route("/results/<job_name>")
def results(job_name):
    job = db.get_job(job_name)
    items = db.latest_items(job_name)
    runs = db.recent_runs(job_name)
    columns = list(items[0].keys()) if items else []
    return render_template(
        "results.html", job=job, items=items, columns=columns, runs=runs
    )


@app.route("/download/<job_name>/<fmt>")
def download(job_name, fmt):
    items = db.latest_items(job_name, limit=10000)
    if not items:
        return "No data yet - run the job first.", 404

    if fmt == "json":
        buf = json.dumps(items, indent=2, ensure_ascii=False)
        return Response(
            buf, mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={job_name}.json"},
        )
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(items[0].keys()))
        writer.writeheader()
        writer.writerows(items)
        return Response(
            output.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={job_name}.csv"},
        )


@app.route("/leads/<job_name>")
def leads(job_name):
    job = db.get_job(job_name)
    lead_rows = db.get_leads(job_name)
    return render_template("leads.html", job=job, leads=lead_rows)


@app.route("/download_leads/<job_name>/<fmt>")
def download_leads(job_name, fmt):
    lead_rows = db.get_leads(job_name)
    if not lead_rows:
        return "No leads extracted yet - run the job first.", 404

    if fmt == "json":
        buf = json.dumps(lead_rows, indent=2, ensure_ascii=False)
        return Response(
            buf, mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={job_name}_leads.json"},
        )
    elif fmt == "vcard":
        lines = []
        for lead in lead_rows:
            lines.append("BEGIN:VCARD")
            lines.append("VERSION:3.0")
            if lead["name"]:
                lines.append(f"FN:{lead['name']}")
            if lead["company"]:
                lines.append(f"ORG:{lead['company']}")
            if lead["email"]:
                lines.append(f"EMAIL:{lead['email']}")
            if lead["phone"]:
                lines.append(f"TEL:{lead['phone']}")
            lines.append("END:VCARD")
        return Response(
            "\n".join(lines) + "\n", mimetype="text/vcard",
            headers={"Content-Disposition": f"attachment; filename={job_name}_leads.vcf"},
        )
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(lead_rows[0].keys()))
        writer.writeheader()
        writer.writerows(lead_rows)
        return Response(
            output.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={job_name}_leads.csv"},
        )


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
import os
if os.path.exists("scrapebot.db"):
    os.remove("scrapebot.db")

db.init_db()
start_scheduler()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
