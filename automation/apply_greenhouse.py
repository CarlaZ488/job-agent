import os
import json
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "jobs.db"
ANSWER_BANK_PATH = ROOT / "config" / "answer_bank.json"

SCREENSHOT_DIR = ROOT / "artifacts"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def load_answers():
    with open(ANSWER_BANK_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_job(job_id: int):
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

def pick_resume_path(answers: dict, track: str) -> str:
    resumes = answers.get("resumes", {}) or {}
    track = (track or "unknown").lower()

    if track in resumes and resumes[track]:
        return resumes[track]

    # fallback order
    for key in ["default", "data", "it", "software"]:
        if resumes.get(key):
            return resumes[key]

    return ""

def is_greenhouse(url: str) -> bool:
    u = (url or "").lower()
    return "greenhouse.io" in u or "boards.greenhouse.io" in u

def fill_if_present(page, selector, value):
    if not value:
        return False
    loc = page.locator(selector)
    try:
        if loc.count() == 0:
            return False
        loc.first.fill(str(value))
        return True
    except Exception:
        return False

def upload_if_present(page, selector, filepath: str):
    if not filepath or not os.path.exists(filepath):
        return False
    loc = page.locator(selector)
    try:
        if loc.count() == 0:
            return False
        loc.first.set_input_files(filepath)
        return True
    except Exception:
        return False

def greenhouse_fill(job_id: int, resume_path: str, headless: bool = False):
    answers = load_answers()
    identity = answers.get("identity", {}) or {}
    job = get_job(job_id)

    full_name = (identity.get("full_name") or "").strip()
    parts = [p for p in full_name.split(" ") if p]
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    resume_path = resume_path or pick_resume_path(answers, job["track"])

    target = job["apply_url"] or job["url"]
    if not target:
        raise ValueError("No URL found for this job (url/apply_url both empty).")
    if not is_greenhouse(target):
        raise ValueError(f"Not a Greenhouse URL: {target}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print(f"Opening: {target}")
        page.goto(target, wait_until="domcontentloaded", timeout=60000)

        content = page.content().lower()
        closed_signals = [
            "job not found",
            "no longer available",
            "this job has expired",
            "the job you are looking for does not exist",
            "404"
        ]
        if any(s in content for s in closed_signals):
            # update DB
            import sqlite3
            from datetime import datetime, timezone
            con = sqlite3.connect("database/jobs.db")
            con.execute(
                "UPDATE jobs SET availability_status='closed', last_checked_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
            con.commit()
            con.close()
            raise ValueError("This Greenhouse posting appears closed/unavailable. Marked as closed in DB.")

        # Greenhouse application pages usually have #application or a form
        # Sometimes there is an "Apply" button first.
        try:
            apply_btn = page.get_by_role("button", name="Apply").first
            if apply_btn and apply_btn.is_visible():
                apply_btn.click()
        except Exception:
            pass

        # Wait for form fields to appear
        try:
            page.wait_for_selector("form", timeout=20000)
        except PWTimeout:
            pass

        # Common Greenhouse field ids/names
        full_name = identity.get("full_name", "")
        first_name = full_name.split(" ")[0] if full_name else ""
        last_name = " ".join(full_name.split(" ")[1:]) if full_name and " " in full_name else ""

        fill_if_present(page, "input#first_name", first_name)
        fill_if_present(page, "input#last_name", last_name)
        fill_if_present(page, "input#email", identity.get("email"))
        fill_if_present(page, "input#phone", identity.get("phone"))

        # Links (Greenhouse often uses these exact ids)
        fill_if_present(page, "input", identity.get("linkedin"))
        # Some forms label fields; we also try placeholder-based matching:
        for label, val in [
            ("LinkedIn", identity.get("linkedin")),
            ("GitHub", identity.get("github")),
            ("Portfolio", identity.get("portfolio")),
            ("Website", identity.get("portfolio") or identity.get("github") or identity.get("linkedin")),
        ]:
            if not val:
                continue
            try:
                page.get_by_label(label, exact=False).fill(val)
            except Exception:
                pass

        # Resume upload (common selector)
        uploaded = upload_if_present(page, "input#resume", resume_path)
        if not uploaded:
            # some forms use name="job_application[resume]"
            upload_if_present(page, "input[name='job_application[resume]']", resume_path)

        # Cover letter (optional)
        cover_path = answers.get("cover_letter_path", "")
        if cover_path:
            upload_if_present(page, "input#cover_letter", cover_path)

        # Best-effort custom questions:
        # Fill "Yes/No" or text fields based on Answer Bank mappings if you add them later.
        # For now, we just leave them for manual review.

        # Screenshot + stop
        shot = SCREENSHOT_DIR / f"greenhouse_job_{job_id}.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"Saved screenshot: {shot}")

        print("\n✅ READY TO SUBMIT (not submitted). Review the browser window.")
        print("Close the browser when you're done reviewing.\n")

        # Keep browser open for review
        page.wait_for_timeout(10_000_000)

        context.close()
        browser.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--job_id", type=int, required=True)
    parser.add_argument("--resume", type=str, default="", help="Optional override resume PDF path")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    greenhouse_fill(job_id=args.job_id, resume_path=args.resume, headless=args.headless)
