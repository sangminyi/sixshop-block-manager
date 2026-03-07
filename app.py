import json
import threading
from flask import Flask, render_template, request, Response, stream_with_context
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)
automation_lock = threading.Lock()


def insert_code_into_editor(page, block_code, clear_first=False):
    """Try all strategies to insert code into the Prism Code Editor.
    Returns (success: bool, method: str)."""

    # Strategy 1: prism-code-editor's textarea
    try:
        editor_textarea = page.locator(".prism-code-editor textarea, .prism-editor textarea").first
        editor_textarea.wait_for(state="attached", timeout=5000)
        editor_textarea.click()
        if clear_first:
            page.keyboard.press("Meta+a")
            page.keyboard.press("Delete")
        editor_textarea.fill(block_code)
        page.evaluate("(el) => el.dispatchEvent(new Event('input', { bubbles: true }))",
                      editor_textarea.element_handle())
        return True, "editor textarea"
    except Exception:
        pass

    # Strategy 2: contenteditable element
    try:
        ce = page.locator("[contenteditable='true']").first
        ce.wait_for(state="visible", timeout=5000)
        ce.click()
        if clear_first:
            page.evaluate("""(el) => {
                el.textContent = '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""", ce.element_handle())
        page.evaluate("""(el, code) => {
            el.textContent = code;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", ce.element_handle(), block_code)
        return True, "contenteditable"
    except Exception:
        pass

    # Strategy 3: visible textarea fallback
    try:
        ta = page.locator("textarea:visible").first
        ta.wait_for(state="visible", timeout=5000)
        ta.click()
        if clear_first:
            page.keyboard.press("Meta+a")
            page.keyboard.press("Delete")
        ta.fill(block_code)
        page.evaluate("(el) => el.dispatchEvent(new Event('input', { bubbles: true }))",
                      ta.element_handle())
        return True, "visible textarea"
    except Exception:
        pass

    return False, ""


def run_automation(email, password, block_name, block_code, block_id=None):
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
            yield msg("Navigating to login page...")
            page.goto("https://store.sixshop.com/auth/login", wait_until="networkidle")

            yield msg("Filling in credentials...")
            page.fill('input[type="email"], input[name="email"], input[type="text"]', email)
            page.fill('input[type="password"]', password)

            yield msg("Submitting login...")
            page.keyboard.press("Enter")

            try:
                page.wait_for_url(
                    lambda url: "login" not in url,
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                yield msg("Login may have failed or is taking too long. Check the browser.", "error")
                return

            yield msg("Logged in successfully.", "success")

            if block_id:
                # UPDATE mode: go directly to the block page
                current_url = f"https://store.sixshop.com/editor/block-maker/?id={block_id}"
                yield msg(f"Navigating to block page (ID: {block_id})...")
                page.goto(current_url, wait_until="networkidle")
                yield msg("Block page loaded.", "success")

                # Step 5: Clear existing code and insert new code
                yield msg("Clearing existing code and inserting new code...")
                inserted, method = insert_code_into_editor(page, block_code, clear_first=True)

            else:
                # CREATE mode: navigate to block-maker and create a new block
                # Step 2: Navigate to block-maker
                yield msg("Navigating to block-maker...")
                page.goto("https://store.sixshop.com/editor/block-maker", wait_until="networkidle")

                # Step 3: Click 블록 추가 button
                yield msg("Clicking 블록 추가 button...")
                add_button = page.locator('[class*="AddButton"]').first
                add_button.wait_for(state="visible", timeout=10000)
                add_button.click()

                # Fill block name
                yield msg(f"Entering block name: {block_name}")
                name_input = page.locator('input[name="blockName"]')
                name_input.wait_for(state="visible", timeout=10000)
                name_input.fill(block_name)

                # Click 추가 (confirm) button
                yield msg("Clicking 추가 button...")
                confirm_btn = page.locator('button[data-modal-action="true"]')
                confirm_btn.wait_for(state="visible", timeout=10000)
                confirm_btn.click()

                # Step 4: Wait for redirect to block page
                yield msg("Waiting for block page to load...")
                try:
                    page.wait_for_url(
                        lambda url: "block-maker" in url and "id=" in url,
                        timeout=15000,
                    )
                except PlaywrightTimeoutError:
                    yield msg("Did not redirect to block page in time. Check the browser.", "error")
                    return

                current_url = page.url
                yield msg(f"Block created! Page: {current_url}", "success")
                page.wait_for_load_state("networkidle")

                # Step 5: Insert code into Prism Code Editor
                yield msg("Inserting code into editor...")
                inserted, method = insert_code_into_editor(page, block_code, clear_first=False)

            if not inserted:
                yield msg("Could not find code editor. Please insert code manually in the browser.", "error")
                return

            yield msg(f"Code inserted via {method}.")

            # Step 6: Click 저장 button
            yield msg("Clicking 저장 button...")
            save_btn = page.locator('button[aria-label="저장"]')
            save_btn.wait_for(state="visible", timeout=10000)
            save_btn.click()

            page.wait_for_timeout(2000)
            action = "updated" if block_id else "saved"
            yield msg(f"Block {action} successfully!", "success")
            yield msg(f"Done. Block URL: {current_url}", "success")

        except Exception as e:
            yield msg(f"Unexpected error: {e}", "error")
        finally:
            page.wait_for_timeout(3000)
            browser.close()


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
    block_id = request.form.get("blockId", "").strip()
    block_name = request.form.get("blockName", "").strip()
    block_code = request.form.get("blockCode", "")

    if not all([email, password, block_code]):
        automation_lock.release()
        return Response(
            'data: {"text": "Email, password, and block code are required.", "status": "error"}\n\n',
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
            yield from run_automation(email, password, block_name, block_code, block_id=block_id or None)
        finally:
            automation_lock.release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=False, port=8080)
