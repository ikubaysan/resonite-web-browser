import os
import time
import uuid
import urllib.parse
from io import BytesIO
import logging
import platform
import ipaddress

from webdriver_manager.core.os_manager import OperationSystemManager, ChromeType

from modules.BrowserScripts import QUERY_NETWORK, INJECT_NETWORK_TRACKER, CLICK_AT, IS_INPUT

from selenium.common.exceptions import TimeoutException, WebDriverException

import chromedriver_autoinstaller
from PIL import Image
from functools import wraps

from flask import Flask, request, send_from_directory, Response

import undetected_chromedriver as uc
from selenium.webdriver.common.keys import Keys

from modules.Helpers import parse_coordinates, log_screenshot_size
from modules.ServerConfig import ServerConfig

from collections import OrderedDict
import time

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

CONFIG = ServerConfig()

PUBLIC_BASE_URL = CONFIG.public_base_url

SEARCH_ENGINE_URL = CONFIG.search_engine_url

BROWSER_WIDTH = CONFIG.browser_width
BROWSER_HEIGHT = CONFIG.browser_height

HEADLESS = CONFIG.headless

PORT = CONFIG.port

# =========================
# SECURITY
# =========================

def is_allowed_api_ip():

    ip_str = request.remote_addr

    log.info(f"[SECURITY] Request from IP: {ip_str}")

    try:
        ip = ipaddress.ip_address(ip_str)

    except ValueError:
        log.warning("[SECURITY] Invalid IP format")
        return False

    # Check against rules
    for network in CONFIG.allowed_ip_rules:

        if ip in network:
            return True

    log.warning("[SECURITY] BLOCKED request")

    return False


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

        self.use_memory_screenshots = CONFIG.use_memory_screenshots
        self.max_memory_screenshots = CONFIG.max_memory_screenshots

        self.screenshot_cache = OrderedDict()


        log.info("[BROWSER] Starting Undetected Chrome")

        self.install_chromedriver()

        options = uc.ChromeOptions()

        # This prevents Windows DPI scaling from breaking coordinates.
        options.add_argument("--force-device-scale-factor=1")

        options.add_argument('--force-dark-mode')

        if headless:
            options.add_argument("--headless=new")

        # Look for Chromium on Linux and Mac, and Google Chrome on Windows
        try:
            br_ver = OperationSystemManager().get_browser_version_from_os(
                ChromeType.CHROMIUM if platform.system() != 'Windows' else ChromeType.GOOGLE
            )
        except Exception:
            br_ver = OperationSystemManager().get_browser_version_from_os(ChromeType.GOOGLE)

        version_main = int(br_ver.split('.')[0])
        log.info(f"Got browser version: {br_ver}")

        self.driver = uc.Chrome(options=options, version_main=version_main)
        self.driver.set_page_load_timeout(30)

        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": BROWSER_WIDTH,
            "height": BROWSER_HEIGHT,
            "deviceScaleFactor": 1,
            "mobile": True,
        })

        self.driver.set_window_size(
            BROWSER_WIDTH,
            BROWSER_HEIGHT
        )


        self.output_dir = os.path.abspath("screenshots")

        os.makedirs(self.output_dir, exist_ok=True)

        self.cache = {}

        size = self.driver.execute_script("""
        return {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            devicePixelRatio: window.devicePixelRatio
        };
        """)

        print("VIEWPORT:", size)

        log.info("[BROWSER] Ready")

    # -------------------------
    # STOP CURRENT LOAD
    # -------------------------

    def stop_loading(self):
        """
        Attempt to stop the current page load.

        Safe to call even if no navigation is active.
        Uses window.stop() and ESC as fallback.
        """

        try:
            log.info("[NAV] Stopping current page load")

            # Primary method
            self.driver.execute_script(
                "window.stop();"
            )

        except Exception as e:
            log.warning(
                f"[NAV] window.stop() failed: {e}"
            )

        # ESC fallback (optional but helpful)
        try:
            body = self.driver.find_element(
                "tag name",
                "body"
            )

            body.send_keys(Keys.ESCAPE)

        except Exception:
            pass

    def install_chromedriver(self):
        """
        Ensure the correct chromedriver version is installed.

        Safe to call multiple times.
        Will download only if missing.
        """

        while True:
            try:
                path = chromedriver_autoinstaller.install()
                log.info(f"[CHROMEDRIVER] Ready at: {path}")
                break
            except Exception as e:
                log.error(f"[CHROMEDRIVER] Install failed: {e}")
                time.sleep(30)

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
            self.driver.execute_script(INJECT_NETWORK_TRACKER)
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
                            INJECT_NETWORK_TRACKER
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
                    QUERY_NETWORK
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

        # Stop current load first
        self.stop_loading()

        # Start navigation
        self.driver.get(url)

        self.wait_for_page_ready("navigate")
    # -------------------------
    # CLICK
    # -------------------------

    def click_at(self, img_x, img_y):

        self.stop_loading()

        # Use the configured browser dimensions (matches screenshot size)
        # NOT innerWidth/innerHeight which can differ due to mobile emulation
        px = int(BROWSER_WIDTH / 2 + img_x)
        py = int(BROWSER_HEIGHT / 2 - img_y)

        self.driver.execute_script(CLICK_AT, px, py)

        self.wait_for_page_ready("click")

        active = self.driver.execute_script(
            "return document.activeElement;"
        )

        is_input = self.driver.execute_script(
            IS_INPUT,
            active
        )

        text_value = ""

        if is_input:
            try:
                text_value = self.driver.execute_script(
                    """
                    let el = arguments[0];

                    if (!el) return "";

                    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
                        return el.value || "";
                    }

                    if (el.isContentEditable) {
                        return el.innerText || "";
                    }

                    return "";
                    """,
                    active
                )
            except Exception as e:
                log.warning(f"[CLICK] Failed to read input value: {e}")

        return bool(is_input), text_value

    # -------------------------
    # TYPE
    # -------------------------

    def type_at(self, img_x, img_y, text):
        px = int(BROWSER_WIDTH / 2 + img_x)
        py = int(BROWSER_HEIGHT / 2 - img_y)

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

        # Select all existing text and delete it before typing new text
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

        self.stop_loading()

        self.driver.back()

        self.wait_for_page_ready("back")

    def forward(self):

        self.stop_loading()

        self.driver.forward()

        self.wait_for_page_ready("forward")

    # -------------------------
    # SCREENSHOT
    # -------------------------

    def screenshot_viewport(self, url, fmt="jpg"):

        total_start = time.perf_counter()

        # =========================
        # wait_for_page_ready
        # =========================
        t0 = time.perf_counter()

        try:
            self.wait_for_page_ready("screenshot")
        except Exception as e:
            log.warning(f"[SCREENSHOT] wait_for_page_ready failed: {e}")

        log.info(
            f"[TIMING] wait_for_page_ready: "
            f"{time.perf_counter() - t0:.3f}s"
        )

        filename = f"{uuid.uuid4().hex}.{fmt.lower()}"

        # =========================
        # screenshot capture
        # =========================
        t0 = time.perf_counter()

        try:
            png_bytes = self.driver.get_screenshot_as_png()
        except (TimeoutException, WebDriverException) as e:
            log.warning(f"[SCREENSHOT] primary capture failed, retrying anyway: {e}")

            retry_start = time.perf_counter()

            try:
                png_bytes = self.driver.get_screenshot_as_png()
            except Exception as e2:
                log.error(f"[SCREENSHOT] fallback capture failed: {e2}")
                return None

            log.info(
                f"[TIMING] screenshot retry: "
                f"{time.perf_counter() - retry_start:.3f}s"
            )

        log.info(
            f"[TIMING] screenshot capture: "
            f"{time.perf_counter() - t0:.3f}s"
        )

        # =========================
        # raw size logging
        # =========================
        t0 = time.perf_counter()

        log_screenshot_size(png_bytes, filename, "Raw PNG")

        log.info(
            f"[TIMING] raw size logging: "
            f"{time.perf_counter() - t0:.3f}s"
        )

        # =========================
        # MEMORY MODE
        # =========================
        if self.use_memory_screenshots:

            t0 = time.perf_counter()

            if fmt.lower() in ("jpg", "jpeg"):
                image = Image.open(BytesIO(png_bytes)).convert("RGB")
                buffer = BytesIO()
                image.save(buffer, "JPEG", quality=85, optimize=True)
                data = buffer.getvalue()
            else:
                data = png_bytes

            log.info(
                f"[TIMING] memory conversion: "
                f"{time.perf_counter() - t0:.3f}s"
            )

            t0 = time.perf_counter()

            log_screenshot_size(data, filename, "memory")

            self.screenshot_cache[filename] = data

            while len(self.screenshot_cache) > self.max_memory_screenshots:
                self.screenshot_cache.popitem(last=False)

            log.info(
                f"[TIMING] memory storage: "
                f"{time.perf_counter() - t0:.3f}s"
            )

            log.info(
                f"[TIMING] TOTAL screenshot_viewport: "
                f"{time.perf_counter() - total_start:.3f}s"
            )

            return filename

        # =========================
        # FILE MODE
        # =========================
        path = os.path.join(self.output_dir, filename)

        t0 = time.perf_counter()

        try:
            if fmt.lower() == "png":
                with open(path, "wb") as f:
                    f.write(png_bytes)

            elif fmt.lower() in ("jpg", "jpeg"):
                image = Image.open(BytesIO(png_bytes)).convert("RGB")
                image.save(path, "JPEG", quality=85, optimize=True)
            else:
                raise ValueError("Invalid format")

            log_screenshot_size(png_bytes, filename, "file")

        except Exception as e:
            log.error(f"[SCREENSHOT] save failed: {e}")
            return None

        log.info(
            f"[TIMING] file save: "
            f"{time.perf_counter() - t0:.3f}s"
        )

        log.info(
            f"[TIMING] TOTAL screenshot_viewport: "
            f"{time.perf_counter() - total_start:.3f}s"
        )

        return filename

    def current_url(self):

        return self.driver.current_url

    def close(self):

        log.info("[BROWSER] shutdown")

        self.driver.quit()


