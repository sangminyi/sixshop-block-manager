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

    switcher = page.locator(
        f"a:has-text('{current_store}'), button:has-text('{current_store}')"
    ).first
    switcher.wait_for(state="visible", timeout=10000)
    switcher.click()

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


def run_automation(
    email, password, store_id, block_name, block_code,
    block_id=None, block_property=None, block_settings=None, block_libraries=None,
    block_title=None,
    preserve_title=False, preserve_code=False, preserve_settings=False, preserve_libraries=False,
):
    """Generator that yields SSE-formatted status messages."""

    if block_property is None:
        block_property = {}
    if block_settings is None:
        block_settings = []
    if block_libraries is None:
        block_libraries = []

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            if block_id:
                # UPDATE mode
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

                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
                store_from_token = decode_jwt(bearer_token).get("storeName", "")
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_from_token,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }

                # Fetch current block if any field should be preserved
                if preserve_title or preserve_code or preserve_settings or preserve_libraries:
                    yield msg("Fetching current block data...")
                    get_resp = http.get(api_url, headers=headers, timeout=15)
                    if get_resp.status_code == 200:
                        current = get_resp.json()
                        if preserve_title:
                            block_title = current.get("title", "")
                        if preserve_code:
                            block_code = current.get("content", "")
                        if preserve_settings:
                            block_property = current.get("property", {})
                            block_settings = current.get("settings", [])
                        if preserve_libraries:
                            block_libraries = current.get("libraries", [])
                        yield msg("Current values loaded.", "success")
                    else:
                        yield msg(f"Warning: Could not fetch current block ({get_resp.status_code}). Using provided values.", "info")

                yield msg(f"Updating block (ID: {block_id})...")
                payload = {
                    "content": block_code,
                    "property": block_property,
                    "settings": block_settings,
                    "libraries": block_libraries,
                }
                payload["title"] = block_title

                resp = http.put(api_url, headers=headers, json=payload, timeout=15)

                if resp.status_code == 200:
                    block_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                    yield msg("Block updated successfully!", "success")
                    yield msg(f"Done. Block URL: {block_url}", "success")
                else:
                    yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")
                return

            else:
                # CREATE mode: login with store switching, then create via API
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
                browser.close()

                yield msg(f"Creating block '{block_name}' via API...")
                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "storeid": store_id,
                    "bff-access-key": BFF_ACCESS_KEY,
                    "Content-Type": "application/json",
                }
                resp = http.post(
                    "https://storefront-blockmaker-service.sixshop.io/v1/block-components",
                    headers=headers,
                    json={"title": block_name, "content": block_code, "status": "active", "property": block_property, "settings": block_settings, "libraries": block_libraries},
                    timeout=15,
                )

                if resp.status_code == 201:
                    data = resp.json()
                    new_block_id = data.get("_id")
                    block_url = f"https://store.sixshop.com/editor/block-maker/?id={new_block_id}" if new_block_id else "https://store.sixshop.com/editor/block-maker"
                    yield msg(f"Block created! ID: {new_block_id}", "success")
                    yield msg(f"Done. Block URL: {block_url}", "success")
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


def run_delete(email, password, block_id):
    """Generator that yields SSE-formatted status messages for block deletion."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
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

            yield msg(f"Deleting block (ID: {block_id})...")
            api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
            store_from_token = decode_jwt(bearer_token).get("storeName", "")
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "storeid": store_from_token,
                "bff-access-key": BFF_ACCESS_KEY,
                "Content-Type": "application/json",
            }
            resp = http.delete(api_url, headers=headers, timeout=15)
            if resp.status_code in (200, 204):
                yield msg("Block deleted successfully!", "success")
            else:
                yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")

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
    block_title = request.form.get("blockTitle", "").strip()
    block_code = request.form.get("blockCode", "").replace("\r\n", "\n").replace("\r", "\n")

    preserve_title = request.form.get("preserveTitle") == "on"
    preserve_code = request.form.get("preserveCode") == "on"
    preserve_settings = request.form.get("preserveSettings") == "on"
    preserve_libraries = request.form.get("preserveLibraries") == "on"

    try:
        sp = json.loads(request.form.get("settingsProperty", "") or "{}")
    except json.JSONDecodeError:
        sp = {}
    block_property = sp.get("property", {})
    block_settings = sp.get("settings", [])

    # TODO: libraries are currently ignored by the PUT/POST API.
    # Re-enable once the team opens libraries support on the block-components endpoint.
    raw_libraries = request.form.get("libraries", "").strip()
    block_libraries = [lib.strip() for lib in raw_libraries.split(",") if lib.strip()] if raw_libraries else []

    if not all([email, password]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email and password are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    if block_id:
        # UPDATE mode: block_code can be empty if preserve_code is set
        if not preserve_code and not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required (or check \'Preserve existing\' for Block Code).", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
    elif store_id and block_name:
        # CREATE mode: block_code required
        if not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required.", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
    else:
        automation_lock.release()
        return Response(
            'data: {"text": "Create mode requires Store ID and Block Name.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_automation(
                email, password, store_id, block_name, block_code,
                block_id=block_id or None,
                block_property=block_property,
                block_settings=block_settings,
                block_libraries=block_libraries,
                block_title=block_title or None,
                preserve_title=preserve_title,
                preserve_code=preserve_code,
                preserve_settings=preserve_settings,
                preserve_libraries=preserve_libraries,
            )
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/delete", methods=["POST"])
def delete_route():
    if not automation_lock.acquire(blocking=False):
        return Response(
            'data: {"text": "Another automation is already running. Please wait.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    delete_block_id = request.form.get("deleteBlockId", "").strip()

    if not all([email, password, delete_block_id]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, and Block ID are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_delete(email, password, delete_block_id)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
