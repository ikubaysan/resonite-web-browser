"""
browser_scripts.py

Central location for all JavaScript used by Selenium.

Keeping scripts here:
- avoids giant inline strings
- makes server.py readable
- allows reuse
- keeps everything import-safe
"""

# =========================
# NETWORK TRACKER
# =========================

INJECT_NETWORK_TRACKER = r"""
if (!window.__netTracker) {

    window.__netTracker = {
        inFlight: 0,
        lastFinished: performance.now()
    };

    const t = window.__netTracker;

    const origFetch = window.fetch;

    window.fetch = function(...args) {

        t.inFlight++;

        return origFetch.apply(this, args)
            .finally(() => {

                t.inFlight =
                    Math.max(0, t.inFlight - 1);

                t.lastFinished =
                    performance.now();

            });
    };

    const origOpen =
        XMLHttpRequest.prototype.open;

    XMLHttpRequest.prototype.open =
        function(...args) {

            this.addEventListener(
                "loadend",
                () => {

                    t.inFlight =
                        Math.max(
                            0,
                            t.inFlight - 1
                        );

                    t.lastFinished =
                        performance.now();
                }
            );

            t.inFlight++;

            return origOpen.apply(
                this,
                args
            );
        };

}
"""


QUERY_NETWORK = r"""
return (function() {

    const t = window.__netTracker;

    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const pendingImgs =
        Array.from(document.images)
            .filter(i => {

                if (!i.src || i.complete)
                    return false;

                const r =
                    i.getBoundingClientRect();

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
        t
        ? performance.now()
            - t.lastFinished
        : 9999;

    return {

        pendingImgs,
        inFlight,
        msSinceLast

    };

})();
"""


CLICK_AT = r"""
const x = arguments[0];
const y = arguments[1];

let el = document.elementFromPoint(x, y);
if (!el) return;

try {

    let clickable = el;

    while (clickable && clickable !== document.body) {

        if (typeof clickable.click === "function")
            break;

        clickable =
            clickable.parentElement;
    }

    if (clickable &&
        typeof clickable.click === "function") {

        clickable.click();

    } else {

        const evt =
            new MouseEvent("click", {

                bubbles: true,
                cancelable: true,
                view: window,
                clientX: x,
                clientY: y

            });

        el.dispatchEvent(evt);
    }

    if (clickable &&
        typeof clickable.focus === "function") {

        clickable.focus();
    }

}
catch (e) {

    const evt =
        new MouseEvent("click", {

            bubbles: true,
            cancelable: true,
            view: window,
            clientX: x,
            clientY: y

        });

    el.dispatchEvent(evt);
}
"""



IS_INPUT = """
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
            """