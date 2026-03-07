"""
Captures the API call made when creating a new block via the Block Maker UI.
Usage: python3 inspect_block_create.py <email> <password> <store_id> <block_name>
"""
import sys
import json
from playwright.sync_api import sync_playwright


def main(email, password, store_id, block_name):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        captured = []

        def on_request(req):
            if req.method in ("POST", "PUT", "PATCH") and "sixshop" in req.url:
                captured.append({
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                    "body": req.post_data,
                })

        def on_response(res):
            for entry in captured:
                if entry["url"] == res.url and "response" not in entry:
                    try:
                        entry["response_status"] = res.status
                        entry["response_body"] = res.text()
                    except Exception:
                        pass

        page.on("request", on_request)
        page.on("response", on_response)

        # Login
        print("Logging in...")
        page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
        page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        page.wait_for_load_state("networkidle")
        print(f"Logged in. URL: {page.url}")

        # Switch store if needed
        current_store = page.url.rstrip("/").split("/")[-1]
        if current_store.lower() != store_id.lower():
            print(f"Switching to store '{store_id}'...")
            switcher = page.locator(f"a:has-text('{current_store}'), button:has-text('{current_store}')").first
            switcher.wait_for(state="visible", timeout=10000)
            switcher.click()
            page.get_by_role("button").filter(has_text=store_id).first.wait_for(state="visible", timeout=10000)
            page.get_by_role("button").filter(has_text=store_id).first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

        # Navigate to block-maker
        print("Navigating to block-maker...")
        page.goto("https://store.sixshop.com/editor/block-maker", wait_until="networkidle")
        captured.clear()

        # Click 블록 추가
        print("Clicking 블록 추가...")
        add_button = page.locator('[class*="AddButton"]').first
        add_button.wait_for(state="visible", timeout=10000)
        add_button.click()

        # Fill block name
        print(f"Filling block name: {block_name}")
        name_input = page.locator('input[name="blockName"]')
        name_input.wait_for(state="visible", timeout=10000)
        name_input.fill(block_name)

        # Click 추가
        print("Clicking 추가...")
        confirm_btn = page.locator('button[data-modal-action="true"]')
        confirm_btn.wait_for(state="visible", timeout=10000)
        confirm_btn.click()
        page.wait_for_timeout(3000)

        print("\n--- Captured API calls ---")
        for entry in captured:
            print(f"\n{entry['method']} {entry['url']}")
            print(f"  Status  : {entry.get('response_status', '?')}")
            print(f"  Payload : {entry.get('body', '(none)')}")
            print(f"  Headers (relevant):")
            for k in ("authorization", "storeid", "bff-access-key", "content-type"):
                if k in entry["headers"]:
                    print(f"    {k}: {entry['headers'][k][:80]}")
            print(f"  Response: {entry.get('response_body', '?')[:500]}")

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python3 inspect_block_create.py <email> <password> <store_id> <block_name>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
