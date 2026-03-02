# USAJOBS ingestion stub (API key required)
# Docs: https://developer.usajobs.gov/
import os, requests, sqlite3
from datetime import datetime
from scoring.matcher import classify_track
import json

ROOT = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(ROOT, "database", "jobs.db")
JOB_PROFILE_PATH = os.path.join(ROOT, "config", "job_profile.json")

def load_profile():
    with open(JOB_PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def upsert_job(job):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """INSERT OR IGNORE INTO jobs
           (source, external_id, url, title, company, location_text, work_mode, posted_date, scraped_at, description, track, match_score, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job.get("source"), job.get("external_id"), job.get("url"), job.get("title"), job.get("company"),
         job.get("location_text"), job.get("work_mode"), job.get("posted_date"), job.get("scraped_at"),
         job.get("description"), job.get("track"), job.get("match_score"), job.get("status","new"))
    )
    con.commit()
    con.close()

def search_usajobs(params):
    headers = {
        "Authorization-Key": os.environ["USAJOBS_API_KEY"],
        "User-Agent": os.environ["USAJOBS_USER_AGENT"],
        "Host": "data.usajobs.gov"
    }
    r = requests.get("https://data.usajobs.gov/api/Search", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def run():
    profile = load_profile()
    tracks = profile["tracks"]

    data = search_usajobs({
        "Keyword": "data engineer OR data analyst OR systems analyst OR programmer analyst",
        "LocationName": "Long Beach, California",
        "Radius": 25,
        "ResultsPerPage": 50
    })
    now = datetime.utcnow().isoformat()

    # NOTE: Parsing USAJOBS fields into our schema is TODO here.
    # We'll implement it once you confirm your preferred fed job families/series.
    print("Fetched USAJOBS results. Next step: normalize SearchResultItems into jobs table.")

if __name__ == "__main__":
    run()
