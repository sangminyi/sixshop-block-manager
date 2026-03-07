"""
Captures GET responses when block pages load, comparing a block created by
our app (저장 active) vs one created manually (저장 inactive).
Usage: python3 inspect_block_load.py <email> <password> <app_block_id> <manual_block_id>
"""
import sys
import json
from playwright.sync_api import sync_playwright


def capture_block_load(page, block_id, label):
    captured = {}

    def on_response(res):
        if f"block-components/{block_id}" in res.url and res.request.method == "GET":
            try:
                captured["url"] = res.url
                captured["status"] = res.status
                captured["body"] = res.json()
            except Exception as e:
                captured["error"] = str(e)

    page.on("response", on_response)
    print(f"\n{'='*60}")
    print(f"Loading [{label}] block: {block_id}")
    page.goto(
        f"https://store.sixshop.com/editor/block-maker/?id={block_id}",
        wait_until="networkidle",
    )
    page.wait_for_timeout(2000)
    page.remove_listener("response", on_response)

    if not captured:
        print("  No GET to block-components captured.")
        return

    body = captured.get("body", {})
    print(f"  URL    : {captured.get('url')}")
    print(f"  Status : {captured.get('status')}")
    print(f"  Keys   : {list(body.keys())}")
    print(f"  meta      : {json.dumps(body.get('meta'))}")
    print(f"  status    : {body.get('status')}")
    print(f"  libraries : {json.dumps(body.get('libraries'))[:300]}")
    print(f"  snippet (first 200): {str(body.get('snippet', ''))[:200]}")
    print(f"  property empty? : {body.get('property') == {}}")
    print(f"  settings count  : {len(body.get('settings', []))}")


def main(email, password, app_block_id, manual_block_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print("Logging in...")
        page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
        page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        page.wait_for_load_state("networkidle")
        print(f"Logged in. URL: {page.url}")

        capture_block_load(page, app_block_id, "OUR APP - 저장 ACTIVE")
        capture_block_load(page, manual_block_id, "MANUAL - 저장 INACTIVE")

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python3 inspect_block_load.py <email> <password> <app_block_id> <manual_block_id>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
