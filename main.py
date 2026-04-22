import os
import uuid
import urllib.parse
import hashlib
import json
import logging
from functools import wraps

from flask import Flask, request, send_from_directory, Response

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("BrowserAPI")


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
    ip = request.remote_addr
    log.info(f"[SECURITY] Request from IP: {ip}")
    return ip in ALLOWED_IPS


def require_api_ip(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_allowed_api_ip():
            log.warning("[SECURITY] BLOCKED request")
            return Response("FORBIDDEN", status=403, mimetype="text/plain")
        return func(*args, **kwargs)
    return wrapper


def format_file_url(filename: str):
    url = f"{PUBLIC_BASE_URL}/files/{filename}" if PUBLIC_BASE_URL else f"/files/{filename}"
    log.info(f"[FILES] Returning: {url}")
    return url


# =========================
# URL RESOLUTION
# =========================

def resolve_input_to_url(text: str) -> str:
    text = text.strip()
    log.info(f"[RESOLVE] Input: {text}")

    if not text:
        raise ValueError("Empty input")

    if text.startswith("http://") or text.startswith("https://"):
        return text

    if "." in text and " " not in text:
        url = "https://" + text
        log.info(f"[RESOLVE] Domain → {url}")
        return url

    url = SEARCH_ENGINE_URL.format(urllib.parse.quote_plus(text))
    log.info(f"[RESOLVE] Search → {url}")
    return url


# =========================
# BROWSER
# =========================

class BrowserManager:
    def __init__(self, geckodriver_path=GECKODRIVER_PATH, headless=True):
        log.info("[BROWSER] Starting Firefox")

        options = Options()
        options.headless = headless

        service = Service(geckodriver_path)
        self.driver = webdriver.Firefox(service=service, options=options)

        self.output_dir = os.path.abspath("screenshots")
        os.makedirs(self.output_dir, exist_ok=True)

        # cache:
        # key -> filename
        self.cache = {}

        log.info("[BROWSER] Ready")

    # -------------------------
    # NAVIGATION
    # -------------------------
    def navigate(self, url: str):
        log.info(f"[NAV] {url}")
        self.driver.get(url)

    # -------------------------
    # SCROLL POSITION
    # -------------------------
    def get_scroll(self):
        scroll = self.driver.execute_script(
            "return [window.scrollX, window.scrollY];"
        )
        log.info(f"[SCROLL] Position: {scroll}")
        return scroll

    # -------------------------
    # STABLE PAGE SIGNATURE
    # -------------------------
    def get_page_signature(self):
        sig = self.driver.execute_script("""
            return {
                url: window.location.href,
                title: document.title,
                body_len: document.body ? document.body.innerText.length : 0
            };
        """)
        log.info(f"[SIG] {sig}")
        return sig

    def hash_obj(self, obj):
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True).encode("utf-8")
        ).hexdigest()

    # -------------------------
    # SCREENSHOT VIEWPORT
    # -------------------------
    def screenshot_viewport(self, url: str):
        scroll = self.get_scroll()
        sig_hash = self.hash_obj(self.get_page_signature())

        key = (url, "viewport", scroll[0], scroll[1], sig_hash)

        log.info(f"[CACHE] Key: {key}")

        if key in self.cache:
            log.info("[CACHE] HIT → viewport")
            return self.cache[key]

        log.info("[CACHE] MISS → taking viewport screenshot")

        filename = f"{uuid.uuid4().hex}.png"
        path = os.path.join(self.output_dir, filename)

        self.driver.save_screenshot(path)

        self.cache[key] = filename

        log.info(f"[SCREENSHOT] Saved viewport: {filename}")
        return filename

    # -------------------------
    # SCREENSHOT FULL PAGE
    # -------------------------
    def screenshot_full(self, url: str):
        sig_hash = self.hash_obj(self.get_page_signature())

        key = (url, "full", sig_hash)

        log.info(f"[CACHE] Key: {key}")

        if key in self.cache:
            log.info("[CACHE] HIT → full page")
            return self.cache[key]

        log.info("[CACHE] MISS → full page screenshot")

        filename = f"{uuid.uuid4().hex}.png"
        path = os.path.join(self.output_dir, filename)

        self.driver.save_full_page_screenshot(path)

        self.cache[key] = filename

        log.info(f"[SCREENSHOT] Saved full: {filename}")
        return filename

    # -------------------------
    # NAV
    # -------------------------
    def back(self):
        log.info("[NAV] back")
        self.driver.back()

    def forward(self):
        log.info("[NAV] forward")
        self.driver.forward()

    def close(self):
        log.info("[BROWSER] shutdown")
        self.driver.quit()


# =========================
# APP
# =========================

app = Flask(__name__)
browser = BrowserManager(headless=False)


# =========================
# NAVIGATE
# =========================

@app.route("/navigate", methods=["POST"])
@require_api_ip
def navigate():
    raw = request.data.decode("utf-8").strip()

    if not raw:
        return Response("EMPTY", status=400)

    try:
        url = resolve_input_to_url(raw)
        browser.navigate(url)
        return Response("OK", mimetype="text/plain")
    except Exception as e:
        log.exception("Navigate error")
        return Response(str(e), status=500)


# =========================
# SCREENSHOT (CACHE-AWARE)
# =========================

@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():
    full = request.args.get("full", "false").lower() in ("true", "1", "yes", "on")

    try:
        url = browser.driver.current_url
        log.info(f"[SCREENSHOT] URL={url} full={full}")

        if full:
            filename = browser.screenshot_full(url)
        else:
            filename = browser.screenshot_viewport(url)

        return Response(format_file_url(filename), mimetype="text/plain")

    except Exception as e:
        log.exception("Screenshot error")
        return Response(str(e), status=500)


# =========================
# SCROLL
# =========================

@app.route("/scroll/down", methods=["GET"])
@require_api_ip
def scroll_down():
    log.info("[SCROLL] down")
    browser.driver.execute_script("window.scrollBy(0, window.innerHeight);")
    return Response("OK")


@app.route("/scroll/up", methods=["GET"])
@require_api_ip
def scroll_up():
    log.info("[SCROLL] up")
    browser.driver.execute_script("window.scrollBy(0, -window.innerHeight);")
    return Response("OK")


# =========================
# NAV
# =========================

@app.route("/back", methods=["GET"])
@require_api_ip
def back():
    browser.back()
    return Response("OK")


@app.route("/forward", methods=["GET"])
@require_api_ip
def forward():
    browser.forward()
    return Response("OK")


@app.route("/shutdown", methods=["GET"])
@require_api_ip
def shutdown():
    browser.close()
    return Response("closed")


# =========================
# FILES
# =========================

@app.route("/files/<path:filename>")
def files(filename):
    return send_from_directory(browser.output_dir, filename)


# =========================
# RUN
# =========================

if __name__ == "__main__":
    log.info("[SERVER] starting")
    app.run(host="0.0.0.0", port=5049)