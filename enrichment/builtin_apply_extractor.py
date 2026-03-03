import re
import urllib.parse

ATS_DOMAINS = [
    "boards.greenhouse.io",
    "greenhouse.io",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "icims.com",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "governmentjobs.com",
    "neogov.com",
]

def strip_tracking(url: str) -> str:
    """Remove common tracking params; keep the canonical page."""
    p = urllib.parse.urlparse(url)
    # Keep only path + query WITHOUT known tracking keys
    qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    drop = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","preference_id","i"}
    qs2 = [(k,v) for (k,v) in qs if k not in drop]
    return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, p.params, urllib.parse.urlencode(qs2), ""))

def find_first_ats_url(html: str) -> str | None:
    # Look for direct ATS URLs embedded anywhere
    # Handles escaped slashes too.
    pattern = r"https?://[^\s\"'<>]+"
    candidates = re.findall(pattern, html, flags=re.IGNORECASE)
    for u in candidates:
        ul = u.lower().replace("\\/", "/")
        if any(d in ul for d in ATS_DOMAINS):
            return ul
    return None

def find_builtin_apply_redirect(html: str, base_url: str) -> str | None:
    """
    BuiltIn sometimes links to /apply or /redirect endpoints.
    We’ll capture BuiltIn links that look like apply actions.
    """
    # Grab hrefs
    hrefs = re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)
    for h in hrefs:
        h2 = h.replace("&amp;", "&")
        abs_url = urllib.parse.urljoin(base_url, h2)
        ul = abs_url.lower()
        if "builtin.com" in ul and ("apply" in ul or "redirect" in ul):
            return abs_url
    return None

def extract_apply_url_from_builtin(html: str, page_url: str) -> str | None:
    """
    Returns either:
      - direct ATS URL (best)
      - BuiltIn apply redirect URL (still useful; your resolver can follow redirects)
    """
    # 1) Best: direct ATS url somewhere in page source
    ats = find_first_ats_url(html)
    if ats:
        return ats

    # 2) Next best: BuiltIn apply redirect link
    redir = find_builtin_apply_redirect(html, page_url)
    if redir:
        return redir

    return None