import os
import re
import json
import time
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from scoring.matcher import classify_track

from enrichment.builtin_apply_extractor import extract_apply_url_from_builtin, strip_tracking

ROOT = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(ROOT, "database", "jobs.db")
PROFILE_PATH = os.path.join(ROOT, "config", "job_profile.json")

USER_AGENT = "Mozilla/5.0 (JobAgent/1.0; +local-dev)"
SLEEP_SECONDS = 1.2  # polite rate limit


def unwrap_tracking_url(url: str) -> str:
    u = (url or "").strip()

    # Unwrap AWS L0 pattern
    m = re.search(r"/L\d+/(https:%2F%2F.+)$", u)
    if m:
        u = urllib.parse.unquote(m.group(1))

    # Canonicalize BuiltIn URLs
    if "builtin.com/job/" in u.lower():
        u = u.split("?", 1)[0]

    return u

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_html(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            return ""
        return resp.read().decode("utf-8", errors="replace")


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def extract_meta_property(html: str, prop: str) -> str:
    # og:title / og:site_name / etc.
    m = re.search(rf'<meta[^>]+property="{re.escape(prop)}"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    return clean_text(m.group(1)) if m else ""


def extract_meta_name(html: str, name: str) -> str:
    m = re.search(rf'<meta[^>]+name="{re.escape(name)}"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    return clean_text(m.group(1)) if m else ""


def extract_title_tag(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return clean_text(m.group(1)) if m else ""


def extract_json_ld(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html,
                         re.IGNORECASE | re.DOTALL):
        blob = m.group(1).strip()
        if not blob:
            continue
        # Some pages include multiple JSON-LD objects/arrays.
        try:
            data = json.loads(blob)
            if isinstance(data, dict):
                out.append(data)
            elif isinstance(data, list):
                out.extend([x for x in data if isinstance(x, dict)])
        except Exception:
            continue
    return out


def parse_job_from_jsonld(items: list[dict]) -> dict:
    """
    Best effort: find JobPosting schema.
    """
    for obj in items:
        t = str(obj.get("@type", "")).lower()
        if "jobposting" in t:
            title = clean_text(obj.get("title", ""))
            org = obj.get("hiringOrganization") or {}
            if isinstance(org, dict):
                company = clean_text(org.get("name", ""))
            else:
                company = ""
            loc = obj.get("jobLocation") or {}
            location_text = ""
            # jobLocation can be dict or list
            if isinstance(loc, list) and loc:
                loc = loc[0]
            if isinstance(loc, dict):
                addr = loc.get("address") or {}
                if isinstance(addr, dict):
                    locality = addr.get("addressLocality", "")
                    region = addr.get("addressRegion", "")
                    country = addr.get("addressCountry", "")
                    location_text = clean_text(", ".join([x for x in [locality, region, country] if x]))
            desc = clean_text(obj.get("description", ""))
            return {
                "title": title,
                "company": company,
                "location_text": location_text,
                "description": desc,
            }
    return {}


def heuristic_company_from_og_site(html: str) -> str:
    # Many job boards set og:site_name to company/job board name
    site = extract_meta_property(html, "og:site_name")
    return site


def enrich_url(url: str) -> dict:
    html = fetch_html(url)
    if not html:
        return {}

    builtin_apply = None
    builtin_source = None
    # If this is a BuiltIn job page, capture apply/source but continue full parsing.
    if "builtin.com/job/" in url.lower():
        builtin_apply = extract_apply_url_from_builtin(html, url)
        builtin_source = strip_tracking(url)

    # 1) Prefer JSON-LD JobPosting
    jsonld = extract_json_ld(html)
    parsed = parse_job_from_jsonld(jsonld)
    if parsed.get("title"):
        title = parsed.get("title", "")
        company = parsed.get("company", "")
        location_text = parsed.get("location_text", "")
        desc = parsed.get("description", "")
    else:
        # 2) Fallback: meta + title heuristics
        og_title = extract_meta_property(html, "og:title")
        title_tag = extract_title_tag(html)

        # BuiltIn + others often include good og:title
        title = og_title or title_tag

        # Try to split title into "Role - Company" patterns if needed
        company = heuristic_company_from_og_site(html)

        # Sometimes location exists in meta description
        meta_desc = extract_meta_name(html, "description")
        location_text = ""
        m = re.search(r"\b(Long Beach|Los Angeles|El Segundo|Westchester|California|CA)\b", meta_desc)
        if m:
            location_text = m.group(0)

        desc = meta_desc  # lightweight fallback, not full JD

    out = {
        "title": title,
        "company": company,
        "location_text": location_text,
        "description": desc,
    }
    if builtin_apply:
        out["apply_url"] = builtin_apply
    if builtin_source:
        out["source_url"] = builtin_source
    return out


def load_profile() -> dict:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def select_needs_enrichment(con: sqlite3.Connection, limit: int = 25):
    # Target “digest-ish” placeholders and missing fields
    return con.execute(
        """
        SELECT id, url, source_url, apply_url, title, company, location_text, description
        FROM jobs
        WHERE
          (title IS NULL OR title = '' OR title LIKE 'New %Job Matches%' OR title LIKE 'New %job matches%')
          OR company IS NULL OR company = ''
          OR location_text IS NULL OR location_text = ''
        ORDER BY scraped_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def update_job(con: sqlite3.Connection, job_id: int, fields: dict):
    cols = []
    vals = []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(job_id)
    con.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", vals)


def main(limit: int = 25):
    profile = load_profile()
    tracks = profile["tracks"]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")

    rows = select_needs_enrichment(con, limit=limit)
    if not rows:
        print("No jobs need enrichment right now.")
        con.close()
        return

    updated = 0
    for (job_id, url, source_url, apply_url, title, company, location_text, description) in rows:
        raw_target = apply_url or url or source_url
        target = unwrap_tracking_url(raw_target)

        # Only normalize BuiltIn tracking URLs, not real ATS URLs
        if raw_target and target and raw_target != target:
            if "builtin.com" in raw_target.lower():
                con.execute("UPDATE jobs SET url=? WHERE id=?", (target, job_id))
                con.commit()

        if not target:
            continue

        print(f"Enriching #{job_id}: {target}")
        try:
            data = enrich_url(target)
        except Exception as e:
            print(f"  - failed: {e}")
            continue

        # If enrichment returns empty / useless, skip
        new_title = clean_text(data.get("title", "")) or ""
        new_company = clean_text(data.get("company", "")) or ""
        new_loc = clean_text(data.get("location_text", "")) or ""
        new_desc = clean_text(data.get("description", "")) or ""

        # Only update if it improved something
        improved = {}
        if (not title) or ("job matches" in (title or "").lower()):
            if new_title and "job matches" not in new_title.lower():
                improved["title"] = new_title
        if not company and new_company:
            improved["company"] = new_company
        if not location_text and new_loc:
            improved["location_text"] = new_loc
        if (not description) and new_desc:
            improved["description"] = new_desc

        # Re-score if we now have better text
        score_text_title = improved.get("title", title) or ""
        score_text_desc = improved.get("description", description) or ""
        if score_text_title or score_text_desc:
            track, match_score = classify_track(score_text_title, score_text_desc, tracks)
            improved["track"] = track
            improved["match_score"] = float(match_score)

        if improved:
            # If enrichment extracted an apply_url, store it
            if data.get("apply_url"):
                improved["apply_url"] = data["apply_url"]

            # Optional: clean source_url
            if data.get("source_url"):
                improved["source_url"] = data["source_url"]

            improved["notes"] = clean_text((con.execute(
                "SELECT notes FROM jobs WHERE id=?", (job_id,)
            ).fetchone() or [""])[0] or "")

            update_job(con, job_id, improved)
            con.commit()
            updated += 1

        time.sleep(SLEEP_SECONDS)

    con.close()
    print(f"Done. Updated {updated} jobs.")


if __name__ == "__main__":
    main()
