# Playwright "fill but don't submit" skeleton
# Run:
#   pip install playwright
#   playwright install
#   python automation/playwright_apply.py --url "<job link>" --resume "output/resumes/<file>.pdf"

import argparse
from playwright.sync_api import sync_playwright

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--resume", required=True)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded")

        # TODO: detect platform + route to handler modules:
        # greenhouse.py, lever.py, workday.py, builtin.py, linkedin_easy_apply.py (use with caution)
        page.wait_for_timeout(1500)
        page.screenshot(path="automation_last_page.png", full_page=True)

        print("Opened URL and captured automation_last_page.png")
        print("Next: implement platform-specific handler and STOP on final review screen.")

        ctx.close()
        browser.close()

if __name__ == "__main__":
    main()
