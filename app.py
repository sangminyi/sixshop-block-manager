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


def _summary(results, total, action_done):
    """Yields SSE summary lines for a completed bulk job."""
    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    success_count = sum(1 for r in results if r["success"])
    completed = len(results)
    incomplete = total - completed
    header = f"결과 요약  {success_count}/{completed} 성공"
    if incomplete:
        header += f"  ({incomplete}개 미완료)"

    yield msg("─" * 48)
    yield msg(header)
    for r in results:
        label = f"{r['store_id']} / {r['block_id']}"
        if r["success"]:
            yield msg(f"{label}  —  {action_done}", "success")
        else:
            yield msg(f"{label}  —  {r['error']}", "error")
    yield msg("─" * 48)


def run_bulk_create(email, password, store_ids, block_name, block_code, block_property, block_settings, block_libraries):
    """Generator for CREATE mode — runs sequentially across all store IDs."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    total = len(store_ids)
    results = []

    try:
        for i, store_id in enumerate(store_ids, 1):
            prefix = f"[{i}/{total}] {store_id}"

            yield msg(f"{prefix} — Logging in...")
            bearer_token, err = get_auth_token(store_id, email, password)
            if not bearer_token:
                err_text = f"Login failed: {err}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": None, "success": False, "error": err_text})
                continue

            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "storeid": store_id,
                "bff-access-key": BFF_ACCESS_KEY,
                "Content-Type": "application/json",
            }

            yield msg(f"{prefix} — Creating block '{block_name}'...")
            try:
                resp = http.post(
                    "https://storefront-blockmaker-service.sixshop.io/v1/block-components",
                    headers=headers,
                    json={"title": block_name, "content": block_code, "status": "active", "property": block_property, "settings": block_settings, "libraries": block_libraries},
                    timeout=15,
                )
                if resp.status_code == 201:
                    new_block_id = resp.json().get("_id")
                    yield msg(f"{prefix} — Block created! ID: {new_block_id}", "success")
                    results.append({"store_id": store_id, "block_id": new_block_id, "success": True})
                else:
                    err_text = f"API error {resp.status_code}: {resp.text[:100]}"
                    yield msg(f"{prefix} — {err_text}", "error")
                    results.append({"store_id": store_id, "block_id": None, "success": False, "error": err_text})
            except Exception as e:
                err_text = f"Request error: {e}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": None, "success": False, "error": err_text})

    except Exception as e:
        yield msg(f"Unexpected error: {e}", "error")

    if results:
        def msg2(text, status="info"):
            data = json.dumps({"text": text, "status": status})
            return f"data: {data}\n\n"
        success_count = sum(1 for r in results if r["success"])
        completed = len(results)
        incomplete = total - completed
        header = f"결과 요약  {success_count}/{completed} 성공"
        if incomplete:
            header += f"  ({incomplete}개 미완료)"
        yield msg("─" * 48)
        yield msg(header)
        for r in results:
            if r["success"]:
                yield msg(f"{r['store_id']}  —  ID: {r['block_id']}", "success")
            else:
                yield msg(f"{r['store_id']}  —  {r['error']}", "error")
        yield msg("─" * 48)


def run_bulk_update(
    email, password, pairs, block_code, block_property, block_settings, block_libraries,
    block_title=None,
    preserve_title=False, preserve_code=False, preserve_settings=False, preserve_libraries=False,
):
    """Generator for UPDATE mode — runs sequentially across all (store_id, block_id) pairs."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    total = len(pairs)
    results = []

    try:
        for i, (store_id, block_id) in enumerate(pairs, 1):
            prefix = f"[{i}/{total}] {store_id} / {block_id}"

            yield msg(f"{prefix} — Logging in...")
            bearer_token, err = get_auth_token(store_id, email, password)
            if not bearer_token:
                err_text = f"Login failed: {err}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})
                continue

            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "storeid": store_id,
                "bff-access-key": BFF_ACCESS_KEY,
                "Content-Type": "application/json",
            }
            api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"

            cur_title, cur_code = block_title, block_code
            cur_property, cur_settings, cur_libraries = block_property, block_settings, block_libraries

            if preserve_title or preserve_code or preserve_settings or preserve_libraries:
                yield msg(f"{prefix} — Fetching current block data...")
                try:
                    get_resp = http.get(api_url, headers=headers, timeout=15)
                    if get_resp.status_code == 200:
                        current = get_resp.json()
                        if preserve_title:
                            cur_title = current.get("title", "")
                        if preserve_code:
                            cur_code = current.get("content", "")
                        if preserve_settings:
                            cur_property = current.get("property", {})
                            cur_settings = current.get("settings", [])
                        if preserve_libraries:
                            cur_libraries = current.get("libraries", [])
                        yield msg(f"{prefix} — Current values loaded.", "success")
                    else:
                        yield msg(f"{prefix} — Warning: Could not fetch ({get_resp.status_code}). Using provided values.", "info")
                except Exception as e:
                    yield msg(f"{prefix} — Warning: Fetch error: {e}. Using provided values.", "info")

            yield msg(f"{prefix} — Updating...")
            try:
                resp = http.put(api_url, headers=headers, json={
                    "title": cur_title,
                    "content": cur_code,
                    "property": cur_property,
                    "settings": cur_settings,
                    "libraries": cur_libraries,
                }, timeout=15)
                if resp.status_code == 200:
                    block_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                    yield msg(f"{prefix} — Updated! {block_url}", "success")
                    results.append({"store_id": store_id, "block_id": block_id, "success": True})
                else:
                    err_text = f"API error {resp.status_code}: {resp.text[:100]}"
                    yield msg(f"{prefix} — {err_text}", "error")
                    results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})
            except Exception as e:
                err_text = f"Request error: {e}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})

    except Exception as e:
        yield msg(f"Unexpected error: {e}", "error")

    if results:
        success_count = sum(1 for r in results if r["success"])
        completed = len(results)
        incomplete = total - completed
        header = f"결과 요약  {success_count}/{completed} 성공"
        if incomplete:
            header += f"  ({incomplete}개 미완료)"
        yield msg("─" * 48)
        yield msg(header)
        for r in results:
            label = f"{r['store_id']} / {r['block_id']}"
            if r["success"]:
                yield msg(f"{label}  —  업데이트 완료", "success")
            else:
                yield msg(f"{label}  —  {r['error']}", "error")
        yield msg("─" * 48)


