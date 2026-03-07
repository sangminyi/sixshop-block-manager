import base64
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


def decode_jwt(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}



def login_and_get_token(page, email, password, store_id):
    """Log in via browser, switch store via API if needed, and return the Bearer token.
    Uses page.evaluate() for store switch so the browser session is updated correctly.
    Returns (bearer_token, error) tuple."""

    page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")
    page.locator('input[type="email"], input[type="text"], input[name*="email"], input[name*="id"]').first.fill(email)
    page.locator('input[type="password"]').first.fill(password)

    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "login" not in url, timeout=15000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)

    # Read token from localStorage — this is what the browser uses for API calls
    try:
        user_data = json.loads(page.evaluate("() => localStorage.getItem('user')") or "{}")
        initial_token = user_data.get("userToken")
    except Exception as e:
        return None, f"localstorage_parse_error: {e}"

    if not initial_token:
        return None, "no_token_in_localstorage"

    if not store_id:
        return initial_token, None

    # Detect current store from the landing URL
    current_store = page.url.rstrip("/").split("/")[-1]

    if current_store.lower() == store_id.lower():
        return initial_token, None

    # Intercept the store-switch API response to capture the new token.
    # We let the browser's own UI trigger the switch so all HTTP-only cookies
    # travel correctly across domains (storemanager-be.sixshop.io etc.).
    captured = {"token": None}

    def on_response(response):
        if "owner/auth/store" in response.url and response.status == 200:
            try:
                body = response.json()
                t = body.get("data", {}).get("userToken")
                if t:
                    captured["token"] = t
            except Exception:
                pass

    page.on("response", on_response)

    # Open the store switcher — find the element showing the current store name.
    # Uses text-based selectors so class names don't matter.
    switcher = page.locator(
        f"a:has-text('{current_store}'), button:has-text('{current_store}')"
    ).first
    switcher.wait_for(state="visible", timeout=10000)
    switcher.click()

    # Click the target store button by its visible text.
    target = page.get_by_role("button").filter(has_text=store_id).first
    target.wait_for(state="visible", timeout=10000)
    target.click()

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    page.remove_listener("response", on_response)

    if not captured["token"]:
        # Fallback: read updated token from localStorage
        try:
            user_data = json.loads(page.evaluate("() => localStorage.getItem('user')") or "{}")
            captured["token"] = user_data.get("userToken")
        except Exception:
            pass

    if not captured["token"]:
        return None, "no_token_after_store_switch"

    return captured["token"], None


def run_automation(email, password, store_id, block_name, block_code, block_id=None, block_property=None, block_settings=None):
    """Generator that yields SSE-formatted status messages."""

    if block_property is None:
        block_property = {}
    if block_settings is None:
        block_settings = []

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            if block_id:
                # UPDATE mode: no store switching needed — login to default store
                yield msg("Logging in...")
                try:
                    bearer_token, err = login_and_get_token(page, email, password, store_id="")
                except PlaywrightTimeoutError:
                    yield msg("Login failed or timed out.", "error")
                    return
                if not bearer_token:
                    yield msg(f"Could not get auth token: {err}", "error")
                    return
                yield msg("Logged in.", "success")
                browser.close()

                yield msg(f"Updating block (ID: {block_id})...")
                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
                store_from_token = decode_jwt(bearer_token).get("storeName", "")
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_from_token,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }
                resp = http.put(api_url, headers=headers, json={"content": block_code, "property": block_property, "settings": block_settings}, timeout=15)

                if resp.status_code == 200:
                    block_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                    yield msg("Block updated successfully!", "success")
                    yield msg(f"Done. Block URL: {block_url}", "success")
                else:
                    yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")
                return

            else:
                # CREATE mode: login with store switching, then browser automation
                yield msg(f"Logging in and switching to store '{store_id}'...")
                try:
                    bearer_token, err = login_and_get_token(page, email, password, store_id)
                except PlaywrightTimeoutError:
                    yield msg("Login failed or timed out.", "error")
                    return
                if not bearer_token:
                    yield msg(f"Could not get auth token: {err}", "error")
                    return
                yield msg(f"Logged in. Active store: {store_id}", "success")

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

                yield msg("Saving block code via API...")
                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{new_block_id}"
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_id,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }
                resp = http.put(api_url, headers=headers, json={"content": block_code, "property": block_property, "settings": block_settings}, timeout=15)

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

    try:
        sp = json.loads(request.form.get("settingsProperty", "") or "{}")
    except json.JSONDecodeError:
        sp = {}
    block_property = sp.get("property", {})
    block_settings = sp.get("settings", [])

    if not all([email, password, block_code]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, and block code are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    if block_id:
        pass  # UPDATE mode: block_id is sufficient
    elif store_id and block_name:
        pass  # CREATE mode: both store_id and block_name required
    else:
        automation_lock.release()
        return Response(
            'data: {"text": "Create mode requires Store ID and Block Name.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_automation(email, password, store_id, block_name, block_code, block_id=block_id or None, block_property=block_property, block_settings=block_settings)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
