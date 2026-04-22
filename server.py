import os
import time
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
        self.cache = {}
        log.info("[BROWSER] Ready")

    # -------------------------
    # PAGE READINESS WAIT
    # -------------------------

    # Injected once per page — installs a PerformanceObserver that tracks
    # how many resource fetches are in-flight at any moment.
    _INJECT_NETWORK_TRACKER = """
    if (!window.__netTracker) {
        window.__netTracker = {
            inFlight: 0,
            lastFinished: performance.now()
        };
        const t = window.__netTracker;
        // Intercept fetch
        const origFetch = window.fetch;
        window.fetch = function(...args) {
            t.inFlight++;
            return origFetch.apply(this, args).finally(() => {
                t.inFlight = Math.max(0, t.inFlight - 1);
                t.lastFinished = performance.now();
            });
        };
        // Intercept XMLHttpRequest
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
        // Only count images that are inside the current viewport.
        // Lazy-loaded off-screen images are never .complete until scrolled
        // into view, so waiting for them would always time out.
        const vw = window.innerWidth, vh = window.innerHeight;
        const pendingImgs = Array.from(document.images).filter(i => {
            if (!i.src || i.complete) return false;
            const r = i.getBoundingClientRect();
            return r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
        }).length;
        const inFlight    = t ? t.inFlight : 0;
        const msSinceLast = t ? (performance.now() - t.lastFinished) : 9999;
        return {pendingImgs, inFlight, msSinceLast};
    })();
    """

    def wait_for_page_ready(self, action_label: str = "action",
                            settle_s: float = 0.15,
                            poll_s: float = 0.1,
                            stability_s: float = 0.4,
                            network_quiet_ms: float = 500,
                            timeout_s: float = 30.0):
        """
        Block until the browser has truly finished loading after any action.

        Waits for ALL of:
          1. document.readyState == 'complete'
          2. URL and page title stable for `stability_s` seconds
             (catches SPA soft-navigations)
          3. All <img> elements are .complete (no src still downloading)
          4. No in-flight fetch/XHR requests AND no new network resource
             has finished for at least `network_quiet_ms` ms
        """
        log.info(f"[WAIT] Settling after {action_label} ({settle_s}s)…")
        time.sleep(settle_s)

        # Inject the network tracker (no-op if already installed on this page)
        try:
            self.driver.execute_script(self._INJECT_NETWORK_TRACKER)
        except Exception:
            pass  # page may still be unloading; we'll retry in the loop

        deadline     = time.monotonic() + timeout_s
        stable_since = time.monotonic()
        last_url     = self.driver.current_url
        last_title   = self.driver.title

        log.info(f"[WAIT] Polling for full page ready (timeout={timeout_s}s)…")
        while time.monotonic() < deadline:
            try:
                ready_state = self.driver.execute_script("return document.readyState;")
                cur_url     = self.driver.current_url
                cur_title   = self.driver.title

                # Re-inject tracker after a navigation flushes the page
                if cur_url != last_url or cur_title != last_title:
                    last_url     = cur_url
                    last_title   = cur_title
                    stable_since = time.monotonic()
                    log.info(f"[WAIT] Change → {cur_url!r}  readyState={ready_state}")
                    try:
                        self.driver.execute_script(self._INJECT_NETWORK_TRACKER)
                    except Exception:
                        pass

                if ready_state != "complete":
                    time.sleep(poll_s)
                    continue

                if time.monotonic() - stable_since < stability_s:
                    time.sleep(poll_s)
                    continue

                net = self.driver.execute_script(self._QUERY_NETWORK)
                pending_imgs = net.get("pendingImgs", 0)
                in_flight    = net.get("inFlight", 0)
                ms_quiet     = net.get("msSinceLast", 9999)

                if pending_imgs > 0 or in_flight > 0 or ms_quiet < network_quiet_ms:
                    log.info(
                        f"[WAIT] Still loading — imgs={pending_imgs} "
                        f"xhr/fetch={in_flight} quietMs={ms_quiet:.0f}"
                    )
                    time.sleep(poll_s)
                    continue

                log.info(f"[WAIT] Page fully ready after {action_label}")
                return

            except Exception as exc:
                # Driver may briefly throw during a navigation; just keep polling
                log.debug(f"[WAIT] Poll exception (transient): {exc}")
                time.sleep(poll_s)

        log.warning(f"[WAIT] Timed out after {timeout_s}s waiting for {action_label}")

    # -------------------------
    # NAVIGATION
    # -------------------------
    def navigate(self, url: str):
        log.info(f"[NAV] {url}")
        self.driver.get(url)
        self.wait_for_page_ready("navigate")

    # -------------------------
    # CLICK at centred coords
    # Returns True if a text input is now focused
    # -------------------------

    # Tags / types that accept keyboard text input
    _IS_TEXT_INPUT_JS = """
    return (function(el) {
        if (!el) return false;
        const tag = el.tagName.toLowerCase();
        if (tag === 'textarea') return true;
        if (el.isContentEditable) return true;
        if (tag === 'input') {
            const t = (el.type || 'text').toLowerCase();
            const textTypes = ['text','search','email','url','tel','password',
                               'number','date','datetime-local','month','week','time'];
            return textTypes.includes(t);
        }
        return false;
    })(arguments[0]);
    """

    def _coords_to_viewport_px(self, img_x: float, img_y: float):
        vw = self.driver.execute_script("return window.innerWidth;")
        vh = self.driver.execute_script("return window.innerHeight;")
        img_x = max(-vw / 2, min(vw / 2, img_x))
        img_y = max(-vh / 2, min(vh / 2, img_y))
        px = int(vw / 2 + img_x)
        py = int(vh / 2 - img_y)
        return px, py

    def click_at(self, img_x: float, img_y: float) -> bool:
        """Click and return True if the clicked element (or active element) is a text input."""
        px, py = self._coords_to_viewport_px(img_x, img_y)
        log.info(f"[CLICK] img=({img_x},{img_y}) → px=({px},{py})")

        # Check what's at these coords BEFORE clicking — some sites move focus
        # away from the input on click (e.g. clicking a wrapper div).
        el_at_point = self.driver.execute_script(
            "return document.elementFromPoint(arguments[0], arguments[1]);",
            px, py
        )
        is_input_at_point = bool(self.driver.execute_script(
            self._IS_TEXT_INPUT_JS, el_at_point
        )) if el_at_point else False

        # Also try walking up the ancestor chain — input may be wrapped in a label/div
        is_input_ancestor = bool(self.driver.execute_script("""
            let el = document.elementFromPoint(arguments[0], arguments[1]);
            while (el && el !== document.body) {
                const tag = el.tagName.toLowerCase();
                if (tag === 'textarea') return true;
                if (el.isContentEditable) return true;
                if (tag === 'input') {
                    const t = (el.type || 'text').toLowerCase();
                    const ok = ['text','search','email','url','tel','password',
                                'number','date','datetime-local','month','week','time'];
                    if (ok.includes(t)) return true;
                }
                el = el.parentElement;
            }
            return false;
        """, px, py))

        # Perform the click + explicit focus
        self.driver.execute_script("""
            const x = arguments[0];
            const y = arguments[1];

            let el = document.elementFromPoint(x, y);
            if (!el) return;

            try {
                // If click() exists, use it
                if (typeof el.click === "function") {
                    el.click();
                } else {
                    // Otherwise simulate a real mouse click
                    const evt = new MouseEvent("click", {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                        clientX: x,
                        clientY: y
                    });
                    el.dispatchEvent(evt);
                }

                // Focus if possible
                if (typeof el.focus === "function") {
                    el.focus();
                }

            } catch (e) {
                console.warn("Click fallback triggered:", e);
            }
        """, px, py)

        self.wait_for_page_ready("click")

        # Check activeElement after click
        active = self.driver.execute_script("return document.activeElement;")
        is_active_input = bool(self.driver.execute_script(
            self._IS_TEXT_INPUT_JS, active
        )) if active else False

        is_input = is_input_at_point or is_input_ancestor or is_active_input
        log.info(
            f"[CLICK] text_input check — at_point={is_input_at_point} "
            f"ancestor={is_input_ancestor} active={is_active_input} → {is_input}"
        )
        return is_input

    # -------------------------
    # TYPE into element at coords
    # -------------------------
    def type_at(self, img_x: float, img_y: float, text: str):
        """Focus the element at (img_x, img_y), clear it, type text."""
        from selenium.webdriver.common.keys import Keys

        px, py = self._coords_to_viewport_px(img_x, img_y)
        log.info(f"[TYPE] img=({img_x},{img_y}) → px=({px},{py})  text={text!r}")

        # Click to focus
        self.driver.execute_script(
            "document.elementFromPoint(arguments[0], arguments[1])?.click();",
            px, py
        )

        active = self.driver.execute_script("return document.activeElement;")
        is_input = bool(self.driver.execute_script(self._IS_TEXT_INPUT_JS, active))
        if not is_input:
            raise ValueError("No text input found at those coordinates")

        # Clear existing content then send keys
        active.send_keys(Keys.CONTROL, "a")
        active.send_keys(Keys.DELETE)
        active.send_keys(text)
        log.info("[TYPE] Text entered")

        # Invalidate cache for current URL so next screenshot is fresh
        cur_url = self.driver.current_url
        stale = [k for k in self.cache if k[0] == cur_url]
        for k in stale:
            del self.cache[k]
        log.info(f"[CACHE] Invalidated {len(stale)} entries for {cur_url!r}")

    # -------------------------
    # SCROLL
    # -------------------------
    def scroll(self, direction: str):
        log.info(f"[SCROLL] {direction}")
        delta = "window.innerHeight" if direction == "down" else "-window.innerHeight"
        self.driver.execute_script(f"window.scrollBy(0, {delta});")
        self.wait_for_page_ready("scroll")

    # -------------------------
    # BACK / FORWARD
    # -------------------------
    def back(self):
        log.info("[NAV] back")
        self.driver.back()
        self.wait_for_page_ready("back")

    def forward(self):
        log.info("[NAV] forward")
        self.driver.forward()
        self.wait_for_page_ready("forward")

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
    # CURRENT URL
    # -------------------------
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
# CLICK
# =========================
@app.route("/click", methods=["POST"])
@require_api_ip
def click():
    """
    Body: "x y"
    Response (plain text, two lines):
      Line 1: "TEXT_INPUT" if a text field was focused, else "OK"
      Line 2: echoed "x y" (so the client knows where to follow up with /type)
    """
    raw = request.data.decode("utf-8").strip()
    log.info(f"[CLICK] Raw body: {raw!r}")
    try:
        parts = raw.split()
        if len(parts) != 2:
            return Response("Expected 'x y'", status=400)
        img_x = float(parts[0])
        img_y = float(parts[1])
        is_input = browser.click_at(img_x, img_y)
        status = "TEXT_INPUT" if is_input else "OK"
        return Response(f"{status}\n{img_x} {img_y}", mimetype="text/plain")
    except ValueError as e:
        return Response(f"Bad coords: {e}", status=400)
    except Exception as e:
        log.exception("Click error")
        return Response(str(e), status=500)


