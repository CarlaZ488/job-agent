import re
import urllib.request
import urllib.parse
from html.parser import HTMLParser

APPLY_HINTS = [
    "apply", "application", "careers", "job", "jobs", "greenhouse", "lever",
    "workday", "icims", "jobvite", "taleo", "successfactors", "neogov",
    "governmentjobs", "smartrecruiters", "bamboohr"
]

AGGREGATOR_DOMAINS = {
    "builtin.com",
}

ATS_DOMAINS = [
    "greenhouse.io",
    "boards.greenhouse.io",
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

def _same_or_subdomain(domain: str, host: str) -> bool:
    return host == domain or host.endswith("." + domain)

def _looks_like_apply_url(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in APPLY_HINTS)

def _is_ats_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(_same_or_subdomain(d, host) for d in ATS_DOMAINS)

class _LinkCollector(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = None
        for k, v in attrs:
            if k.lower() == "href" and v:
                href = v
                break
        if not href:
            return
        abs_url = urllib.parse.urljoin(self.base_url, href)
        self.links.append(abs_url)

def fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (JobAgent/1.0; +https://github.com/yourname/job-agent)",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype:
            return ""
        return resp.read().decode("utf-8", errors="replace")

def resolve_final_url(url: str, timeout: int = 15) -> str:
    """
    Follow redirects to get the final landing URL.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (JobAgent/1.0)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl()

def resolve_canonical_apply_url(source_url: str) -> str | None:
    """
    Best-effort:
    - Follow redirects
    - Parse page links and choose the best 'apply' link, preferring ATS domains
    """
    try:
        final_url = resolve_final_url(source_url)
    except Exception:
        final_url = source_url

    # If it already lands on an ATS, we're done.
    if _is_ats_url(final_url):
        return final_url

    # Fetch HTML and extract links
    try:
        html = fetch_html(final_url)
    except Exception:
        html = ""

    if not html:
        return None

    collector = _LinkCollector(final_url)
    collector.feed(html)

    # Score links
    scored: list[tuple[int, str]] = []
    for link in collector.links:
        # skip mailto, etc.
        if link.startswith("mailto:") or link.startswith("tel:"):
            continue
        score = 0
        if _looks_like_apply_url(link):
            score += 3
        if _is_ats_url(link):
            score += 5
        # prefer same-site "apply" if it’s clearly a job page
        host = urllib.parse.urlparse(link).netloc.lower()
        final_host = urllib.parse.urlparse(final_url).netloc.lower()
        if host == final_host:
            score += 1
        scored.append((score, link))

    scored.sort(reverse=True, key=lambda x: x[0])

    # Return the highest scoring link that looks plausible
    for score, link in scored[:25]:
        if score >= 5:  # ATS link strongly preferred
            return link
    for score, link in scored[:25]:
        if score >= 3:  # fallback: apply-ish link
            return link

    return None