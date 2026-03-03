import sqlite3
from datetime import datetime, timezone

DB_PATH = "database/jobs.db"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def choose_canonical(rows):
    """
    rows: list of dict-like tuples for same apply_url.
    Pick the best record to keep:
      - prefer non-empty title/company/location/description
      - prefer statuses beyond 'new'
      - otherwise keep the oldest (lowest id)
    """
    status_rank = {
        "offer": 6, "interview": 5, "applied": 4, "applying": 3,
        "queued": 2, "new": 1, "rejected": 0, "archived": -1
    }

    def score(r):
        (job_id, title, company, location_text, description, status, scraped_at, url, source_url) = r
        s = 0
        s += 3 if title else 0
        s += 3 if company else 0
        s += 2 if location_text else 0
        s += 2 if description else 0
        s += status_rank.get((status or "").lower(), 0) * 5
        # prefer builtin url as discovery if present
        s += 1 if (url and "builtin.com/job/" in url.lower()) else 0
        # prefer having source_url too
        s += 1 if source_url else 0
        # older id as mild tie-breaker (keep earlier inserted)
        s += max(0, 100000 - int(job_id)) * 0.000001
        return s

    return max(rows, key=score)

def main(dry_run=True):
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")

    # Find apply_urls with duplicates
    dup_apply_urls = con.execute("""
        SELECT apply_url, COUNT(*)
        FROM jobs
        WHERE apply_url IS NOT NULL AND apply_url <> ''
          AND status <> 'archived'
        GROUP BY apply_url
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
    """).fetchall()

    if not dup_apply_urls:
        print("No duplicates found by apply_url.")
        con.close()
        return

    total_groups = len(dup_apply_urls)
    print(f"Found {total_groups} duplicate apply_url groups.")

    changes = 0
    for (apply_url, cnt) in dup_apply_urls:
        rows = con.execute("""
            SELECT id, title, company, location_text, description, status, scraped_at, url, source_url
            FROM jobs
            WHERE apply_url = ?
              AND status <> 'archived'
            ORDER BY id ASC
        """, (apply_url,)).fetchall()

        canonical = choose_canonical(rows)
        canonical_id = canonical[0]

        dup_ids = [r[0] for r in rows if r[0] != canonical_id]

        print(f"\napply_url: {apply_url}")
        print(f"  keep: #{canonical_id}  | dups: {dup_ids}")

        if dry_run:
            continue

        # Archive duplicates, point them to canonical
        for dup_id in dup_ids:
            con.execute("""
                UPDATE jobs
                SET status='archived',
                    canonical_job_id=?,
                    dedup_reason=?,
                    notes=TRIM(
                        COALESCE(notes, '') ||
                        CASE WHEN COALESCE(notes, '') = '' THEN '' ELSE '\n' END ||
                        ?
                    )
                WHERE id=?
            """, (canonical_id, "duplicate_apply_url", f"[dedupe] duplicate of job_id={canonical_id}", dup_id))
            changes += 1

    if not dry_run:
        con.commit()
        print(f"\nDone. Archived {changes} duplicate rows.")
    else:
        print("\nDry run only. Re-run with dry_run=False to apply changes.")

    con.close()

if __name__ == "__main__":
    # first run should be dry run
    main(dry_run=False)