def run_bulk_delete(email, password, pairs):
    """Generator for DELETE mode — runs sequentially across all (store_id, block_id) pairs."""

    def msg(text, status="info"):
        data = json.dumps({"text": text, "status": status})
        return f"data: {data}\n\n"

    total = len(pairs)
    results = []

    try:
        for i, (store_id, block_id) in enumerate(pairs, 1):
            prefix = f"[{i}/{total}] {store_id} / {block_id}"

            yield msg(f"{prefix} — Logging in...")
            bearer_token, err = get_auth_token(store_id, email, password)
            if not bearer_token:
                err_text = f"Login failed: {err}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})
                continue

            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "storeid": store_id,
                "bff-access-key": BFF_ACCESS_KEY,
                "Content-Type": "application/json",
            }

            yield msg(f"{prefix} — Deleting...")
            try:
                api_url = f"https://storefront-blockmaker-service.sixshop.io/v1/block-components/{block_id}"
                resp = http.delete(api_url, headers=headers, timeout=15)
                if resp.status_code in (200, 204):
                    yield msg(f"{prefix} — Deleted!", "success")
                    results.append({"store_id": store_id, "block_id": block_id, "success": True})
                else:
                    err_text = f"API error {resp.status_code}: {resp.text[:100]}"
                    yield msg(f"{prefix} — {err_text}", "error")
                    results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})
            except Exception as e:
                err_text = f"Request error: {e}"
                yield msg(f"{prefix} — {err_text}", "error")
                results.append({"store_id": store_id, "block_id": block_id, "success": False, "error": err_text})

    except Exception as e:
        yield msg(f"Unexpected error: {e}", "error")

    if results:
        success_count = sum(1 for r in results if r["success"])
        completed = len(results)
        incomplete = total - completed
        header = f"결과 요약  {success_count}/{completed} 성공"
        if incomplete:
            header += f"  ({incomplete}개 미완료)"
        yield msg("─" * 48)
        yield msg(header)
        for r in results:
            label = f"{r['store_id']} / {r['block_id']}"
            if r["success"]:
                yield msg(f"{label}  —  삭제 완료", "success")
            else:
                yield msg(f"{label}  —  {r['error']}", "error")
        yield msg("─" * 48)


