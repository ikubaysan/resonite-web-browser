import os
import uuid
import urllib.parse
from functools import wraps

from flask import Flask, request, send_from_directory, Response

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options


# =========================
# CONFIG
# =========================

GECKODRIVER_PATH = "geckodriver.exe"

ALLOWED_IPS = {
    "127.0.0.1",
    "localhost",
    "YOUR_PUBLIC_IP_HERE"
}

PUBLIC_BASE_URL = None

SEARCH_ENGINE_URL = "https://duckduckgo.com/?q={}"


# =========================
# SECURITY (API ONLY)
# =========================

def is_allowed_api_ip():
    return request.remote_addr in ALLOWED_IPS


def require_api_ip(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_allowed_api_ip():
            return Response(
                "FORBIDDEN: API access denied",
                status=403,
                mimetype="text/plain"
            )
        return func(*args, **kwargs)
    return wrapper


def format_file_url(filename: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{filename}"
    return f"/files/{filename}"


# =========================
# URL / SEARCH RESOLUTION
# =========================

def resolve_input_to_url(user_input: str) -> str:
    text = user_input.strip()

    if not text:
        raise ValueError("Empty input")

    # URL
    if text.startswith("http://") or text.startswith("https://"):
        return text

    # bare domain
    if "." in text and " " not in text:
        return "https://" + text

    # search query
    query = urllib.parse.quote_plus(text)
    return SEARCH_ENGINE_URL.format(query)


# =========================
# BROWSER
# =========================

class BrowserManager:
    def __init__(self, geckodriver_path=GECKODRIVER_PATH, headless=True):
        options = Options()
        options.headless = headless

        service = Service(geckodriver_path)
        self.driver = webdriver.Firefox(service=service, options=options)

        self.output_dir = os.path.abspath("screenshots")
        os.makedirs(self.output_dir, exist_ok=True)

    def open_url(self, url: str):
        self.driver.get(url)

    # viewport screenshot (visible area only)
    def screenshot_viewport(self) -> str:
        filename = f"{uuid.uuid4().hex}.png"
        filepath = os.path.join(self.output_dir, filename)
        self.driver.save_screenshot(filepath)
        return filename

    # full page screenshot
    def screenshot_full_page(self) -> str:
        filename = f"{uuid.uuid4().hex}.png"
        filepath = os.path.join(self.output_dir, filename)
        self.driver.save_full_page_screenshot(filepath)
        return filename

    def back(self):
        self.driver.back()

    def forward(self):
        self.driver.forward()

    def close(self):
        self.driver.quit()


# =========================
# APP
# =========================

app = Flask(__name__)
browser = BrowserManager(headless=False)


# =========================
# SCREENSHOT API
# =========================

@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():
    """
    RAW BODY:
        URL or search query

    QUERY PARAM:
        ?full=true  -> full page screenshot
        default     -> viewport only
    """

    raw_input = request.data.decode("utf-8").strip()

    if not raw_input:
        return Response("ERROR: empty input", status=400, mimetype="text/plain")

    try:
        url = resolve_input_to_url(raw_input)

        # -----------------------------
        # NEW: full vs viewport mode
        # -----------------------------
        full_param = request.args.get("full", "false").lower()
        full_screenshot = full_param in ("true", "1", "yes", "on")

        browser.open_url(url)

        if full_screenshot:
            filename = browser.screenshot_full_page()
        else:
            filename = browser.screenshot_viewport()

        return Response(format_file_url(filename), mimetype="text/plain")

    except Exception as e:
        return Response(f"ERROR: {str(e)}", status=500, mimetype="text/plain")


# =========================
# SCROLL CONTROL (API ONLY)
# =========================

@app.route("/scroll/down", methods=["GET"])
@require_api_ip
def scroll_down():
    try:
        browser.driver.execute_script(
            "window.scrollBy(0, window.innerHeight);"
        )
        return Response("OK", mimetype="text/plain")
    except Exception as e:
        return Response(f"ERROR: {str(e)}", status=500, mimetype="text/plain")


@app.route("/scroll/up", methods=["GET"])
@require_api_ip
def scroll_up():
    try:
        browser.driver.execute_script(
            "window.scrollBy(0, -window.innerHeight);"
        )
        return Response("OK", mimetype="text/plain")
    except Exception as e:
        return Response(f"ERROR: {str(e)}", status=500, mimetype="text/plain")


# =========================
# NAVIGATION (API ONLY)
# =========================

@app.route("/back", methods=["GET"])
@require_api_ip
def back():
    browser.back()
    return Response("OK", mimetype="text/plain")


@app.route("/forward", methods=["GET"])
@require_api_ip
def forward():
    browser.forward()
    return Response("OK", mimetype="text/plain")


@app.route("/shutdown", methods=["GET"])
@require_api_ip
def shutdown():
    browser.close()
    return Response("Browser closed", mimetype="text/plain")


# =========================
# PUBLIC FILE SERVER
# =========================

@app.route("/files/<path:filename>", methods=["GET"])
def files(filename):
    return send_from_directory(browser.output_dir, filename)


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5049)