# =========================
# TYPE
# =========================
@app.route("/type", methods=["POST"])
@require_api_ip
def type_text():
    """
    Body (plain text):
      Line 1: "x y"
      Line 2 onward: text to type (may contain newlines)
    Clears the field first, types the text, invalidates cache.
    Response: "OK" or error.
    """
    raw = request.data.decode("utf-8")
    log.info(f"[TYPE] Raw body: {raw!r}")
    try:
        first_newline = raw.index("\n")
        coords_part = raw[:first_newline].strip()
        text        = raw[first_newline + 1:]          # preserve inner newlines
        parts = coords_part.split()
        if len(parts) != 2:
            return Response("Expected 'x y\\ntext'", status=400)
        img_x = float(parts[0])
        img_y = float(parts[1])
        browser.type_at(img_x, img_y, text)
        return Response("OK", mimetype="text/plain")
    except ValueError as e:
        return Response(f"Bad request: {e}", status=400)
    except Exception as e:
        log.exception("Type error")
        return Response(str(e), status=500)


# =========================
# SCREENSHOT (CACHE-AWARE)
# =========================
@app.route("/screenshot", methods=["POST"])
@require_api_ip
def screenshot():
    full = request.args.get("full", "false").lower() in ("true", "1", "yes", "on")
    try:
        url = browser.current_url()
        log.info(f"[SCREENSHOT] URL={url} full={full}")
        if full:
            filename = browser.screenshot_full(url)
        else:
            filename = browser.screenshot_viewport(url)
        # Return "url\ncurrent_page_url" so client can update the address bar
        file_url = format_file_url(filename)
        return Response(f"{file_url}\n{url}", mimetype="text/plain")
    except Exception as e:
        log.exception("Screenshot error")
        return Response(str(e), status=500)


# =========================
# SCROLL
# =========================
@app.route("/scroll/down", methods=["GET"])
@require_api_ip
def scroll_down():
    browser.scroll("down")
    return Response("OK")

@app.route("/scroll/up", methods=["GET"])
@require_api_ip
def scroll_up():
    browser.scroll("up")
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