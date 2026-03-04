import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "jobs.db"
ANSWER_BANK_PATH = ROOT / "config" / "answer_bank.json"
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)

def load_answers() -> dict:
    with open(ANSWER_BANK_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def pick_resume_path(answers: dict, track: str) -> str:
    resumes = (answers.get("resumes") or {})
    t = (track or "unknown").lower()
    if resumes.get(t):
        return resumes[t]
    for key in ["default", "data", "it", "software"]:
        if resumes.get(key):
            return resumes[key]
    return ""

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

def is_workday(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "myworkdayjobs.com" in host

def _name_parts(full_name: str):
    parts = [p for p in (full_name or "").strip().split(" ") if p]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:]) if len(parts) > 1 else ""

def workday_fill(job_id: int, headless: bool = False):
    answers = load_answers()
    identity = answers.get("identity", {}) or {}
    job = get_job(job_id)

    target = (job["apply_url"] or job["url"]).strip()
    if not target:
        raise ValueError("No url/apply_url found for this job.")
    if not is_workday(target):
        raise ValueError(f"Not a Workday URL: {target}")

    first_name, last_name = _name_parts(identity.get("full_name", ""))
    resume_path = pick_resume_path(answers, job["track"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        print(f"Opening: {target}")
        page.goto(target, wait_until="domcontentloaded", timeout=60000)

        # Workday often has an "Apply" button on the job page
        for name in ["Apply", "Apply Now", "Apply now"]:
            try:
                btn = page.get_by_role("button", name=name).first
                if btn and btn.is_visible():
                    btn.click()
                    break
            except Exception:
                pass

        # Wait a bit for the application flow to load
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass

        # ---- Best-effort field fills ----
        # Workday forms vary a lot; labels are more reliable than IDs.

        def fill_by_label(label_substr: str, value: str):
            if not value:
                return False
            try:
                loc = page.get_by_label(label_substr, exact=False)
                if loc.count() > 0:
                    loc.first.fill(value)
                    return True
            except Exception:
                return False
            return False

        fill_by_label("First Name", first_name)
        fill_by_label("Last Name", last_name)
        fill_by_label("Email", identity.get("email", ""))
        fill_by_label("Phone", identity.get("phone", ""))

        # Links (sometimes show as "LinkedIn Profile" / "Website" etc.)
        fill_by_label("LinkedIn", identity.get("linkedin", ""))
        fill_by_label("GitHub", identity.get("github", ""))
        fill_by_label("Website", identity.get("portfolio", "") or identity.get("github", "") or identity.get("linkedin", ""))

        # Resume upload: Workday varies; we try common patterns
        if resume_path:
            uploaded = False
            # try file input directly if present
            try:
                file_inputs = page.locator("input[type='file']")
                if file_inputs.count() > 0:
                    file_inputs.first.set_input_files(resume_path)
                    uploaded = True
            except Exception:
                pass

            # some flows have an "Upload" button that opens a chooser; we can't always control that
            if not uploaded:
                print("Resume upload not found via direct file input (may require clicking Upload in UI).")

        # Screenshot and pause (NO submit)
        shot = ARTIFACTS / f"workday_job_{job_id}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            print(f"Saved screenshot: {shot}")
        except Exception:
            pass

        print("\n✅ READY TO REVIEW (not submitted).")
        print("Please review the form in the browser, finish any required fields, then submit manually.\n")

        page.wait_for_timeout(10_000_000)
        context.close()
        browser.close()