"""
Inspection script for store switching.
Logs in, opens the store switcher, lists all stores, clicks the target store,
and reports network requests + URLs captured during the switch.

Usage: python3 inspect_store_switch.py <email> <password> <store_id>
"""
import sys
from playwright.sync_api import sync_playwright

def main(email, password, store_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        requests_log = []
        url_log = []

        def on_request(req):
            if req.method in ("POST", "PUT", "PATCH", "GET") and "sixshop" in req.url:
                entry = {
                    "method": req.method,
                    "url": req.url,
                    "storeid": req.headers.get("storeid", ""),
                    "auth": req.headers.get("authorization", "")[:60] + "..." if req.headers.get("authorization") else "",
                    "payload": req.post_data or "",
                }
                requests_log.append(entry)
                print(f"  [REQ] {req.method} {req.url[:80]}  storeid={req.headers.get('storeid', '-')}")

        def on_response(res):
            if "owner/auth/store" in res.url:
                try:
                    body = res.text()
                    print(f"\n  [RES] {res.url}")
                    print(f"  status  : {res.status}")
                    print(f"  body    : {body[:1000]}")
                except Exception as e:
                    print(f"  [RES] error reading body: {e}")

        def on_framenavigated(frame):
            if frame == page.main_frame:
                url_log.append(frame.url)
                print(f"  [NAV] {frame.url}")

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("framenavigated", on_framenavigated)

        print("Logging in...")
        page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
        page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        print(f"Logged in. Current URL: {page.url}")

        # Step 1: Click store switcher
        print("\nClicking store switcher (a.sc-908dea85-0)...")
        page.locator("a.sc-908dea85-0").first.click()

        # Step 2: Wait for store list and click last store button
        print("Waiting for store list...")
        page.locator("button:last-child .sc-aa391376-1").wait_for(state="visible", timeout=10000)

        # Print all store names found
        print("\n--- Stores found ---")
        spans = page.locator("button .sc-aa391376-1 span.sc-aa391376-5.GpgPy")
        count = spans.count()
        for i in range(count):
            print(f"  [{i}] '{spans.nth(i).text_content()}'")

        print(f"\nClicking store '{store_id}'...")
        page.locator(f"button .sc-aa391376-1 span.sc-aa391376-5.GpgPy:has-text('{store_id}')").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        print(f"\nFinal URL after store switch: {page.url}")

        print("\n--- All navigation URLs ---")
        for u in url_log:
            print(f"  {u}")

        print("\n--- Store switch API calls (payload + cookies) ---")
        switch_urls = ["owner/auth/store", "api/change"]
        for r in requests_log:
            if any(u in r["url"] for u in switch_urls):
                print(f"\n  {r['method']} {r['url']}")
                print(f"  storeid : {r['storeid']}")
                print(f"  auth    : {r['auth']}")
                print(f"  payload : {r['payload']}")
                print(f"  cookies : {r['cookies'][:300]}")

        browser.close()

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 inspect_store_switch.py <email> <password> <store_id>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
