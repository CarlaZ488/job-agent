import json
import sqlite3
import re
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "jobs.db"
ANSWER_BANK_PATH = ROOT / "config" / "answer_bank.json"
WORKDAY_TENANT_ACCOUNTS_PATH = ROOT / "config" / "workday_tenant_accounts.json"
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

def visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False

def click_if_visible(locator, label: str) -> bool:
    try:
        if locator.count() > 0 and locator.first.is_visible():
            locator.first.scroll_into_view_if_needed()
            try:
                locator.first.click(timeout=3000)
            except Exception:
                locator.first.click(timeout=3000, force=True)
            print(f"[debug] clicked: {label}")
            return True
    except Exception:
        pass
    return False

def fill_if_present_by_label(page, label_regex, value) -> bool:
    try:
        loc = page.get_by_label(label_regex)
        if loc.count() == 0:
            return False
        el = loc.first
        el.scroll_into_view_if_needed()
        el.fill(value)
        return True
    except Exception:
        return False
  
def workday_tenant(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def load_workday_tenant_accounts() -> dict:
    default = {"tenants": {}}
    if not WORKDAY_TENANT_ACCOUNTS_PATH.exists():
        return default
    try:
        with open(WORKDAY_TENANT_ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        tenants = data.get("tenants")
        if not isinstance(tenants, dict):
            data["tenants"] = {}
        return data
    except Exception as e:
        print(f"[warn] failed to read {WORKDAY_TENANT_ACCOUNTS_PATH}: {e}")
        return default

def save_workday_tenant_accounts(data: dict):
    try:
        WORKDAY_TENANT_ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(WORKDAY_TENANT_ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[warn] failed to write {WORKDAY_TENANT_ACCOUNTS_PATH}: {e}")

def tenant_has_known_account(tenant: str, email: str = "") -> bool:
    if not tenant:
        return False
    data = load_workday_tenant_accounts()
    rec = (data.get("tenants") or {}).get(tenant)
    if not isinstance(rec, dict):
        return False
    rec_email = str(rec.get("email", "")).strip().lower()
    if rec_email and email and rec_email != email.strip().lower():
        return False
    return bool(rec.get("has_account", True))

def remember_tenant_account(tenant: str, email: str, source: str):
    if not tenant:
        return
    data = load_workday_tenant_accounts()
    tenants = data.setdefault("tenants", {})
    rec = tenants.get(tenant)
    if not isinstance(rec, dict):
        rec = {}

    now = utc_now_iso()
    if "first_seen" not in rec:
        rec["first_seen"] = now
    rec["last_seen"] = now
    rec["has_account"] = True
    rec["last_flow"] = source
    if email:
        rec["email"] = email

    tenants[tenant] = rec
    save_workday_tenant_accounts(data)

def resolve_workday_auth_inputs(answers: dict) -> tuple[str, str, str]:
    identity = (answers.get("identity") or {})
    workday_account = (answers.get("workday_account") or {})
    configured_email = str(identity.get("email", "")).strip()
    configured_default_password = str(
        workday_account.get("default_password") or identity.get("workday_password", "")
    ).strip()
    configured_sign_in_password = str(
        workday_account.get("sign_in_password") or configured_default_password
    ).strip()
    configured_create_password = str(
        workday_account.get("create_password") or configured_default_password
    ).strip()
    wd_email = os.getenv("JOBAGENT_WORKDAY_EMAIL", "").strip() or configured_email

    sign_in_password = os.getenv("JOBAGENT_WORKDAY_PASSWORD", "").strip() or configured_sign_in_password
    create_password = (
        os.getenv("JOBAGENT_WORKDAY_CREATE_PASSWORD", "").strip()
        or configured_create_password
        or sign_in_password
    )
    return wd_email, sign_in_password, create_password

def _fill_first_visible(locator, value: str) -> bool:
    if not value:
        return False
    try:
        count = min(locator.count(), 8)
    except Exception:
        return False
    for i in range(count):
        try:
            el = locator.nth(i)
            if not el.is_visible():
                continue
            el.scroll_into_view_if_needed()
            el.fill(value)
            return True
        except Exception:
            continue
    return False

def _fill_email_input(page, value: str) -> bool:
    if not value:
        return False
    candidates = []
    try:
        candidates.append(page.get_by_label(re.compile(r"Email Address|Email|Username", re.I)))
    except Exception:
        pass
    try:
        candidates.append(page.locator("input[type='email']"))
        candidates.append(page.locator("input[name*='email']"))
        candidates.append(page.locator("input[id*='email']"))
    except Exception:
        pass
    for c in candidates:
        if _fill_first_visible(c, value):
            return True
    return False

def _pick_sign_in_scope(page):
    """
    Prefer the active sign-in dialog/form scope when present to avoid filling
    hidden create-account inputs that remain in the DOM.
    """
    scope_candidates = [
        page.get_by_role("dialog"),
        page.locator("[role='dialog']"),
        page.locator("[aria-modal='true']"),
        page.locator("form:has(button:has-text('Sign In'))"),
        page.locator("div:has(button:has-text('Sign In'))"),
    ]

    for group in scope_candidates:
        try:
            count = min(group.count(), 6)
        except Exception:
            continue
        for i in range(count):
            try:
                scope = group.nth(i)
                if not scope.is_visible():
                    continue
                has_signin_cta = (
                    scope.get_by_role("button", name=re.compile(r"^\s*Sign In\s*$", re.I)).count() > 0
                    or scope.locator("button:has-text('Sign In')").count() > 0
                )
                has_password = scope.locator("input[type='password']").count() > 0
                if has_signin_cta and has_password:
                    return scope
            except Exception:
                continue
    return page

def _fill_password_input(page, value: str, index: int = 0) -> bool:
    if not value:
        return False
    try:
        pw = page.locator("input[type='password']")
        if pw.count() > index:
            el = pw.nth(index)
            if el.is_visible():
                el.scroll_into_view_if_needed()
                el.fill(value)
                return True
    except Exception:
        pass
    return False

def _visible_value_len(locator) -> int:
    try:
        count = min(locator.count(), 8)
    except Exception:
        return 0
    for i in range(count):
        try:
            el = locator.nth(i)
            if not el.is_visible():
                continue
            v = el.input_value() or ""
            return len(v.strip())
        except Exception:
            continue
    return 0

def debug_auth_shot(page, tag: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shot = ARTIFACTS / f"workday_auth_{tag}_{ts}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        print(f"[debug] auth screenshot: {shot}")
    except Exception:
        pass

def _has_verify_password_field(page) -> bool:
    candidates = [
        page.get_by_label(re.compile(r"Verify New Password|Confirm Password", re.I)),
        page.get_by_text(re.compile(r"Verify New Password|Confirm Password", re.I)),
    ]
    for c in candidates:
        if visible(c):
            return True
    return False

def is_create_account_page(page) -> bool:
    """
    Workday create-account step.
    URL often stays the same, so detection is strictly DOM-based.
    """
    try:
        has_verify = _has_verify_password_field(page)
        has_two_passwords = page.locator("input[type='password']").count() >= 2
        has_create_cta = visible(page.get_by_role("button", name=re.compile(r"Create Account", re.I)))
        has_heading = visible(page.get_by_role("heading", name=re.compile(r"Create Account", re.I)))
        return (has_verify or has_two_passwords) and (has_create_cta or has_heading)
    except Exception:
        return False

def is_sign_in_ui(page) -> bool:
    try:
        has_email = (
            page.get_by_label(re.compile(r"Email Address|Email|Username", re.I)).count() > 0
            or page.locator("input[type='email']").count() > 0
            or page.locator("input[name*='email']").count() > 0
        )
        has_password = page.locator("input[type='password']").count() >= 1
        has_sign_in_button = (
            visible(page.get_by_role("button", name=re.compile(r"^\s*Sign In\s*$", re.I)))
            or visible(page.locator("button:has-text('Sign In')"))
        )
        has_verify = _has_verify_password_field(page) or page.locator("input[type='password']").count() >= 2
        return has_email and has_password and has_sign_in_button and (not has_verify)
    except Exception:
        return False

def click_sign_in_link_from_create_account(page) -> bool:
    """On Create Account page, click the Sign In link and wait for DOM transition."""
    candidates = [
        page.get_by_role("link", name=re.compile(r"^\s*Sign In\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*Sign In\s*$", re.I)),
        page.locator("a:has-text('Sign In')"),
        page.locator("button:has-text('Sign In')"),
        page.locator("[role='button']:has-text('Sign In')"),
        page.locator("text=/Already have an account\\?/i").locator("xpath=..").locator("a,button,[role='button']"),
        page.get_by_text(re.compile(r"^\s*Sign In\s*$", re.I)),
    ]
    for link in candidates:
        try:
            if link.count() == 0:
                continue
            count = min(link.count(), 4)
            for i in range(count):
                el = link.nth(i)
                if not el.is_visible():
                    continue
                el.scroll_into_view_if_needed()
                try:
                    el.click(timeout=4000)
                except Exception:
                    try:
                        el.click(timeout=4000, force=True)
                    except Exception:
                        continue
                page.wait_for_timeout(300)
                waited = 0
                while waited < 6000:
                    if is_sign_in_ui(page):
                        return True
                    page.wait_for_timeout(250)
                    waited += 250
        except Exception:
            continue
    return is_sign_in_ui(page)

def is_past_auth_step(page) -> bool:
    """
    Only return True when we see real form controls from downstream steps,
    not just the stepper labels.
    """
    candidates = [
        page.get_by_label(re.compile(r"First Name|Last Name|Legal Name|Preferred Name", re.I)),
        page.get_by_label(re.compile(r"Address|City|State|Postal|Zip", re.I)),
        page.get_by_label(re.compile(r"Phone", re.I)),
        page.locator("input[type='file']"),
        page.get_by_role("button", name=re.compile(r"Upload Resume|Upload|Select files|Attach", re.I)),
        page.locator("text=/Drop\\s+files?\\s+here/i"),
        page.locator("text=/Drop\\s+file\\s+here/i"),
        page.locator("[data-automation-id*='drop' i]"),
        page.locator("[class*='dropzone' i]"),
    ]
    for c in candidates:
        if visible(c):
            return True
    return False

def detect_auth_state(page) -> str:
    if is_create_account_page(page):
        return "create_account"
    if is_sign_in_ui(page):
        return "sign_in"
    if is_past_auth_step(page):
        return "ready"
    return "unknown"

def wait_for_auth_state(page, timeout_ms: int = 7000) -> str:
    waited = 0
    while waited <= timeout_ms:
        state = detect_auth_state(page)
        if state != "unknown":
            return state
        page.wait_for_timeout(250)
        waited += 250
    return "unknown"

def find_visible_text(page, patterns: list[str]) -> str:
    for pattern in patterns:
        try:
            loc = page.get_by_text(re.compile(pattern, re.I))
            if loc.count() > 0 and loc.first.is_visible():
                return pattern
        except Exception:
            continue
    return ""

def workday_sign_in_if_needed(page, wd_email: str, wd_pass: str) -> bool:
    """
    Attempts sign-in if sign-in UI is visible.
    Returns True only after we detect we moved past auth.
    """
    state = detect_auth_state(page)
    if state == "ready":
        return True
    if state != "sign_in":
        return False

    print(f"[debug] sign-in credentials set: email={bool(wd_email)} password={bool(wd_pass)}")
    if not wd_email or not wd_pass:
        print("[warn] Missing JOBAGENT_WORKDAY_EMAIL and/or JOBAGENT_WORKDAY_PASSWORD.")
        return False

    scope = _pick_sign_in_scope(page)
    print(f"[debug] sign-in scope={'page' if scope == page else 'modal/form'}")
    debug_auth_shot(page, "signin_before_fill")

    ok_email = _fill_email_input(scope, wd_email) or fill_if_present_by_label(scope, re.compile(r"Email Address|Email|Username", re.I), wd_email)
    ok_pass = _fill_password_input(scope, wd_pass, index=0) or fill_if_present_by_label(scope, re.compile(r"Password", re.I), wd_pass)
    print(f"[debug] filled sign-in email={ok_email} password={ok_pass}")
    if not ok_email or not ok_pass:
        return False

    email_len = _visible_value_len(
        scope.locator("input[type='email'], input[name*='email'], input[id*='email'], input[type='text']")
    )
    pw_len = _visible_value_len(scope.locator("input[type='password']"))
    print(f"[debug] visible sign-in input lengths: email={email_len} password={pw_len}")
    debug_auth_shot(page, "signin_after_fill")

    clicked = False
    btn_candidates = [
        scope.get_by_role("button", name=re.compile(r"^\s*Sign In\s*$", re.I)),
        scope.locator("button:has-text('Sign In')"),
        page.get_by_role("button", name=re.compile(r"^\s*Sign In\s*$", re.I)),
        page.locator("button:has-text('Sign In')"),
    ]
    for b in btn_candidates:
        try:
            if b.count() == 0:
                continue
            el = b.first
            if not el.is_visible():
                continue
            el.scroll_into_view_if_needed()
            try:
                el.click(timeout=5000)
            except Exception:
                el.click(timeout=5000, force=True)
            clicked = True
            print("[debug] clicked Sign In button")
            break
        except Exception:
            continue

    if not clicked:
        print("[warn] could not click Sign In button")
        return False

    debug_auth_shot(page, "signin_after_click")
    page.wait_for_timeout(800)
    err = find_visible_text(page, [
        r"Invalid",
        r"incorrect",
        r"Unable to sign in",
        r"Try again",
        r"Account locked",
        r"too many attempts",
    ])
    if err:
        print(f"[warn] sign-in error visible: {err}")
        return False

    waited = 0
    while waited < 30000:
        state = detect_auth_state(page)
        if state == "ready":
            print("[debug] sign-in succeeded")
            return True
        if state == "create_account":
            print("[warn] bounced to Create Account after sign-in attempt")
            return False
        page.wait_for_timeout(250)
        waited += 250

    if is_past_auth_step(page):
        print("[debug] sign-in fallback success: reached post-auth UI")
        return True

    print("[warn] sign-in UI did not advance")
    return False

def workday_create_account_if_needed(page, wd_email: str, wd_pass: str) -> str:
    """
    Returns one of:
      created, needs_sign_in, already_exists, missing_credentials, failed, not_on_create
    """
    if not is_create_account_page(page):
        return "not_on_create"

    if not wd_email or not wd_pass:
        print("[warn] Missing create-account credentials (email/password).")
        return "missing_credentials"

    ok_email = _fill_email_input(page, wd_email) or fill_if_present_by_label(page, re.compile(r"Email Address|Email|Username", re.I), wd_email)
    ok_pw = _fill_password_input(page, wd_pass, index=0)
    ok_verify = (
        fill_if_present_by_label(page, re.compile(r"Verify New Password|Confirm Password", re.I), wd_pass)
        or _fill_password_input(page, wd_pass, index=1)
    )
    print(f"[debug] filled create-account email={ok_email} password={ok_pw} verify={ok_verify}")
    if not ok_email or not ok_pw or not ok_verify:
        return "failed"

    clicked = False
    candidates = [
        page.get_by_role("button", name=re.compile(r"Create Account", re.I)),
        page.locator("button:has-text('Create Account')"),
    ]
    for btn in candidates:
        try:
            if btn.count() == 0:
                continue
            el = btn.first
            if not el.is_visible():
                continue
            el.scroll_into_view_if_needed()
            try:
                el.click(timeout=5000)
            except Exception:
                el.click(timeout=5000, force=True)
            clicked = True
            print("[debug] clicked Create Account button")
            break
        except Exception:
            continue

    if not clicked:
        return "failed"

    page.wait_for_timeout(1000)
    waited = 0
    while waited < 12000:
        state = detect_auth_state(page)
        if state == "ready":
            return "created"
        if state == "sign_in":
            return "needs_sign_in"
        if state == "create_account":
            err = find_visible_text(page, [
                r"already have an account",
                r"already exists",
                r"email.*already",
                r"use.*sign in",
                r"unable to create",
                r"password requirements",
            ])
            if err:
                if re.search(r"already|sign in", err, re.I):
                    return "already_exists"
                return "failed"
        page.wait_for_timeout(250)
        waited += 250

    if is_create_account_page(page):
        return "failed"
    if is_sign_in_ui(page):
        return "needs_sign_in"
    if is_past_auth_step(page):
        return "created"
    return "failed"

def ensure_workday_authenticated(page, target_url: str, job_url: str, reopen_workday_apply_modal, answers: dict) -> str:
    """
    Returns:
      'signed_in'    -> proceed
      'needs_create' -> tenant onboarding needed (missing create creds or verify flow)
      'failed'       -> sign-in/create failed
    """
    tenant = workday_tenant(target_url) or workday_tenant(job_url)
    wd_email, wd_signin_pass, wd_create_pass = resolve_workday_auth_inputs(answers)
    known_tenant_account = tenant_has_known_account(tenant, wd_email)
    print(f"[debug] tenant={tenant}")
    print(
        f"[debug] known_tenant_account={known_tenant_account} "
        f"| email_set={bool(wd_email)} sign_in_pw_set={bool(wd_signin_pass)} create_pw_set={bool(wd_create_pass)}"
    )

    state = wait_for_auth_state(page, timeout_ms=7000)
    print(f"[debug] auth_state_initial={state}")

    if state == "unknown":
        reopened = reopen_workday_apply_modal(page)
        print(f"[debug] reopen_workday_apply_modal={reopened}")
        page.wait_for_timeout(1000)
        state = wait_for_auth_state(page, timeout_ms=6000)
        print(f"[debug] auth_state_after_reopen={state}")

    if state == "ready":
        remember_tenant_account(tenant, wd_email, "already_authenticated")
        return "signed_in"

    if known_tenant_account and state == "create_account":
        print("[debug] known tenant account; switching Create Account -> Sign In")
        switched = click_sign_in_link_from_create_account(page)
        print(f"[debug] switched_create_to_sign_in={switched}")
        state = wait_for_auth_state(page, timeout_ms=6000)
        print(f"[debug] auth_state_after_switch={state}")
        if state == "create_account":
            print("[warn] known tenant still shows Create Account after switch attempt")
            return "failed"

    if state == "sign_in":
        sign_in_password = wd_signin_pass or wd_create_pass
        ok = workday_sign_in_if_needed(page, wd_email, sign_in_password)
        print(f"[debug] sign_in_ok={ok}")
        if ok:
            remember_tenant_account(tenant, wd_email, "sign_in")
            return "signed_in"
        state = wait_for_auth_state(page, timeout_ms=3000)
        if state != "create_account":
            return "failed"

    if state == "create_account":
        create_result = workday_create_account_if_needed(page, wd_email, wd_create_pass)
        print(f"[debug] create_account_result={create_result}")

        if create_result == "created":
            remember_tenant_account(tenant, wd_email, "create_account")
            return "signed_in"

        if create_result == "needs_sign_in" or create_result == "already_exists":
            if create_result == "already_exists":
                # Persist this so future runs default to Sign In for this tenant.
                remember_tenant_account(tenant, wd_email, "already_exists")
            switched = click_sign_in_link_from_create_account(page) or is_sign_in_ui(page)
            print(f"[debug] create_to_signin_after_result={switched}")
            if not switched:
                # Some tenants don't expose a reliable DOM target after the duplicate-account error.
                # Reopen the apply flow and let auth state re-resolve.
                reopened = reopen_workday_apply_modal(page)
                print(f"[debug] reopen_after_create_exists={reopened}")
                state = wait_for_auth_state(page, timeout_ms=7000)
                print(f"[debug] auth_state_after_reopen_create_exists={state}")
                switched = state == "sign_in" or is_sign_in_ui(page)
                if not switched:
                    return "failed"
            sign_in_password = wd_signin_pass or wd_create_pass
            ok = workday_sign_in_if_needed(page, wd_email, sign_in_password)
            print(f"[debug] sign_in_after_create_ok={ok}")
            if ok:
                remember_tenant_account(tenant, wd_email, "sign_in_after_create")
                return "signed_in"
            return "failed"

        if create_result == "missing_credentials":
            return "needs_create"
        return "needs_create"

    if is_past_auth_step(page):
        remember_tenant_account(tenant, wd_email, "post_auth_fallback")
        return "signed_in"

    print("[warn] could not determine Workday auth state")
    return "failed"

def restart_apply_flow(page, job_url: str):
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    # click Apply button on the job posting page
    page.get_by_role("button", name=re.compile(r"^\s*Apply\s*$", re.I)).first.click(timeout=10000)
    page.wait_for_timeout(1000)

def is_my_information_form_open(page) -> bool:
    candidates = [
        page.get_by_label(re.compile(r"First Name|Last Name|Legal Name|Preferred Name", re.I)),
        page.get_by_label(re.compile(r"Address|City|State|Postal|Zip", re.I)),
        page.get_by_label(re.compile(r"Phone", re.I)),
    ]
    for c in candidates:
        try:
            if c.count() and c.first.is_visible():
                return True
        except Exception:
            pass
    return False

def click_continue(page) -> bool:
    # Common Workday continue buttons
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*Continue\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*Next\s*$", re.I)),
        page.locator("button:has-text('Continue')"),
        page.locator("button:has-text('Next')"),
    ]
    for c in candidates:
        try:
            if c.count() == 0:
                continue
            b = c.first
            if not b.is_visible():
                continue
            b.scroll_into_view_if_needed()
            try:
                b.click(timeout=3000)
            except Exception:
                b.click(timeout=3000, force=True)
            return True
        except Exception:
            continue
    return False

def upload_resume_best_effort(page, resume_path: str) -> bool:
    if not resume_path:
        print("[debug] no resume_path configured")
        return False

    resume_path = str(Path(resume_path).expanduser().resolve())
    if not os.path.exists(resume_path):
        print(f"[debug] resume not found: {resume_path}")
        return False

    # 1) Try any visible/hidden file input first.
    try:
        file_inputs = page.locator("input[type='file']")
        count = min(file_inputs.count(), 8)
        for i in range(count):
            try:
                file_inputs.nth(i).set_input_files(resume_path)
                print("[debug] resume uploaded via hidden file input")
                return True
            except Exception:
                continue
    except Exception as e:
        print(f"[debug] direct file input upload failed: {e}")

    # 2) Click dropzone and handle chooser.
    dropzone_candidates = [
        page.locator("text=Drop file here"),
        page.locator("text=/Drop\\s+file\\s+here/i"),
        page.locator("text=/Drop\\s+files?\\s+here/i"),
        page.locator("[data-automation-id*='drop' i]"),
        page.locator("[class*='dropzone' i]"),
    ]
    for dz in dropzone_candidates:
        try:
            if dz.count() == 0:
                continue
            target = dz.first
            if not target.is_visible():
                continue
            target.scroll_into_view_if_needed()
            with page.expect_file_chooser(timeout=5000) as fc_info:
                target.click(force=True)
            fc_info.value.set_files(resume_path)
            print("[debug] resume uploaded via dropzone chooser")
            return True
        except Exception:
            continue

    # 3) Click upload/select/attach controls and handle chooser.
    upload_clickers = [
        page.get_by_role("button", name=re.compile(r"Upload", re.I)),
        page.get_by_role("button", name=re.compile(r"Select", re.I)),
        page.get_by_role("button", name=re.compile(r"Attach", re.I)),
        page.locator("button:has-text('Upload')"),
        page.locator("button:has-text('Select')"),
        page.locator("button:has-text('Attach')"),
    ]
    for btn in upload_clickers:
        try:
            if btn.count() == 0:
                continue
            b = btn.first
            if not b.is_visible():
                continue
            b.scroll_into_view_if_needed()

            # Click to trigger file chooser, then set file via expect_file_chooser
            with page.expect_file_chooser(timeout=5000) as fc_info:
                try:
                    b.click(timeout=3000)
                except Exception:
                    b.click(timeout=3000, force=True)
            chooser = fc_info.value
            chooser.set_files(resume_path)
            print("[debug] resume uploaded via file chooser")
            return True
        except Exception:
            continue

    print("[debug] could not find resume upload control")
    return False

def _has_enabled_continue_or_next(page) -> bool:
    candidates = [
        page.get_by_role("button", name=re.compile(r"^\s*Continue\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"^\s*Next\s*$", re.I)),
        page.locator("button:has-text('Continue')"),
        page.locator("button:has-text('Next')"),
    ]
    for c in candidates:
        try:
            if c.count() == 0:
                continue
            el = c.first
            if el.is_visible() and el.is_enabled():
                return True
        except Exception:
            continue
    return False

def _resume_filename_visible(page, resume_path: str = "") -> bool:
    if not resume_path:
        return False
    filename = Path(resume_path).name
    if not filename:
        return False
    patterns = [
        re.escape(filename),
        re.escape(Path(filename).stem),
    ]
    for pat in patterns:
        try:
            loc = page.get_by_text(re.compile(pat, re.I))
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False

def wait_for_resume_parse(page, resume_path: str = "", timeout_ms: int = 60000):
    """
    Workday parsing UI varies. We'll wait until either:
    - We see 'My Information' step content, OR
    - We see filename attachment, OR
    - We see Continue/Next enabled, OR
    - Parsing/upload spinner disappears
    """
    start = 0
    while start < timeout_ms:
        # If My Information form already visible, we're good
        if is_my_information_form_open(page):
            return True

        if _resume_filename_visible(page, resume_path):
            print("[debug] resume filename visible in UI")
            return True

        # If we see parsing indicators, keep waiting
        parsing_indicators = [
            "text=Parsing",
            "text=Uploading",
            "text=Processing",
            "text=Reading your resume",
            "[aria-busy='true']",
            "[role='progressbar']",
        ]
        any_parsing = False
        for sel in parsing_indicators:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    any_parsing = True
                    break
            except Exception:
                pass

        # Continue/Next enabled is a strong signal parsing is done.
        if _has_enabled_continue_or_next(page):
            if click_continue(page):
                page.wait_for_timeout(1500)
                if is_my_information_form_open(page):
                    return True
                if _resume_filename_visible(page, resume_path):
                    return True
                # Even if UI varies, enabled continue means upload step is complete.
                return True

        # If parsing indicators disappeared, upload likely settled.
        if not any_parsing and _resume_filename_visible(page, resume_path):
            return True

        page.wait_for_timeout(500)
        start += 500

    return False

def workday_fill(job_id: int, headless: bool = False):
    answers = load_answers()
    identity = answers.get("identity", {}) or {}
    job = get_job(job_id)

    target = (job["apply_url"] or job["url"]).strip()
    job_url = (job["url"] or target).strip()
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

        def debug_shot(tag: str):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shot = ARTIFACTS / f"workday_job_{job_id}_{tag}_{ts}.png"
            try:
                page.screenshot(path=str(shot), full_page=True)
                print(f"[debug] screenshot: {shot}")
            except Exception as e:
                print(f"[debug] screenshot failed ({tag}): {e}")

        debug_shot("before")

        def try_click_apply_in_scope(scope, label: str) -> bool:
            # scope can be page or frame
            selectors = [
                ("wd apply button", lambda: scope.locator("[data-automation-id='applyButton']")),
                ("role=button", lambda: scope.get_by_role("button", name=re.compile(r"apply", re.I))),
                ("role=link", lambda: scope.get_by_role("link", name=re.compile(r"apply", re.I))),
                ("button text", lambda: scope.locator("button:has-text('Apply')")),
                ("css a", lambda: scope.locator("a:has-text('Apply')")),
                ("role attr", lambda: scope.locator("[role='button']:has-text('Apply')")),
                ("text exact", lambda: scope.get_by_text(re.compile(r"^\s*Apply\s*$", re.I))),
            ]

            for kind, make in selectors:
                try:
                    loc = make()
                    count = min(loc.count(), 5)
                    if count == 0:
                        continue

                    for idx in range(count):
                        el = loc.nth(idx)
                        try:
                            if not el.is_visible():
                                continue
                        except Exception:
                            continue
                        try:
                            el.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        scope.wait_for_timeout(200)
                        try:
                            el.click(timeout=3000)
                        except Exception:
                            el.click(timeout=3000, force=True)
                        print(f"[debug] clicked Apply via {kind}[{idx}] in {label}")
                        return True
                except Exception:
                    continue
            return False

        def click_apply_anywhere() -> bool:
            # Wait for interactive elements to hydrate.
            try:
                page.wait_for_selector("button, a, [role='button']", timeout=12000)
            except Exception:
                pass

            # Retry because Workday content often renders late and below the fold.
            for attempt in range(20):
                if try_click_apply_in_scope(page, "main page"):
                    return True

                for i, frame in enumerate(page.frames):
                    if frame == page.main_frame:
                        continue
                    if try_click_apply_in_scope(frame, f"frame[{i}] {frame.url}"):
                        return True

                # Scroll and retry to trigger lazy rendering.
                try:
                    page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
                except Exception:
                    pass
                page.wait_for_timeout(400)

                if attempt in (6, 12):
                    try:
                        page.evaluate("window.scrollTo(0, 0)")
                    except Exception:
                        pass
                    page.wait_for_timeout(400)

            return False

        def open_start_application_modal_and_pick_mode() -> bool:
            modal_detected = False
            # Wait for modal to appear
            try:
                page.wait_for_selector("text=Start Your Application", timeout=10000)
                print("[debug] Workday application modal detected.")
                modal_detected = True
            except Exception:
                print("[debug] Application modal not detected.")

            mode_clicked = False
            # Prefer Autofill with Resume
            try:
                autofill_btn = page.get_by_role("button", name=re.compile("Autofill", re.I))
                if autofill_btn.count() > 0:
                    autofill_btn.first.click()
                    print("[debug] Clicked 'Autofill with Resume'")
                    mode_clicked = True
                else:
                    manual_btn = page.get_by_role("button", name=re.compile("Apply Manually", re.I))
                    if manual_btn.count() > 0:
                        manual_btn.first.click()
                        print("[debug] Clicked 'Apply Manually'")
                        mode_clicked = True
            except Exception as e:
                print(f"[debug] Failed selecting application mode: {e}")

            return modal_detected or mode_clicked or is_sign_in_ui(page) or is_create_account_page(page)

        def reopen_workday_apply_modal(_page) -> bool:
            clicked_after_restart = click_apply_anywhere()
            print(f"[debug] apply_clicked_after_restart={clicked_after_restart}")
            if clicked_after_restart:
                try:
                    _page.wait_for_load_state("networkidle", timeout=20000)
                except PWTimeout:
                    pass
            _page.wait_for_timeout(1200)
            return open_start_application_modal_and_pick_mode()

        clicked = click_apply_anywhere()
        print(f"[debug] apply_clicked={clicked}")

        if clicked:
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except PWTimeout:
                pass

        if not clicked:
            # Quick diagnostics: do we see the word Apply anywhere?
            try:
                c = page.locator("text=Apply").count()
                print(f"[debug] page locator('text=Apply') count = {c}")
            except Exception as e:
                print(f"[debug] text=Apply count failed: {e}")

            # Frame diagnostics
            try:
                for i, fr in enumerate(page.frames):
                    try:
                        fc = fr.locator("text=Apply").count()
                        print(f"[debug] frame[{i}] text=Apply count = {fc} | {fr.url}")
                    except Exception:
                        pass
            except Exception:
                pass

        page.wait_for_timeout(1500)
        print(f"[debug] URL after click: {page.url}")
        debug_shot("after")

        # Try to detect application UI (NOT the header sign-in)
        app_signals = [
            "text=Create Account",
            "text=Create account",
            "text=Autofill with Resume",
            "text=Use my last application",
            "text=My Information",
            "text=Contact Information",
            "text=Resume",
        ]
        found_signal = None
        for sel in app_signals:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    found_signal = sel
                    break
            except Exception:
                pass

        print(f"[debug] app_signal={found_signal}")

        _opened_modal = open_start_application_modal_and_pick_mode()
        print(f"[debug] opened_modal_and_selected_mode={_opened_modal}")

        # After selecting Autofill/Manual, Workday may require auth
        page.wait_for_timeout(1500)
        auth_state = ensure_workday_authenticated(page, page.url, job_url, reopen_workday_apply_modal, answers)
        print(f"[debug] auth_state={auth_state}")

        if auth_state == "failed":
            print("[warn] Sign-in failed. Check creds or tenant behavior.")
            return

        if auth_state == "needs_create":
            print("[info] This tenant needs account creation or verification before continuing.")
            print("Set these env vars and retry:")
            print(" - JOBAGENT_WORKDAY_EMAIL")
            print(" - JOBAGENT_WORKDAY_CREATE_PASSWORD")
            print("Optional (for sign-in flows): JOBAGENT_WORKDAY_PASSWORD")
            print("Alternative: set config.answer_bank.json -> workday_account.default_password")
            return

        # ---- Resume upload + parse + continue ----
        resume_path = pick_resume_path(answers, job["track"])

        # We should be on a screen that includes Autofill / Resume step
        # Try upload
        uploaded = upload_resume_best_effort(page, resume_path)

        if not uploaded:
            print("[warn] resume upload skipped (missing file or upload control). Proceeding without resume autofill.")

        if uploaded:
            ok = wait_for_resume_parse(page, resume_path=resume_path, timeout_ms=90000)
            print(f"[debug] resume_parse_ok={ok}")
        else:
            print("[debug] resume upload did not run; will try Continue if available.")
            click_continue(page)

        # Ensure we land on My Information (or at least reach that step)
        page.wait_for_timeout(1500)
        if is_my_information_form_open(page):
            print("[debug] reached My Information FORM")
        else:
            print("[debug] My Information form not open yet")
            click_continue(page)
            page.wait_for_timeout(1500)
            if is_my_information_form_open(page):
                print("[debug] reached My Information FORM after extra Continue")
                    
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

        # Screenshot and pause (NO submit)
        shot = ARTIFACTS / f"workday_job_{job_id}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            print(f"Saved screenshot: {shot}")
        except Exception:
            pass

        print("\nREADY TO REVIEW (not submitted).")
        print("Please review the form in the browser, finish any required fields, then submit manually.\n")

        try:
            print("Browser will stay open for manual review. Close it when done.")
            while True:
                page.wait_for_timeout(1000)
        except Exception:
            print("Browser closed - exiting cleanly.")
        context.close()
        browser.close()
