import os
import time
import uuid
import urllib.parse
import hashlib
import json
from io import BytesIO
import logging
from PIL import Image
from functools import wraps

from flask import Flask, request, send_from_directory, Response

import undetected_chromedriver as uc
from selenium.webdriver.common.keys import Keys

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

ALLOWED_IPS = {
    "127.0.0.1",
    "localhost",
    "YOUR_PUBLIC_IP_HERE"
}

PUBLIC_BASE_URL = None

SEARCH_ENGINE_URL = "https://duckduckgo.com/?q={}"

BROWSER_WIDTH = 720
BROWSER_HEIGHT = 1280

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
            return Response("FORBIDDEN", status=403)
        return func(*args, **kwargs)

    return wrapper


def format_file_url(filename: str):
    url = (
        f"{PUBLIC_BASE_URL}/files/{filename}"
        if PUBLIC_BASE_URL
        else f"/files/{filename}"
    )
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

    url = SEARCH_ENGINE_URL.format(
        urllib.parse.quote_plus(text)
    )

    log.info(f"[RESOLVE] Search → {url}")

    return url


# =========================
# BROWSER
# =========================

class BrowserManager:

    def __init__(self, headless=False):

        log.info("[BROWSER] Starting Undetected Chrome")

        options = uc.ChromeOptions()

        # This prevents Windows DPI scaling from breaking coordinates.
        options.add_argument("--force-device-scale-factor=1")

        if headless:
            options.add_argument("--headless=new")

        self.driver = uc.Chrome(options=options)

        self.driver.set_window_size(
            BROWSER_WIDTH,
            BROWSER_HEIGHT
        )


        self.output_dir = os.path.abspath("screenshots")

        os.makedirs(self.output_dir, exist_ok=True)

        self.cache = {}

        log.info("[BROWSER] Ready")

    # -------------------------
    # NETWORK TRACKER
    # -------------------------

    _INJECT_NETWORK_TRACKER = """
    if (!window.__netTracker) {
        window.__netTracker = {
            inFlight: 0,
            lastFinished: performance.now()
        };

        const t = window.__netTracker;

        const origFetch = window.fetch;

        window.fetch = function(...args) {
            t.inFlight++;

            return origFetch.apply(this, args).finally(() => {
                t.inFlight = Math.max(0, t.inFlight - 1);
                t.lastFinished = performance.now();
            });
        };

        const origOpen = XMLHttpRequest.prototype.open;

        XMLHttpRequest.prototype.open = function(...args) {

            this.addEventListener('loadend', () => {
                t.inFlight = Math.max(0, t.inFlight - 1);
                t.lastFinished = performance.now();
            });

            t.inFlight++;

            return origOpen.apply(this, args);
        };
    }
    """

    _QUERY_NETWORK = """
    return (function() {

        const t = window.__netTracker;

        const vw = window.innerWidth;
        const vh = window.innerHeight;

        const pendingImgs =
            Array.from(document.images).filter(i => {

                if (!i.src || i.complete)
                    return false;

                const r = i.getBoundingClientRect();

                return (
                    r.bottom > 0 &&
                    r.right  > 0 &&
                    r.top    < vh &&
                    r.left   < vw
                );
            }).length;

        const inFlight =
            t ? t.inFlight : 0;

        const msSinceLast =
            t ? (performance.now() - t.lastFinished) : 9999;

        return {
            pendingImgs,
            inFlight,
            msSinceLast
        };

    })();
    """

    # -------------------------
    # WAIT
    # -------------------------

    def wait_for_page_ready(
            self,
            action_label="action",
            timeout_s=30.0,
            stability_s=0.4,
            network_quiet_ms=500
    ):

        log.info(f"[WAIT] {action_label}")

        deadline = time.monotonic() + timeout_s

        # Track navigation changes
        last_url = self.driver.current_url
        last_title = self.driver.title

        stable_since = time.monotonic()

        # Ensure tracker exists
        try:
            self.driver.execute_script(self._INJECT_NETWORK_TRACKER)
        except Exception:
            pass

        while time.monotonic() < deadline:

            try:

                ready = self.driver.execute_script(
                    "return document.readyState;"
                )

                cur_url = self.driver.current_url
                cur_title = self.driver.title

                # Detect navigation change
                if (
                        cur_url != last_url
                        or cur_title != last_title
                ):
                    log.info(
                        f"[WAIT] Navigation detected → {cur_url}"
                    )

                    last_url = cur_url
                    last_title = cur_title

                    stable_since = time.monotonic()

                    try:
                        self.driver.execute_script(
                            self._INJECT_NETWORK_TRACKER
                        )
                    except Exception:
                        pass

                # Wait for DOM ready
                if ready != "complete":
                    time.sleep(0.1)
                    continue

                # Wait for stability
                if (
                        time.monotonic()
                        - stable_since
                        < stability_s
                ):
                    time.sleep(0.1)
                    continue

                # Check network state
                net = self.driver.execute_script(
                    self._QUERY_NETWORK
                )

                if (
                        net["pendingImgs"] > 0
                        or net["inFlight"] > 0
                        or net["msSinceLast"] < network_quiet_ms
                ):
                    time.sleep(0.1)
                    continue

                log.info(
                    f"[WAIT] Page fully ready after {action_label}"
                )

                return

            except Exception:
                time.sleep(0.1)

        log.warning(
            f"[WAIT] timeout after {action_label}"
        )

    # -------------------------
    # NAVIGATION
    # -------------------------

    def navigate(self, url):

        log.info(f"[NAV] {url}")

        self.driver.get(url)

        self.wait_for_page_ready("navigate")

    # -------------------------
    # CLICK
    # -------------------------

    def click_at(self, img_x, img_y):

        vw = self.driver.execute_script(
            "return window.innerWidth;"
        )

        vh = self.driver.execute_script(
            "return window.innerHeight;"
        )

        px = int(vw / 2 + img_x)

        py = int(vh / 2 - img_y)

        self.driver.execute_script("""
            const x = arguments[0];
            const y = arguments[1];

            let el = document.elementFromPoint(x, y);
            if (!el) return;

            try {
                // Walk up DOM to find a clickable element
                let clickable = el;

                while (clickable && clickable !== document.body) {
                    if (typeof clickable.click === "function") break;
                    clickable = clickable.parentElement;
                }

                if (clickable && typeof clickable.click === "function") {
                    clickable.click();
                } else {
                    // Fallback: dispatch real mouse event
                    const evt = new MouseEvent("click", {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: x,
                        clientY: y
                    });
                    el.dispatchEvent(evt);
                }

                // Focus best candidate
                if (clickable && typeof clickable.focus === "function") {
                    clickable.focus();
                }

            } catch (e) {
                console.warn("Click fallback triggered:", e);

                // Absolute fallback
                const evt = new MouseEvent("click", {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: x,
                    clientY: y
                });
                el.dispatchEvent(evt);
            }
        """, px, py)

        self.wait_for_page_ready("click")

        active = self.driver.execute_script(
            "return document.activeElement;"
        )

        is_input = self.driver.execute_script(
            """
            let el = arguments[0];

            if (!el)
                return false;

            let tag =
                el.tagName.toLowerCase();

            if (tag === "textarea")
                return true;

            if (tag === "input")
                return true;

            return el.isContentEditable;
            """,
            active
        )

        return bool(is_input)

    # -------------------------
    # TYPE
    # -------------------------

    def type_at(self, img_x, img_y, text):

        vw = self.driver.execute_script(
            "return window.innerWidth;"
        )

        vh = self.driver.execute_script(
            "return window.innerHeight;"
        )

        px = int(vw / 2 + img_x)

        py = int(vh / 2 - img_y)

        self.driver.execute_script(
            """
            document
                .elementFromPoint(
                    arguments[0],
                    arguments[1]
                )?.click();
            """,
            px,
            py
        )

        active = self.driver.execute_script(
            "return document.activeElement;"
        )

        if not active:
            raise ValueError(
                "No text input found"
            )

        element = active

        element.send_keys(Keys.CONTROL, "a")

        element.send_keys(Keys.DELETE)

        element.send_keys(text)

        self.cache.clear()

        self.wait_for_page_ready("type")

    # -------------------------
    # SCROLL
    # -------------------------

    def scroll(self, direction: str):
        log.info(f"[SCROLL] {direction}")

        delta = (
            "window.innerHeight"
            if direction == "down"
            else "-window.innerHeight"
        )

        self.driver.execute_script(
            f"window.scrollBy(0, {delta});"
        )

        # Light wait — allow layout to settle
        time.sleep(0.15)

        # Optional: wait until scroll position stabilizes
        last_y = None
        stable_count = 0

        for _ in range(30):

            y = self.driver.execute_script(
                "return window.scrollY;"
            )

            if y == last_y:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0

            last_y = y
            time.sleep(0.05)

        log.info("[SCROLL] Settled")

    # -------------------------
    # NAVIGATION
    # -------------------------

    def back(self):

        self.driver.back()

        self.wait_for_page_ready("back")

    def forward(self):

        self.driver.forward()

        self.wait_for_page_ready("forward")

    # -------------------------
    # SCREENSHOT
    # -------------------------

    def screenshot_viewport(self, url, fmt="jpg"):

        self.wait_for_page_ready("screenshot")

        filename = f"{uuid.uuid4().hex}.{fmt.lower()}"
        path = os.path.join(self.output_dir, filename)

        # Always capture PNG bytes from Chrome
        png_bytes = self.driver.get_screenshot_as_png()

        # PNG mode = original behavior (fast path, no conversion)
        if fmt.lower() == "png":
            with open(path, "wb") as f:
                f.write(png_bytes)
            return filename

        # JPG mode = convert in memory
        elif fmt.lower() in ("jpg", "jpeg"):

            image = Image.open(BytesIO(png_bytes)).convert("RGB")

            image.save(
                path,
                "JPEG",
                quality=85,
                optimize=True
            )

            return filename

        else:
            raise ValueError("Invalid format. Use 'png' or 'jpg'")

    def current_url(self):

        return self.driver.current_url

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

    raw = request.data.decode().strip()

    if not raw:
        return Response("EMPTY", status=400)

    url = resolve_input_to_url(raw)

    browser.navigate(url)

    return Response("OK")