# =========================
# APP
# =========================

app = Flask(__name__)

browser = BrowserManager(headless=HEADLESS)


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

    x, y = parse_coordinates(raw)

    is_input, text_value = browser.click_at(x, y)

    if is_input:
        log.info(f"[CLICK] Input value: '{text_value}'")
        status = "TEXT_INPUT" if is_input else "OK"
        return Response(f"{status}\n{text_value}")

    else:
        return Response("OK")

    # return Response(f"{status}\n{x} {y}")
    #return Response(f"{status}\n{text_value}")


# =========================
# TYPE
# =========================

@app.route("/type", methods=["POST"])
@require_api_ip
def type_text():
    """
    Expects raw body with format:
    <coordinates><newline><text>
    Where:
    :return:
    """

    raw = request.data.decode()

    first_newline = raw.index("\n")

    coords = raw[:first_newline]

    text = raw[first_newline + 1:]

    x, y = parse_coordinates(coords)

    browser.type_at(x, y, text)

    return Response("OK")


# =========================
# SCREENSHOT
# =========================

def _pad_field(text: str, size: int) -> str:
    """
    Pad text with spaces to exactly `size` characters.
    Truncates if too long.
    """
    text = text[:size]
    return text.ljust(size, " ")


@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():
    FIXED_FIELD_SIZE = 2048
    TOTAL_RESPONSE_SIZE = FIXED_FIELD_SIZE * 2

    url = browser.current_url()

    filename = browser.screenshot_viewport(url)

    file_url = format_file_url(filename)

    # Build fixed-length response
    padded_file_url = _pad_field(file_url, FIXED_FIELD_SIZE)
    padded_page_url = _pad_field(url, FIXED_FIELD_SIZE)

    response_text = padded_file_url + padded_page_url

    # Safety check (optional but recommended)
    assert len(response_text) == TOTAL_RESPONSE_SIZE

    return Response(
        response_text,
        mimetype="text/plain"
    )