@app.route("/")
def index():
    return render_template("index.html")


def _parse_pairs(store_ids_raw, block_ids_raw, limit=100):
    store_ids = [s.strip() for s in store_ids_raw.splitlines() if s.strip()]
    block_ids = [s.strip() for s in block_ids_raw.splitlines() if s.strip()]
    if len(store_ids) != len(block_ids):
        return None, "Store IDs and Block IDs counts do not match."
    if not store_ids:
        return None, "At least one pair is required."
    return list(zip(store_ids, block_ids))[:limit], None


@app.route("/run", methods=["POST"])
def run():
    if not automation_lock.acquire(blocking=False):
        return Response(
            'data: {"text": "Another automation is already running. Please wait.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    mode = request.form.get("mode", "create")

    if not all([email, password]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email and password are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    if mode == "update":
        pairs, err = _parse_pairs(
            request.form.get("storeIdsUpdate", ""),
            request.form.get("blockIdsUpdate", ""),
        )
        if err:
            automation_lock.release()
            return Response(
                f'data: {{"text": "{err}", "status": "error"}}\n\n',
                mimetype="text/event-stream",
            )

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
        raw_libraries = request.form.get("libraries", "").strip()
        block_libraries = [lib.strip() for lib in raw_libraries.split(",") if lib.strip()] if raw_libraries else []

        if not preserve_code and not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required (or check \'Preserve existing\' for Block Code).", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )

        def generate():
            try:
                yield from run_bulk_update(
                    email, password, pairs, block_code, block_property, block_settings, block_libraries,
                    block_title=block_title or None,
                    preserve_title=preserve_title, preserve_code=preserve_code,
                    preserve_settings=preserve_settings, preserve_libraries=preserve_libraries,
                )
            finally:
                automation_lock.release()

    else:
        # CREATE mode
        store_ids = list(dict.fromkeys(
            s.strip() for s in request.form.get("storeIds", "").splitlines() if s.strip()
        ))[:100]
        block_name = request.form.get("blockName", "").strip()
        block_code = request.form.get("blockCode", "").replace("\r\n", "\n").replace("\r", "\n")

        try:
            sp = json.loads(request.form.get("settingsProperty", "") or "{}")
        except json.JSONDecodeError:
            sp = {}
        block_property = sp.get("property", {})
        block_settings = sp.get("settings", [])
        raw_libraries = request.form.get("libraries", "").strip()
        block_libraries = [lib.strip() for lib in raw_libraries.split(",") if lib.strip()] if raw_libraries else []

        if not store_ids:
            automation_lock.release()
            return Response(
                'data: {"text": "At least one Store ID is required.", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
        if not block_name:
            automation_lock.release()
            return Response(
                'data: {"text": "Block Name is required.", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )
        if not block_code:
            automation_lock.release()
            return Response(
                'data: {"text": "Block code is required.", "status": "error"}\n\n',
                mimetype="text/event-stream",
            )

        def generate():
            try:
                yield from run_bulk_create(
                    email, password, store_ids, block_name, block_code,
                    block_property, block_settings, block_libraries,
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

    if not all([email, password]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email and password are required.", "status": "error"}\n\n',
            mimetype="text/event-stream",
        )

    pairs, err = _parse_pairs(
        request.form.get("storeIdsDelete", ""),
        request.form.get("blockIdsDelete", ""),
    )
    if err:
        automation_lock.release()
        return Response(
            f'data: {{"text": "{err}", "status": "error"}}\n\n',
            mimetype="text/event-stream",
        )

    def generate():
        try:
            yield from run_bulk_delete(email, password, pairs)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
