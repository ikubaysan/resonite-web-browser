"""
Image Viewer with Coordinate Display
--------------------------------------
Requirements: pip install pillow

Usage: python image_viewer.py [optional_image_path.png]
"""

import tkinter as tk
from tkinter import filedialog, ttk
from PIL import Image, ImageTk
import sys
import os


class ImageViewer:
    def __init__(self, root, image_path=None):
        self.root = root
        self.root.title("Image Viewer")
        self.root.configure(bg="#1e1e2e")

        # State
        self.pil_image = None       # Original PIL image
        self.tk_image = None        # Tkinter-compatible image (reference kept to avoid GC)
        self.image_path = None

        self._build_ui()

        if image_path and os.path.isfile(image_path):
            self._load_image(image_path)

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────
        top_bar = tk.Frame(self.root, bg="#181825", pady=6)
        top_bar.pack(fill=tk.X)

        open_btn = tk.Button(
            top_bar, text="Open Image…",
            command=self._open_file_dialog,
            bg="#7c3aed", fg="white", activebackground="#6d28d9",
            font=("Courier", 10, "bold"), relief=tk.FLAT,
            padx=12, pady=4, cursor="hand2"
        )
        open_btn.pack(side=tk.LEFT, padx=10)

        self.title_label = tk.Label(
            top_bar, text="No image loaded",
            bg="#181825", fg="#a6adc8",
            font=("Courier", 10)
        )
        self.title_label.pack(side=tk.LEFT, padx=6)

        # ── Main area: canvas + side buttons ─────────────────────────────
        main_frame = tk.Frame(self.root, bg="#1e1e2e")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Canvas (image lives here)
        self.canvas = tk.Canvas(
            main_frame, bg="#11111b", highlightthickness=0,
            cursor="crosshair"
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bind mouse events
        self.canvas.bind("<Motion>",   self._on_hover)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Leave>",    self._on_leave)

        # Side panel (up / down buttons)
        side_panel = tk.Frame(main_frame, bg="#181825", width=60)
        side_panel.pack(side=tk.RIGHT, fill=tk.Y)
        side_panel.pack_propagate(False)

        btn_cfg = dict(
            bg="#313244", fg="#cdd6f4",
            activebackground="#45475a", activeforeground="white",
            font=("Courier", 16, "bold"), relief=tk.FLAT,
            width=3, cursor="hand2"
        )

        self.up_btn = tk.Button(
            side_panel, text="▲",
            command=self._on_up, **btn_cfg
        )
        self.up_btn.pack(side=tk.TOP, padx=8, pady=(20, 6))

        self.down_btn = tk.Button(
            side_panel, text="▼",
            command=self._on_down, **btn_cfg
        )
        self.down_btn.pack(side=tk.TOP, padx=8, pady=(6, 8))

        # ── Status bar ───────────────────────────────────────────────────
        status_bar = tk.Frame(self.root, bg="#181825", pady=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Hover coords (left)
        self.hover_label = tk.Label(
            status_bar,
            text="Hover: —",
            bg="#181825", fg="#89b4fa",
            font=("Courier", 10), anchor="w"
        )
        self.hover_label.pack(side=tk.LEFT, padx=12)

        # Click coords (right)
        self.click_label = tk.Label(
            status_bar,
            text="Click: —",
            bg="#181825", fg="#a6e3a1",
            font=("Courier", 10, "bold"), anchor="e"
        )
        self.click_label.pack(side=tk.RIGHT, padx=12)

        # Image size info (centre)
        self.size_label = tk.Label(
            status_bar, text="",
            bg="#181825", fg="#6c7086",
            font=("Courier", 9)
        )
        self.size_label.pack(side=tk.LEFT, padx=20)

        # Recalculate image position when the canvas is resized
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    # ------------------------------------------------------------------ #
    #  Image loading & rendering                                           #
    # ------------------------------------------------------------------ #

    def _open_file_dialog(self):
        path = filedialog.askopenfilename(
            title="Select an image",
            filetypes=[("PNG files", "*.png"),
                       ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff"),
                       ("All files", "*.*")]
        )
        if path:
            self._load_image(path)

    def _load_image(self, path):
        try:
            self.pil_image = Image.open(path).convert("RGBA")
        except Exception as exc:
            self.title_label.config(text=f"Error: {exc}")
            return

        self.image_path = path
        filename = os.path.basename(path)
        w, h = self.pil_image.size
        self.title_label.config(text=filename)
        self.size_label.config(text=f"Image: {w} × {h} px")
        self._render_image()

    def _render_image(self):
        """Draw the image centred on the canvas, scaled to fit."""
        if self.pil_image is None:
            return

        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 2 or ch < 2:
            # Canvas not yet realised; try again shortly
            self.root.after(50, self._render_image)
            return

        iw, ih = self.pil_image.size
        scale = min(cw / iw, ch / ih, 1.0)   # never upscale beyond 1:1
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))

        display = self.pil_image.resize((new_w, new_h), Image.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(display)

        # Store rendered geometry for coordinate mapping
        self._img_x0 = (cw - new_w) // 2   # top-left corner on canvas
        self._img_y0 = (ch - new_h) // 2
        self._img_w  = new_w
        self._img_h  = new_h

        self.canvas.delete("all")
        self.canvas.create_image(
            self._img_x0, self._img_y0,
            anchor=tk.NW, image=self.tk_image
        )

    def _on_canvas_resize(self, event):
        self._render_image()

    # ------------------------------------------------------------------ #
    #  Coordinate conversion                                               #
    # ------------------------------------------------------------------ #

    def _canvas_to_image_coords(self, cx, cy):
        """
        Convert canvas pixel (cx, cy) to centred image coordinates.

        Rules:
          x: left edge → -imgW/2,  right edge → +imgW/2
          y: top edge  → +imgH/2,  bottom edge → -imgH/2   (y flipped)
        """
        if self.pil_image is None:
            return None, None

        iw, ih = self.pil_image.size

        # Position relative to the displayed image's top-left corner
        rel_x = cx - self._img_x0
        rel_y = cy - self._img_y0

        # Clamp to image bounds
        rel_x = max(0, min(self._img_w, rel_x))
        rel_y = max(0, min(self._img_h, rel_y))

        # Normalise to [0, 1] in display space → apply to original image dims
        norm_x = rel_x / self._img_w
        norm_y = rel_y / self._img_h

        # Map to centred coordinate system
        img_x = (norm_x - 0.5) * iw          # -iw/2 … +iw/2
        img_y = (0.5 - norm_y) * ih           # +ih/2 … -ih/2  (y flipped)

        return img_x, img_y

    def _is_over_image(self, cx, cy):
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

        # Visual feedback: brief crosshair flash
        r = 8
        h = self.canvas.create_line(event.x - r, event.y, event.x + r, event.y,
                                    fill="#f38ba8", width=2)
        v = self.canvas.create_line(event.x, event.y - r, event.x, event.y + r,
                                    fill="#f38ba8", width=2)
        self.root.after(400, lambda: self.canvas.delete(h, v))

    # ------------------------------------------------------------------ #
    #  Up / Down buttons (placeholder)                                     #
    # ------------------------------------------------------------------ #

    def _on_up(self):
        """Up button pressed — add your logic here."""
        print("Up button pressed")

    def _on_down(self):
        """Down button pressed — add your logic here."""
        print("Down button pressed")


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

def main():
    image_path = sys.argv[1] if len(sys.argv) > 1 else None

    root = tk.Tk()
    root.geometry("900x620")
    root.minsize(400, 300)

    app = ImageViewer(root, image_path)
    root.mainloop()


if __name__ == "__main__":
    main()