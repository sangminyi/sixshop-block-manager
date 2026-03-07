"""
Run this script to capture the API call made when clicking 저장.
Usage: python3 inspect_api.py <email> <password> <block_id>
"""
import sys
from playwright.sync_api import sync_playwright

def main(email, password, block_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        captured = []

        def on_request(req):
            if req.method in ("POST", "PUT", "PATCH"):
                captured.append({
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                    "post_data": req.post_data,
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
        # Find the first text/email input and the password input
        page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        print("Logged in.")

        # Go to block page
        url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
        print(f"Navigating to {url}")
        page.goto(url, wait_until="networkidle")

        # Clear captured so far (only want save-related requests)
        captured.clear()

        # Click 저장
        print("Clicking 저장 button...")
        save_btn = page.locator('button[aria-label="저장"]')
        save_btn.wait_for(state="visible", timeout=10000)
        save_btn.click()
        page.wait_for_timeout(3000)

        # Print results
        print("\n--- Captured API calls ---")
        if not captured:
            print("No POST/PUT/PATCH requests captured.")
        for entry in captured:
            print(f"\n{entry['method']} {entry['url']}")
            print(f"  Status : {entry.get('response_status', '?')}")
            print(f"  Payload: {entry.get('post_data', '(none)')}")
            print(f"  Headers: {entry.get('headers', {})}")
            print(f"  Response: {entry.get('response_body', '?')[:500]}")

        browser.close()

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 inspect_api.py <email> <password> <block_id>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
