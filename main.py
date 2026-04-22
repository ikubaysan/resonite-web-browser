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
# SECURITY
# =========================

def is_allowed_api_ip():
    return request.remote_addr in ALLOWED_IPS


def require_api_ip(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_allowed_api_ip():
            return Response("FORBIDDEN: API access denied", status=403, mimetype="text/plain")
        return func(*args, **kwargs)
    return wrapper


def format_file_url(filename: str):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{filename}"
    return f"/files/{filename}"


# =========================
# URL / SEARCH RESOLUTION
# =========================

def resolve_input_to_url(text: str) -> str:
    text = text.strip()

    if not text:
        raise ValueError("Empty input")

    if text.startswith("http://") or text.startswith("https://"):
        return text

    if "." in text and " " not in text:
        return "https://" + text

    return SEARCH_ENGINE_URL.format(urllib.parse.quote_plus(text))


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

    # NAVIGATION ONLY
    def navigate(self, url: str):
        self.driver.get(url)

    def screenshot_viewport(self):
        filename = f"{uuid.uuid4().hex}.png"
        path = os.path.join(self.output_dir, filename)
        self.driver.save_screenshot(path)
        return filename

    def screenshot_full(self):
        filename = f"{uuid.uuid4().hex}.png"
        path = os.path.join(self.output_dir, filename)
        self.driver.save_full_page_screenshot(path)
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
# NAVIGATION ENDPOINT (NEW)
# =========================

@app.route("/navigate", methods=["POST"])
@require_api_ip
def navigate():
    """
    RAW BODY:
        URL OR search query

    Example:
        https://github.com
        selenium firefox docs
    """
    raw = request.data.decode("utf-8").strip()

    if not raw:
        return Response("ERROR: empty input", status=400, mimetype="text/plain")

    try:
        url = resolve_input_to_url(raw)
        browser.navigate(url)
        return Response("OK", mimetype="text/plain")

    except Exception as e:
        return Response(f"ERROR: {str(e)}", status=500, mimetype="text/plain")


# =========================
# SCREENSHOT ONLY ENDPOINT
# =========================

@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():
    """
    Takes screenshot of CURRENT browser state.

    QUERY PARAM:
        ?full=true  -> full page
        default     -> viewport
    """

    full_param = request.args.get("full", "false").lower()
    full = full_param in ("true", "1", "yes", "on")

    try:
        if full:
            filename = browser.screenshot_full()
        else:
            filename = browser.screenshot_viewport()

        return Response(format_file_url(filename), mimetype="text/plain")

    except Exception as e:
        return Response(f"ERROR: {str(e)}", status=500, mimetype="text/plain")


# =========================
# SCROLL (UNCHANGED)
# =========================

@app.route("/scroll/down", methods=["GET"])
@require_api_ip
def scroll_down():
    browser.driver.execute_script("window.scrollBy(0, window.innerHeight);")
    return Response("OK", mimetype="text/plain")


@app.route("/scroll/up", methods=["GET"])
@require_api_ip
def scroll_up():
    browser.driver.execute_script("window.scrollBy(0, -window.innerHeight);")
    return Response("OK", mimetype="text/plain")


# =========================
# NAVIGATION CONTROLS
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
# FILE SERVER (PUBLIC)
# =========================

@app.route("/files/<path:filename>")
def files(filename):
    return send_from_directory(browser.output_dir, filename)


# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5049)