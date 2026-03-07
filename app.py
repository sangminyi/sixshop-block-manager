import json
import threading
import requests as http
from flask import Flask, render_template, request, Response, stream_with_context

BFF_ACCESS_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJTVE9SRUZST05UX0FDQ0VTU19UT0tFTiIsImlzcyI6IlNGX0FDQ0VTU09SIiwiaWF0IjoxNjc1Mzc4NDU0fQ"
    ".HrLRIfMdad7bt14rAn2Q-_WXHVQkuz2x6tTdNhUxwQI"
)

app = Flask(__name__)
automation_lock = threading.Lock()


def get_auth_token(store_id, email, password):
    """Login via API and return (token, error) tuple."""
    try:
        resp = http.post(
            "https://storemanager-be.sixshop.io/v1/api/user/signin",
            json={"storeName": store_id, "userId": email, "password": password},
            timeout=15,
        )
        if resp.status_code == 200:
            token = resp.json().get("data", {}).get("userToken")
            if token:
                return token, None
            return None, "no_token_in_response"
        return None, f"signin_error_{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return None, f"signin_exception: {e}"


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

    try:
        yield msg("Logging in...")
        bearer_token, err = get_auth_token(store_id, email, password)
        if not bearer_token:
            yield msg(f"Login failed: {err}", "error")
            return
        yield msg("Logged in.", "success")

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "storeid": store_id,
            "bff-access-key": BFF_ACCESS_KEY,
            "Content-Type": "application/json",
        }

        if block_id:
            # UPDATE mode
            api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"

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
                "title": block_title,
                "content": block_code,
                "property": block_property,
                "settings": block_settings,
                "libraries": block_libraries,
            }
            resp = http.put(api_url, headers=headers, json=payload, timeout=15)

            if resp.status_code == 200:
                block_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                yield msg("Block updated successfully!", "success")
                yield msg(f"Done. Block URL: {block_url}", "success")
            else:
                yield msg(f"API error {resp.status_code}: {resp.text[:300]}", "error")

        else:
            # CREATE mode
            yield msg(f"Creating block '{block_name}'...")
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

    except Exception as e:
        yield msg(f"Unexpected error: {e}", "error")


def run_delete(email, password, store_id, block_id):
    """Generator that yields SSE-formatted status messages for block deletion."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    try:
        yield msg("Logging in...")
        bearer_token, err = get_auth_token(store_id, email, password)
        if not bearer_token:
            yield msg(f"Login failed: {err}", "error")
            return
        yield msg("Logged in.", "success")

        yield msg(f"Deleting block (ID: {block_id})...")
        api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "storeid": store_id,
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

    if not all([email, password, store_id]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, and Store ID are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    if block_id:
        # UPDATE mode
        if not preserve_code and not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required (or check \'Preserve existing\' for Block Code).", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
    elif store_id and block_name:
        # CREATE mode
        if not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required.", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
    else:
        automation_lock.release()
        return Response(
            'data: {"text": "Create mode requires Block Name.", "status": "error"}\n\n',
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
    store_id = request.form.get("storeId", "").strip()
    delete_block_id = request.form.get("deleteBlockId", "").strip()

    if not all([email, password, store_id, delete_block_id]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, Store ID, and Block ID are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_delete(email, password, store_id, delete_block_id)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
