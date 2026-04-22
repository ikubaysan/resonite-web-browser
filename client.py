"""
Browser Client
--------------
Requirements: pip install pillow requests
Usage: python client.py [server_base_url]
  e.g. python client.py http://127.0.0.1:5049
"""

import tkinter as tk
from PIL import Image, ImageTk
import sys
import io
import threading
import urllib.request
import urllib.error


# =========================
# CONFIG
# =========================
DEFAULT_SERVER = "http://127.0.0.1:5049"


# =========================
# HELPERS
# =========================

def _http(method: str, url: str, body: bytes = b"") -> str:
    """Tiny HTTP helper — no third-party deps beyond stdlib."""
    req = urllib.request.Request(url, data=body if body else None, method=method)
    req.add_header("Content-Type", "text/plain")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


# =========================
# BROWSER CLIENT
# =========================

class BrowserClient:
    def __init__(self, root, server: str = DEFAULT_SERVER):
        self.root = root
        self.server = server.rstrip("/")
        self.root.title("Browser")
        self.root.configure(bg="#1e1e2e")

        # Image state
        self.pil_image: Image.Image | None = None
        self.tk_image: ImageTk.PhotoImage | None = None
        self._img_x0 = self._img_y0 = 0
        self._img_w  = self._img_h  = 1

        # Loading overlay items
        self._overlay_ids: list[int] = []

        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI                                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Navigation bar ───────────────────────────────────────────────
        nav_bar = tk.Frame(self.root, bg="#181825", pady=5)
        nav_bar.pack(fill=tk.X)

        btn_cfg = dict(
            bg="#313244", fg="#cdd6f4",
            activebackground="#45475a", activeforeground="white",
            font=("Courier", 11, "bold"), relief=tk.FLAT,
            padx=8, pady=3, cursor="hand2"
        )

        tk.Button(nav_bar, text="◀", command=self._go_back,    **btn_cfg).pack(side=tk.LEFT, padx=(8, 2))
        tk.Button(nav_bar, text="▶", command=self._go_forward, **btn_cfg).pack(side=tk.LEFT, padx=2)
        tk.Button(nav_bar, text="↺", command=self._refresh,    **btn_cfg).pack(side=tk.LEFT, padx=2)

        self.url_var = tk.StringVar()
        url_entry = tk.Entry(
            nav_bar, textvariable=self.url_var,
            bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
            font=("Courier", 11), relief=tk.FLAT,
            highlightthickness=1, highlightbackground="#45475a",
            highlightcolor="#7c3aed"
        )
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=4)
        url_entry.bind("<Return>", lambda _: self._navigate())

        tk.Button(
            nav_bar, text="Go",
            command=self._navigate,
            bg="#7c3aed", fg="white", activebackground="#6d28d9",
            font=("Courier", 10, "bold"), relief=tk.FLAT,
            padx=10, pady=3, cursor="hand2"
        ).pack(side=tk.LEFT, padx=(0, 8))

        # ── Main area: canvas + side panel ───────────────────────────────
        main_frame = tk.Frame(self.root, bg="#1e1e2e")
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            main_frame, bg="#11111b", highlightthickness=0, cursor="crosshair"
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Motion>",   self._on_hover)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Leave>",    self._on_leave)
        self.canvas.bind("<Configure>", lambda _: self._render_image())

        # Side panel
        side_panel = tk.Frame(main_frame, bg="#181825", width=60)
        side_panel.pack(side=tk.RIGHT, fill=tk.Y)
        side_panel.pack_propagate(False)

        side_btn_cfg = dict(
            bg="#313244", fg="#cdd6f4",
            activebackground="#45475a", activeforeground="white",
            font=("Courier", 16, "bold"), relief=tk.FLAT,
            width=3, cursor="hand2"
        )
        tk.Button(side_panel, text="▲", command=self._scroll_up,   **side_btn_cfg).pack(side=tk.TOP, padx=8, pady=(20, 6))
        tk.Button(side_panel, text="▼", command=self._scroll_down, **side_btn_cfg).pack(side=tk.TOP, padx=8, pady=(6, 8))

        # ── Status bar ───────────────────────────────────────────────────
        status_bar = tk.Frame(self.root, bg="#181825", pady=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.hover_label = tk.Label(
            status_bar, text="Hover: —",
            bg="#181825", fg="#89b4fa", font=("Courier", 10), anchor="w"
        )
        self.hover_label.pack(side=tk.LEFT, padx=12)

        self.size_label = tk.Label(
            status_bar, text="",
            bg="#181825", fg="#6c7086", font=("Courier", 9)
        )
        self.size_label.pack(side=tk.LEFT, padx=20)

        self.click_label = tk.Label(
            status_bar, text="Click: —",
            bg="#181825", fg="#a6e3a1", font=("Courier", 10, "bold"), anchor="e"
        )
        self.click_label.pack(side=tk.RIGHT, padx=12)

    # ------------------------------------------------------------------ #
    #  Loading overlay                                                     #
    # ------------------------------------------------------------------ #

    def _show_loading(self, msg="Loading…"):
        self._hide_loading()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        cx, cy = cw // 2, ch // 2
        rect = self.canvas.create_rectangle(
            cx - 100, cy - 22, cx + 100, cy + 22,
            fill="#313244", outline="#7c3aed", width=2
        )
        text = self.canvas.create_text(
            cx, cy, text=msg, fill="#cdd6f4", font=("Courier", 11)
        )
        self._overlay_ids = [rect, text]

    def _hide_loading(self):
        for item in self._overlay_ids:
            self.canvas.delete(item)
        self._overlay_ids = []

    # ------------------------------------------------------------------ #
    #  Server calls (run in threads to keep UI responsive)                 #
    # ------------------------------------------------------------------ #

    def _thread(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _navigate(self):
        url_text = self.url_var.get().strip()
        if not url_text:
            return
        self._show_loading("Navigating…")
        self._thread(self._do_navigate, url_text)

    def _do_navigate(self, url_text: str):
        try:
            _http("POST", f"{self.server}/navigate", url_text.encode())
            self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _refresh(self):
        self._show_loading("Refreshing…")
        self._thread(self._do_screenshot)

    def _go_back(self):
        self._show_loading("Going back…")
        self._thread(self._do_back)

    def _do_back(self):
        try:
            _http("GET", f"{self.server}/back")
            self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _go_forward(self):
        self._show_loading("Going forward…")
        self._thread(self._do_forward)

    def _do_forward(self):
        try:
            _http("GET", f"{self.server}/forward")
            self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _scroll_up(self):
        self._thread(self._do_scroll, "up")

    def _scroll_down(self):
        self._thread(self._do_scroll, "down")

    def _do_scroll(self, direction: str):
        try:
            _http("GET", f"{self.server}/scroll/{direction}")
            self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _do_screenshot(self):
        """Request screenshot; response is 'file_url\ncurrent_page_url'."""
        try:
            body = _http("POST", f"{self.server}/screenshot")
            lines = body.strip().splitlines()
            file_url   = lines[0].strip()
            page_url   = lines[1].strip() if len(lines) > 1 else ""

            # Resolve relative URLs
            if file_url.startswith("/"):
                file_url = self.server + file_url

            # Fetch the image bytes
            with urllib.request.urlopen(file_url, timeout=30) as resp:
                img_bytes = resp.read()

            img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            self.root.after(0, self._set_image, img, page_url)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _send_click_and_refresh(self, img_x: float, img_y: float):
        """POST click coords; if server signals TEXT_INPUT, open typing modal."""
        try:
            body = f"{img_x:.2f} {img_y:.2f}".encode()
            response = _http("POST", f"{self.server}/click", body)
            lines = response.strip().splitlines()
            status = lines[0].strip() if lines else "OK"

            if status == "TEXT_INPUT":
                # Don't take a screenshot — show the typing modal instead
                self.root.after(0, self._hide_loading)
                self.root.after(0, self._open_text_modal, img_x, img_y)
            else:
                self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _open_text_modal(self, img_x: float, img_y: float):
        """Pop up a small modal for the user to type into the focused field."""
        modal = tk.Toplevel(self.root)
        modal.title("Type text")
        modal.configure(bg="#181825")
        modal.resizable(False, False)
        modal.transient(self.root)
        modal.grab_set()

        # Centre over main window
        self.root.update_idletasks()
        rx = self.root.winfo_rootx() + self.root.winfo_width()  // 2 - 200
        ry = self.root.winfo_rooty() + self.root.winfo_height() // 2 - 70
        modal.geometry(f"400x140+{rx}+{ry}")

        tk.Label(
            modal, text="Enter text for the selected field:",
            bg="#181825", fg="#a6adc8", font=("Courier", 10)
        ).pack(pady=(14, 4), padx=16, anchor="w")

        entry_var = tk.StringVar()
        entry = tk.Entry(
            modal, textvariable=entry_var,
            bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
            font=("Courier", 12), relief=tk.FLAT,
            highlightthickness=1, highlightbackground="#45475a",
            highlightcolor="#7c3aed"
        )
        entry.pack(fill=tk.X, padx=16, ipady=5)
        entry.focus_set()

        btn_row = tk.Frame(modal, bg="#181825")
        btn_row.pack(pady=12)

        def submit():
            text = entry_var.get()
            modal.destroy()
            if text:
                self._show_loading("Typing…")
                self._thread(self._do_type, img_x, img_y, text)

        def cancel():
            modal.destroy()
            # Still take a screenshot so the display stays current
            self._thread(self._do_screenshot)

        tk.Button(
            btn_row, text="Send",
            command=submit,
            bg="#7c3aed", fg="white", activebackground="#6d28d9",
            font=("Courier", 10, "bold"), relief=tk.FLAT,
            padx=14, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_row, text="Cancel",
            command=cancel,
            bg="#313244", fg="#cdd6f4", activebackground="#45475a",
            font=("Courier", 10), relief=tk.FLAT,
            padx=14, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT)

        entry.bind("<Return>", lambda _: submit())
        entry.bind("<Escape>", lambda _: cancel())

    def _do_type(self, img_x: float, img_y: float, text: str):
        """POST /type then take a fresh screenshot."""
        try:
            body = f"{img_x:.2f} {img_y:.2f}\n{text}".encode("utf-8")
            _http("POST", f"{self.server}/type", body)
            self._do_screenshot()
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    # ------------------------------------------------------------------ #
    #  Image display                                                       #
    # ------------------------------------------------------------------ #

    def _set_image(self, img: Image.Image, page_url: str = ""):
        self.pil_image = img
        iw, ih = img.size
        self.size_label.config(text=f"Image: {iw} × {ih} px")
        if page_url:
            self.url_var.set(page_url)
        self._render_image()

    def _render_image(self):
        if self.pil_image is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 2 or ch < 2:
            self.root.after(50, self._render_image)
            return

        iw, ih = self.pil_image.size
        scale = min(cw / iw, ch / ih, 1.0)
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))

        display = self.pil_image.resize((new_w, new_h), Image.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(display)

        self._img_x0 = (cw - new_w) // 2
        self._img_y0 = (ch - new_h) // 2
        self._img_w  = new_w
        self._img_h  = new_h

        self.canvas.delete("all")
        self.canvas.create_image(self._img_x0, self._img_y0, anchor=tk.NW, image=self.tk_image)
        self._hide_loading()

    # ------------------------------------------------------------------ #
    #  Coordinate conversion                                               #
    # ------------------------------------------------------------------ #

    def _canvas_to_image_coords(self, cx: int, cy: int):
        """Canvas pixel → centred image coordinate system."""
        if self.pil_image is None:
            return None, None
        iw, ih = self.pil_image.size

        rel_x = max(0, min(self._img_w, cx - self._img_x0))
        rel_y = max(0, min(self._img_h, cy - self._img_y0))

        norm_x = rel_x / self._img_w
        norm_y = rel_y / self._img_h

        img_x = (norm_x - 0.5) * iw
        img_y = (0.5 - norm_y) * ih        # y flipped
        return img_x, img_y

    def _is_over_image(self, cx: int, cy: int) -> bool:
        if self.pil_image is None:
            return False
        return (self._img_x0 <= cx <= self._img_x0 + self._img_w and
                self._img_y0 <= cy <= self._img_y0 + self._img_h)

    # ------------------------------------------------------------------ #
    #  Mouse events                                                        #
    # ------------------------------------------------------------------ #

    def _on_hover(self, event):
        if not self._is_over_image(event.x, event.y):
            self.hover_label.config(text="Hover: (outside image)")
            return
        x, y = self._canvas_to_image_coords(event.x, event.y)
        self.hover_label.config(text=f"Hover:  x={x:+.1f},  y={y:+.1f}")

    def _on_leave(self, event):
        self.hover_label.config(text="Hover: —")

    def _on_click(self, event):
        if not self._is_over_image(event.x, event.y):
            return

        x, y = self._canvas_to_image_coords(event.x, event.y)
        self.click_label.config(text=f"Click:  x={x:+.1f},  y={y:+.1f}")

        # Flash crosshair
        r = 8
        h_line = self.canvas.create_line(event.x - r, event.y, event.x + r, event.y, fill="#f38ba8", width=2)
        v_line = self.canvas.create_line(event.x, event.y - r, event.x, event.y + r, fill="#f38ba8", width=2)
        self.root.after(400, lambda: self.canvas.delete(h_line, v_line))

        # Send to server and refresh
        self._show_loading("Clicking…")
        self._thread(self._send_click_and_refresh, x, y)

    # ------------------------------------------------------------------ #
    #  Error display                                                       #
    # ------------------------------------------------------------------ #

    def _show_error(self, msg: str):
        self._hide_loading()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        cx, cy = cw // 2, ch // 2
        rect = self.canvas.create_rectangle(
            cx - 220, cy - 30, cx + 220, cy + 30,
            fill="#1e1e2e", outline="#f38ba8", width=2
        )
        text = self.canvas.create_text(
            cx, cy, text=f"Error: {msg}", fill="#f38ba8",
            font=("Courier", 10), width=420
        )
        self._overlay_ids = [rect, text]


# =========================
# ENTRY POINT
# =========================

def main():
    server = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SERVER

    root = tk.Tk()
    root.geometry("1100x700")
    root.minsize(500, 350)

    BrowserClient(root, server)
    root.mainloop()


if __name__ == "__main__":
    main()