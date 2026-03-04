import argparse
import sqlite3
from urllib.parse import urlparse
from pathlib import Path

from automation.apply_greenhouse import greenhouse_fill

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "jobs.db"

def get_job(job_id: int) -> dict:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, title, company, url, apply_url, track FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    con.close()
    if not row:
        raise ValueError(f"Job id {job_id} not found")

    return {
        "id": row[0],
        "title": row[1] or "",
        "company": row[2] or "",
        "url": row[3] or "",
        "apply_url": row[4] or "",
        "track": row[5] or "unknown",
    }

def pick_target(job: dict) -> str:
    return (job.get("apply_url") or job.get("url") or "").strip()

def detect_ats(url: str) -> str:
    u = (url or "").lower()
    host = urlparse(url).netloc.lower()

    if "boards.greenhouse.io" in host or "greenhouse.io" in host:
        return "greenhouse"
    if "jobs.lever.co" in host or "lever.co" in host:
        return "lever"
    if "myworkdayjobs.com" in host:
        return "workday"
    if "governmentjobs.com" in host or "neogov.com" in host:
        return "governmentjobs"
    if "builtin.com" in host:
        return "builtin"
    if "linkedin.com" in host:
        return "linkedin"

    return "unknown"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job_id", type=int, required=True)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    job = get_job(args.job_id)
    target = pick_target(job)
    if not target:
        raise ValueError("No url/apply_url found for this job.")

    ats = detect_ats(target)
    print(f"Job #{job['id']} | {job['title']} @ {job['company']}")
    print(f"Target: {target}")
    print(f"Detected ATS: {ats}")

    if ats == "greenhouse":
        from automation.apply_greenhouse import greenhouse_fill
        # resume_path is auto-selected inside apply_greenhouse (Option C)
        greenhouse_fill(job_id=job["id"], resume_path="", headless=args.headless)
        return

    # Not implemented yet, but routed cleanly
    if ats in {"lever", "governmentjobs"}:
        print(f"\n⚠️ Handler for {ats} not implemented yet.")
        print("Next step: we’ll add autofill for this ATS.\n")
        return

    if ats == "builtin":
        print("\n⚠️ This is a BuiltIn listing, not the company ATS.")
        print("Run enrichment again to try extracting apply_url.\n")
        return
    
    if ats == "workday":
        from automation.apply_workday import workday_fill
        workday_fill(job_id=job["id"], headless=args.headless)
        return

    print("\n⚠️ Unknown ATS. Leaving job unchanged.\n")

if __name__ == "__main__":
    main()