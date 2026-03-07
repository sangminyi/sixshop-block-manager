"""
Compares the stored content (GET) vs what the editor sends on 저장 (PUT).
If they differ, the editor normalizes content on load — that's the dirty bug cause.

Usage: python3 inspect_content_diff.py <email> <password> <block_id>
The block should be one created by our app (저장 currently ACTIVE).
"""
import sys
import json
from playwright.sync_api import sync_playwright


def main(email, password, block_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        stored_content = {}
        put_payload = {}

        def on_response(res):
            if f"block-components/{block_id}" in res.url:
                if res.request.method == "GET" and res.status == 200:
                    try:
                        data = res.json()
                        stored_content["content"]  = data.get("content", "")
                        stored_content["property"] = data.get("property", {})
                        stored_content["settings"] = data.get("settings", [])
                        print(f"[GET] Captured stored content ({len(stored_content['content'])} chars)")
                    except Exception as e:
                        print(f"[GET] Error: {e}")
                elif res.request.method == "PUT" and res.status == 200:
                    try:
                        body = json.loads(res.request.post_data or "{}")
                        put_payload["content"]  = body.get("content", "")
                        put_payload["property"] = body.get("property", {})
                        put_payload["settings"] = body.get("settings", [])
                        print(f"[PUT] Captured editor content ({len(put_payload['content'])} chars)")
                    except Exception as e:
                        print(f"[PUT] Error: {e}")

        page.on("response", on_response)

        print("Logging in...")
        page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
        page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
        page.locator('input[type="password"]').first.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        page.wait_for_load_state("networkidle")

        print(f"Navigating to block {block_id}...")
        page.goto(
            f"https://store.sixshop.com/editor/block-maker/?id={block_id}",
            wait_until="networkidle",
        )
        page.wait_for_timeout(2000)

        print("Clicking 저장...")
        save_btn = page.locator('button[aria-label="저장"]')
        save_btn.wait_for(state="visible", timeout=10000)
        save_btn.click()
        page.wait_for_timeout(2000)

        browser.close()

    if not stored_content or not put_payload:
        print("Could not capture both GET and PUT payloads.")
        return

    print("\n" + "="*60)
    print("CONTENT COMPARISON")
    print("="*60)

    # Content
    sc = stored_content["content"]
    pc = put_payload["content"]
    if sc == pc:
        print("\n[content] IDENTICAL — content is not normalized")
    else:
        print(f"\n[content] DIFFERENT ({len(sc)} stored vs {len(pc)} editor)")
        # Find first difference
        for i, (a, b) in enumerate(zip(sc, pc)):
            if a != b:
                print(f"  First diff at char {i}:")
                print(f"  Stored : {repr(sc[max(0,i-20):i+40])}")
                print(f"  Editor : {repr(pc[max(0,i-20):i+40])}")
                break
        if len(sc) != len(pc):
            print(f"  Length diff: stored={len(sc)}, editor={len(pc)}")
            print(f"  Stored end : {repr(sc[-50:])}")
            print(f"  Editor end : {repr(pc[-50:])}")

    # Property
    if stored_content["property"] == put_payload["property"]:
        print("\n[property] IDENTICAL")
    else:
        print("\n[property] DIFFERENT")
        stored_keys = set(stored_content["property"].keys())
        editor_keys = set(put_payload["property"].keys())
        print(f"  Keys only in stored : {stored_keys - editor_keys}")
        print(f"  Keys only in editor : {editor_keys - stored_keys}")

    # Settings
    if stored_content["settings"] == put_payload["settings"]:
        print("\n[settings] IDENTICAL")
    else:
        print(f"\n[settings] DIFFERENT (stored count={len(stored_content['settings'])}, editor count={len(put_payload['settings'])})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 inspect_content_diff.py <email> <password> <block_id>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