# =========================
# SCROLL
# =========================

@app.route("/scroll/down", methods=["POST"])
@require_api_ip
def scroll_down():

    browser.scroll("down")

    return Response("OK")


@app.route("/scroll/up", methods=["POST"])
@require_api_ip
def scroll_up():

    browser.scroll("up")

    return Response("OK")


# =========================
# NAVIGATION
# =========================

@app.route("/back", methods=["POST"])
@require_api_ip
def back():

    browser.back()

    return Response("OK")


@app.route("/forward", methods=["POST"])
@require_api_ip
def forward():

    browser.forward()

    return Response("OK")


@app.route("/shutdown", methods=["POST"])
@require_api_ip
def shutdown():

    browser.close()

    return Response("closed")


# =========================
# FILES
# =========================

@app.route("/files/<path:filename>")
def files(filename):

    # =========================
    # MEMORY MODE
    # =========================
    if browser.use_memory_screenshots:

        data = browser.screenshot_cache.get(filename)

        if data is None:
            return Response("NOT FOUND", status=404)

        # infer mime type
        ext = filename.split(".")[-1].lower()
        mimetype = "image/png" if ext == "png" else "image/jpeg"

        return Response(data, mimetype=mimetype)

    # =========================
    # FILE MODE
    # =========================
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
        port=PORT,
    )