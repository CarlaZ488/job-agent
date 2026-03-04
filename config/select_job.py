import sqlite3
from datetime import datetime, timezone

DB_PATH = "database/jobs.db"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def pick_greenhouse_job(limit=25):
    con = sqlite3.connect(DB_PATH)

    rows = con.execute("""
        SELECT id, title, company, url, apply_url
        FROM jobs
        WHERE status <> 'archived'
          AND (apply_url LIKE '%greenhouse.io%' OR apply_url LIKE '%boards.greenhouse.io%')
          AND (availability_status IS NULL OR availability_status <> 'closed')
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    con.close()

    # Return newest candidate
    return rows[0] if rows else None

if __name__ == "__main__":
    job = pick_greenhouse_job()
    print(job)