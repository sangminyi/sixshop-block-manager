"""
Captures the login API call made when submitting the Sixshop login form.
Usage: python3 inspect_login.py <store_id> <email> <password>
"""
import sys
import json
from playwright.sync_api import sync_playwright


def main(store_id, email, password):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        captured = []

        def on_request(req):
            if req.method == "POST" and "sixshop" in req.url:
                captured.append({
                    "url": req.url,
                    "headers": dict(req.headers),
                    "body": req.post_data,
                })

        def on_response(res):
            for entry in captured:
                if entry["url"] == res.url and "status" not in entry:
                    try:
                        entry["status"] = res.status
                        entry["response"] = res.text()
                    except Exception:
                        pass

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")

        login_btn = page.locator("button.iusDtM").first
        login_btn.wait_for(state="visible", timeout=10000)
        login_btn.click()

        text_inputs = page.locator('input[type="text"]')
        text_inputs.nth(0).wait_for(state="visible", timeout=8000)
        text_inputs.nth(0).fill(store_id)
        text_inputs.nth(1).fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.locator('button[type="submit"]').first.click()

        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        browser.close()

    print("\n" + "=" * 60)
    print(f"Captured {len(captured)} POST request(s)")
    print("=" * 60)
    for entry in captured:
        print(f"\nURL    : {entry['url']}")
        print(f"Status : {entry.get('status', '?')}")
        print(f"Body   : {entry.get('body', '(none)')}")
        print("Headers (relevant):")
        for k in ("content-type", "bff-access-key", "authorization", "storeid"):
            if k in entry["headers"]:
                print(f"  {k}: {entry['headers'][k][:100]}")
        resp = entry.get("response", "")
        try:
            print(f"Response: {json.dumps(json.loads(resp), indent=2)[:800]}")
        except Exception:
            print(f"Response: {resp[:400]}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 inspect_login.py <store_id> <email> <password>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
