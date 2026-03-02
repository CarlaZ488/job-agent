import os
import re
import json
import base64
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Iterable

from scoring.matcher import classify_track
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If you already created ingestion/link_resolver.py, import it:
try:
    from ingestion.link_resolver import resolve_canonical_apply_url
except Exception:
    resolve_canonical_apply_url = None


ROOT = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(ROOT, "database", "jobs.db")
CREDS_PATH = os.path.join(ROOT, "credentials.json")
TOKEN_PATH = os.path.join(ROOT, "token.json")

# Read-only is enough for ingestion. (Least privilege.) :contentReference[oaicite:3]{index=3}
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# --- Tune these to your alert patterns ---
DEFAULT_QUERIES = [
    # LinkedIn job alerts often come from "jobs-noreply@linkedin.com" or similar
    'from:linkedin.com (subject:"Job alert" OR subject:"jobs") newer_than:14d',
    # BuiltIn varies; start broad then tighten after you see actual From/Subject
    '(builtin OR "Built In") (subject:jobs OR subject:"Job" OR subject:"alert") newer_than:14d',
]


URL_RE = re.compile(r"https?://[^\s<>()\"\']+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def ensure_tables():
    con = get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS gmail_processed (
          message_id TEXT PRIMARY KEY,
          processed_at TEXT NOT NULL
        );
    """)
    con.commit()
    con.close()


def is_processed(con: sqlite3.Connection, msg_id: str) -> bool:
    cur = con.execute("SELECT 1 FROM gmail_processed WHERE message_id = ?", (msg_id,))
    return cur.fetchone() is not None


def mark_processed(con: sqlite3.Connection, msg_id: str):
    con.execute(
        "INSERT OR REPLACE INTO gmail_processed (message_id, processed_at) VALUES (?, ?)",
        (msg_id, utc_now_iso()),
    )


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_PATH):
                raise FileNotFoundError(
                    f"Missing {CREDS_PATH}. Download OAuth desktop credentials and save as credentials.json."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def list_message_ids(service, query: str, max_results: int = 50) -> list[str]:
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    msgs = resp.get("messages", [])
    return [m["id"] for m in msgs]


def get_message(service, msg_id: str) -> dict:
    # "full" gives headers + body parts
    return service.users().messages().get(userId="me", id=msg_id, format="full").execute()


def get_headers(msg: dict) -> dict:
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h.get("value", "")
    return headers


def iter_text_parts(payload: dict) -> Iterable[str]:
    """
    Walk the Gmail payload and yield decoded text/plain and text/html parts.
    """
    stack = [payload]
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}

        data = body.get("data")
        if data and (mime.startswith("text/plain") or mime.startswith("text/html")):
            try:
                decoded = base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
                yield decoded
            except Exception:
                pass

        for child in part.get("parts", []) or []:
            stack.append(child)


def extract_urls_from_message(msg: dict) -> list[str]:
    urls = set()
    payload = msg.get("payload", {}) or {}

    for text in iter_text_parts(payload):
        for u in URL_RE.findall(text):
            # strip common trailing punctuation
            u = u.rstrip(").,]>\"'")
            urls.add(u)

    return sorted(urls)


def choose_best_job_url(urls: list[str]) -> Optional[str]:
    """
    Heuristics:
    - Prefer links that look like job postings / apply links
    - Prefer ATS domains (greenhouse/lever/workday/etc.)
    """
    if not urls:
        return None

    ats_hints = ["greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "myworkdayjobs.com",
                 "icims.com", "jobvite.com", "taleo.net", "successfactors", "smartrecruiters.com",
                 "bamboohr.com", "governmentjobs.com", "neogov.com"]
    job_hints = ["jobs", "careers", "job", "apply", "position", "opening"]

    scored = []
    for u in urls:
        ul = u.lower()
        score = 0
        if any(h in ul for h in ats_hints):
            score += 5
        if any(h in ul for h in job_hints):
            score += 2
        scored.append((score, u))

    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[0][1]


def insert_job(con: sqlite3.Connection, source: str, title: str, company: str,
               location_text: str, source_url: str, apply_url: str,
               track: Optional[str], match_score: Optional[float], posted_date: Optional[str] = None):
    # Use apply_url as the canonical URL if available, else source_url
    url = apply_url or source_url

    con.execute(
        """
        INSERT OR IGNORE INTO jobs
        (source, url, title, company, location_text, posted_date, scraped_at, source_url, apply_url, track, match_score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """,
        (source, url, title, company, location_text, posted_date, utc_now_iso(), source_url, apply_url, track, match_score),
    )


def parse_title_company_from_subject(subject: str) -> tuple[str, str]:
    """
    Very best-effort. You’ll refine once you see your actual alert formats.
    """
    s = (subject or "").strip()
    # Examples:
    # "Job alert: Data Analyst at Company"
    m = re.search(r"(?:job alert:?\s*)?(.*?)(?:\s+at\s+(.+))?$", s, re.IGNORECASE)
    if m:
        title = (m.group(1) or "Unknown Title").strip()
        company = (m.group(2) or "").strip()
        return title[:200], company[:200]
    return "Unknown Title", ""


def run(queries: Optional[list[str]] = None, max_per_query: int = 50):
    ensure_tables()
    service = get_gmail_service()
    queries = queries or DEFAULT_QUERIES
    profile_path = os.path.join(ROOT, "config", "job_profile.json")
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    tracks = profile["tracks"]

    con = get_db()
    try:
        for q in queries:
            msg_ids = list_message_ids(service, q, max_results=max_per_query)
            for msg_id in msg_ids:
                if is_processed(con, msg_id):
                    continue

                msg = get_message(service, msg_id)
                headers = get_headers(msg)
                subject = headers.get("subject", "")
                from_ = headers.get("from", "")
                date_ = headers.get("date", "")

                urls = extract_urls_from_message(msg)
                source_url = choose_best_job_url(urls) or ""
                apply_url = ""

                # If we have a resolver module, try to upgrade aggregator URLs to canonical ATS.
                if source_url and resolve_canonical_apply_url:
                    try:
                        resolved = resolve_canonical_apply_url(source_url)
                        apply_url = resolved or ""
                    except Exception:
                        apply_url = ""

                # Minimal metadata from subject; improve later with site-specific parsing.
                title, company = parse_title_company_from_subject(subject)
                track, match_score = classify_track(title, "", tracks)

                # You can optionally detect source from the From header.
                source = "gmail_alert"
                if "linkedin" in from_.lower():
                    source = "linkedin_email"
                elif "builtin" in from_.lower() or "built in" in from_.lower():
                    source = "builtin_email"

                # We don't reliably get location from alert emails; leave blank for now.
                insert_job(
                    con=con,
                    source=source,
                    title=title,
                    company=company,
                    location_text="",
                    source_url=source_url,
                    apply_url=apply_url,
                    track=track,
                    match_score=match_score,
                    posted_date=None,
                )

                mark_processed(con, msg_id)

        con.commit()
        print("Done. Inserted new jobs from Gmail alerts.")
    except HttpError as e:
        raise
    finally:
        con.close()


if __name__ == "__main__":
    run()