# =========================
# CLICK
# =========================

@app.route("/click", methods=["POST"])
@require_api_ip
def click():

    raw = request.data.decode().strip()

    x, y = map(float, raw.split())

    is_input = browser.click_at(x, y)

    status = "TEXT_INPUT" if is_input else "OK"

    return Response(f"{status}\n{x} {y}")


# =========================
# TYPE
# =========================

@app.route("/type", methods=["POST"])
@require_api_ip
def type_text():

    raw = request.data.decode()

    first_newline = raw.index("\n")

    coords = raw[:first_newline]

    text = raw[first_newline + 1:]

    x, y = map(float, coords.split())

    browser.type_at(x, y, text)

    return Response("OK")


# =========================
# SCREENSHOT
# =========================

@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():

    url = browser.current_url()

    filename = browser.screenshot_viewport(url)

    file_url = format_file_url(filename)

    return Response(
        f"{file_url}\n{url}"
    )


# =========================
# SCROLL
# =========================

@app.route("/scroll/down")
@require_api_ip
def scroll_down():

    browser.scroll("down")

    return Response("OK")


@app.route("/scroll/up")
@require_api_ip
def scroll_up():

    browser.scroll("up")

    return Response("OK")


# =========================
# NAVIGATION
# =========================

@app.route("/back")
@require_api_ip
def back():

    browser.back()

    return Response("OK")


@app.route("/forward")
@require_api_ip
def forward():

    browser.forward()

    return Response("OK")


@app.route("/shutdown")
@require_api_ip
def shutdown():

    browser.close()

    return Response("closed")


# =========================
# FILES
# =========================

@app.route("/files/<path:filename>")
def files(filename):

    return send_from_directory(
        browser.output_dir,
        filename
    )


# =========================
# RUN
# =========================

if __name__ == "__main__":

    log.info("[SERVER] starting")

    app.run(
        host="0.0.0.0",
        port=5049
    )