import json
import threading
import requests as http
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BFF_ACCESS_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJTVE9SRUZST05UX0FDQ0VTU19UT0tFTiIsImlzcyI6IlNGX0FDQ0VTU09SIiwiaWF0IjoxNjc1Mzc4NDU0fQ"
    ".HrLRIfMdad7bt14rAn2Q-_WXHVQkuz2x6tTdNhUxwQI"
)

app = Flask(__name__)
automation_lock = threading.Lock()



def login_and_capture_token(page, email, password, store_id):
    """Log in, switch to the correct store via UI, and capture the Bearer token.
    Returns bearer_token or None on failure."""
    captured = {}
    capturing = False

    def on_request(req):
        if not capturing or captured.get("token"):
            return
        auth = req.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            captured["token"] = auth[len("Bearer "):]

    page.on("request", on_request)

    page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
    page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
    page.locator('input[type="password"]').first.fill(password)
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "login" not in url, timeout=15000)

    # Detect the default (currently active) store
    current_store = page.locator(".sc-908dea85-1 span.sc-908dea85-4").first.text_content().strip()

    if current_store.lower() != store_id.lower():
        # Switch to the requested store via UI
        page.locator("a.sc-908dea85-0").first.click()
        page.locator("button .sc-aa391376-1 span.sc-aa391376-5.GpgPy").first.wait_for(state="visible", timeout=10000)
        store_span = page.locator(f"button .sc-aa391376-1 span.sc-aa391376-5.GpgPy:has-text('{store_id}')")
        store_span.wait_for(state="visible", timeout=5000)
        store_span.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

    # Start capturing token only now (correct store is active)
    capturing = True

    # Trigger authenticated requests to capture the Bearer token
    page.goto("https://store.sixshop.com/editor/block-maker", wait_until="networkidle")
    page.wait_for_timeout(2000)

    return captured.get("token")


def run_automation(email, password, store_id, block_name, block_code, block_id=None):
    """Generator that yields SSE-formatted status messages."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Step 1: Login
            yield msg(f"Logging in and switching to store '{store_id}'...")
            try:
                bearer_token = login_and_capture_token(page, email, password, store_id)
            except PlaywrightTimeoutError:
                yield msg("Login failed or timed out.", "error")
                return

            if not bearer_token:
                yield msg("Logged in but could not capture auth token. Try again.", "error")
                return

            yield msg(f"Logged in. Active store: {store_id}", "success")

            if block_id:
                # UPDATE mode: call the API directly
                browser.close()
                yield msg(f"Updating block via API (ID: {block_id})...")

                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_id,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }
                payload = {"content": block_code, "property": {}, "settings": []}
                resp = http.put(api_url, headers=headers, json=payload, timeout=15)

                if resp.status_code == 200:
                    block_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                    yield msg("Block updated successfully!", "success")
                    yield msg(f"Done. Block URL: {block_url}", "success")
                else:
                    yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")
                return

            else:
                # CREATE mode: use browser automation
                yield msg("Navigating to block-maker...")
                page.goto("https://store.sixshop.com/editor/block-maker", wait_until="networkidle")

                yield msg("Clicking 블록 추가 button...")
                add_button = page.locator('[class*="AddButton"]').first
                add_button.wait_for(state="visible", timeout=10000)
                add_button.click()

                yield msg(f"Entering block name: {block_name}")
                name_input = page.locator('input[name="blockName"]')
                name_input.wait_for(state="visible", timeout=10000)
                name_input.fill(block_name)

                yield msg("Clicking 추가 button...")
                confirm_btn = page.locator('button[data-modal-action="true"]')
                confirm_btn.wait_for(state="visible", timeout=10000)
                confirm_btn.click()

                yield msg("Waiting for block page to load...")
                try:
                    page.wait_for_url(
                        lambda url: "block-maker" in url and "id=" in url,
                        timeout=15000,
                    )
                except PlaywrightTimeoutError:
                    yield msg("Did not redirect to block page in time.", "error")
                    return

                current_url = page.url
                new_block_id = current_url.split("id=")[-1]
                yield msg(f"Block created! ID: {new_block_id}", "success")
                page.wait_for_load_state("networkidle")
                browser.close()

                # Insert code via API now that we have the new block ID
                yield msg("Saving block code via API...")
                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{new_block_id}"
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_id,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }
                payload = {"content": block_code, "property": {}, "settings": []}
                resp = http.put(api_url, headers=headers, json=payload, timeout=15)

                if resp.status_code == 200:
                    yield msg("Block saved successfully!", "success")
                    yield msg(f"Done. Block URL: {current_url}", "success")
                else:
                    yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")
                return

        except Exception as e:
            yield msg(f"Unexpected error: {e}", "error")
        finally:
            try:
                browser.close()
            except Exception:
                pass


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run():
    if not automation_lock.acquire(blocking=False):
        return Response(
            'data: {"text": "Another automation is already running. Please wait.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    store_id = request.form.get("storeId", "").strip()
    block_id = request.form.get("blockId", "").strip()
    block_name = request.form.get("blockName", "").strip()
    block_code = request.form.get("blockCode", "")

    if not all([email, password, store_id, block_code]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, store ID, and block code are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    if not block_id and not block_name:
        automation_lock.release()
        return Response(
            'data: {"text": "Provide either a Block ID (to update) or a Block Name (to create).", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_automation(email, password, store_id, block_name, block_code, block_id=block_id or None)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
