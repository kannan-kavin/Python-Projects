"""Simple GUI image/PDF converter with resize, background removal, live preview."""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from pillow_heif import register_heif_opener
from pdf2image import convert_from_path

register_heif_opener()

_rembg_session = None
_rembg_lock = threading.Lock()


def _get_rembg():
    """Lazy-load the rembg model. First call downloads ~170MB; subsequent are instant."""
    global _rembg_session
    with _rembg_lock:
        if _rembg_session is None:
            from rembg import new_session

            _rembg_session = new_session("u2net")
    return _rembg_session


def _detect_edge_bg_color(img: Image.Image) -> tuple[int, int, int]:
    """Sample edge pixels; return black or white to match the existing background."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    strip = max(1, min(w, h) // 40)
    edges = [
        rgb.crop((0, 0, w, strip)),
        rgb.crop((0, h - strip, w, h)),
        rgb.crop((0, 0, strip, h)),
        rgb.crop((w - strip, 0, w, h)),
    ]
    total, count = 0.0, 0
    for e in edges:
        gray = e.convert("L")
        total += sum(gray.getdata())
        count += gray.size[0] * gray.size[1]
    mean = total / max(count, 1)
    return (255, 255, 255) if mean >= 128 else (0, 0, 0)


def remove_background(img: Image.Image, extend_bg: bool = False) -> Image.Image:
    from rembg import remove

    src = img.convert("RGBA")
    if not extend_bg:
        return remove(src, session=_get_rembg())

    w, h = src.size
    pad = max(20, min(w, h) // 7)
    bg_color = _detect_edge_bg_color(src)
    padded = Image.new("RGB", (w + 2 * pad, h + 2 * pad), bg_color)
    padded.paste(src.convert("RGB"), (pad, pad))

    cut = remove(padded, session=_get_rembg())
    return cut.crop((pad, pad, pad + w, pad + h))

SUPPORTED_INPUTS = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}
OUTPUT_FORMATS = ["PDF", "JPG", "PNG", "HEIC"]

PIL_FORMAT = {"JPG": "JPEG", "PNG": "PNG", "HEIC": "HEIF", "PDF": "PDF"}
PIL_EXT = {"JPG": ".jpg", "PNG": ".png", "HEIC": ".heic", "PDF": ".pdf"}

BG = "#0f0f0f"
CARD = "#1a1a1a"
FIELD = "#262626"
INK = "#f5f5f5"
MUTED = "#9a9a9a"
ACCENT = "#a03535"
ACCENT_HOVER = "#c04545"
BORDER = "#2e2e2e"
SUCCESS = "#7bb389"
DANGER = "#e06565"

PREVIEW_BOX = 260


def load_pages(path: Path) -> list[Image.Image]:
    if path.suffix.lower() == ".pdf":
        return convert_from_path(str(path))
    return [Image.open(path)]


def flatten_for_jpeg(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def resize_pages(
    pages: list[Image.Image], size: tuple[int, int] | None
) -> list[Image.Image]:
    if size is None:
        return pages
    return [p.resize(size, Image.LANCZOS) for p in pages]


def convert(
    input_path: Path,
    out_dir: Path,
    out_format: str,
    resize: tuple[int, int] | None = None,
    remove_bg: bool = False,
    extend_bg: bool = False,
) -> list[Path]:
    pages = resize_pages(load_pages(input_path), resize)
    if remove_bg:
        pages = [remove_background(p, extend_bg=extend_bg) for p in pages]
    ext = PIL_EXT[out_format]
    pil_fmt = PIL_FORMAT[out_format]
    stem = input_path.stem
    written: list[Path] = []

    if out_format == "PDF":
        rgb_pages = [flatten_for_jpeg(p) for p in pages]
        out_path = out_dir / f"{stem}{ext}"
        rgb_pages[0].save(
            out_path,
            "PDF",
            save_all=True,
            append_images=rgb_pages[1:] if len(rgb_pages) > 1 else [],
        )
        written.append(out_path)
        return written

    needs_flatten = out_format in {"JPG", "HEIC"}
    for i, page in enumerate(pages):
        img = flatten_for_jpeg(page) if needs_flatten else page
        suffix = f"_p{i + 1}" if len(pages) > 1 else ""
        out_path = out_dir / f"{stem}{suffix}{ext}"
        img.save(out_path, pil_fmt)
        written.append(out_path)
    return written


class HoverButton(tk.Canvas):
    def __init__(
        self,
        parent,
        text,
        command,
        bg=ACCENT,
        hover=ACCENT_HOVER,
        fg="white",
        width=180,
        height=44,
        font=("Helvetica Neue", 13, "bold"),
    ):
        super().__init__(
            parent, width=width, height=height, bg=parent["bg"], highlightthickness=0
        )
        self.command = command
        self.bg_color = bg
        self.hover_color = hover
        self.rect = self.create_rectangle(0, 0, width, height, fill=bg, outline=bg)
        self.label = self.create_text(
            width // 2, height // 2, text=text, fill=fg, font=font
        )
        self.bind("<Enter>", lambda _e: self._set(self.hover_color))
        self.bind("<Leave>", lambda _e: self._set(self.bg_color))
        self.bind("<Button-1>", lambda _e: self.command())

    def _set(self, color):
        self.itemconfig(self.rect, fill=color, outline=color)


class ConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Image Converter, Resizer, and Remover")
        root.geometry("560x900")
        root.resizable(True, True)
        root.configure(bg=BG)
        root.after(50, self._zoom_window)

        self.input_path: Path | None = None
        self.source_image: Image.Image | None = None
        self.orig_w = 0
        self.orig_h = 0
        self._preview_after: str | None = None
        self._syncing = False

        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.lock_ratio = tk.BooleanVar(value=True)
        self.format_var = tk.StringVar(value="PNG")
        self.remove_bg_var = tk.BooleanVar(value=False)
        self.extend_bg_var = tk.BooleanVar(value=False)

        self._bg_removed_cache: Image.Image | None = None
        self._bg_cache_key: tuple | None = None
        self._bg_thread: threading.Thread | None = None

        self.width_var.trace_add("write", lambda *_: self._on_width_change())
        self.height_var.trace_add("write", lambda *_: self._on_height_change())
        self.remove_bg_var.trace_add("write", lambda *_: self._on_remove_bg_toggle())
        self.extend_bg_var.trace_add("write", lambda *_: self._on_extend_bg_toggle())

        self._configure_styles()
        self._build_ui()

    def _zoom_window(self):
        """Expand to fill the screen like clicking the green zoom button."""
        try:
            self.root.wm_attributes("-zoomed", True)
        except tk.TclError:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry(f"{sw}x{sh - 30}+0+30")

    def _configure_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("BG.TFrame", background=BG)
        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=INK,
            font=("Helvetica Neue", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Helvetica Neue", 12),
        )
        style.configure(
            "SectionLabel.TLabel",
            background=CARD,
            foreground=MUTED,
            font=("Helvetica Neue", 11, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Helvetica Neue", 11),
        )
        style.configure(
            "Dims.TLabel",
            background=CARD,
            foreground=MUTED,
            font=("Helvetica Neue", 11),
        )
        style.configure(
            "Lock.TCheckbutton",
            background=CARD,
            foreground=INK,
            font=("Helvetica Neue", 11),
        )
        style.map(
            "Lock.TCheckbutton",
            background=[("active", CARD)],
            foreground=[("active", INK)],
        )

    def _build_ui(self):
        header = ttk.Frame(self.root, style="BG.TFrame")
        header.pack(fill="x", padx=28, pady=(24, 0))
        ttk.Label(
            header,
            text="Image Converter, Resizer, and Remover",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Convert between PDF, JPG, PNG, and HEIC.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        card = tk.Frame(
            self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1
        )
        card.pack(fill="x", padx=28, pady=20)

        ttk.Label(card, text="INPUT FILE", style="SectionLabel.TLabel").pack(
            anchor="w", padx=20, pady=(18, 6)
        )

        self.drop_zone = tk.Frame(
            card,
            bg=FIELD,
            highlightbackground=BORDER,
            highlightthickness=1,
            cursor="hand2",
        )
        self.drop_zone.pack(fill="x", padx=20, pady=(0, 12))
        self.drop_zone.bind("<Button-1>", lambda _e: self.choose_input())

        self.file_label = tk.Label(
            self.drop_zone,
            text="Click to choose a file",
            bg=FIELD,
            fg=MUTED,
            font=("Helvetica Neue", 13, "italic"),
            pady=18,
            cursor="hand2",
        )
        self.file_label.pack()
        self.file_label.bind("<Button-1>", lambda _e: self.choose_input())

        ttk.Label(card, text="PREVIEW", style="SectionLabel.TLabel").pack(
            anchor="w", padx=20, pady=(6, 6)
        )
        preview_wrap = tk.Frame(card, bg=CARD)
        preview_wrap.pack(padx=20, pady=(0, 10))
        self.preview_canvas = tk.Canvas(
            preview_wrap,
            width=PREVIEW_BOX,
            height=PREVIEW_BOX,
            bg=FIELD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.preview_canvas.pack()
        self._preview_photo = None
        self._draw_placeholder("No file selected")

        self.dims_label = ttk.Label(
            card, text="Original: —    Output: —", style="Dims.TLabel"
        )
        self.dims_label.pack(pady=(0, 10))

        resize_row = tk.Frame(card, bg=CARD)
        resize_row.pack(padx=20, pady=(0, 4))
        tk.Label(
            resize_row,
            text="Width",
            bg=CARD,
            fg=MUTED,
            font=("Helvetica Neue", 11),
        ).grid(row=0, column=0, padx=(0, 6))
        self.width_entry = tk.Entry(
            resize_row,
            textvariable=self.width_var,
            width=7,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            insertwidth=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Helvetica Neue", 12),
            cursor="xterm",
        )
        self.width_entry.grid(row=0, column=1, padx=(0, 12), ipady=4)
        tk.Label(
            resize_row,
            text="Height",
            bg=CARD,
            fg=MUTED,
            font=("Helvetica Neue", 11),
        ).grid(row=0, column=2, padx=(0, 6))
        self.height_entry = tk.Entry(
            resize_row,
            textvariable=self.height_var,
            width=7,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            insertwidth=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Helvetica Neue", 12),
            cursor="xterm",
        )
        self.height_entry.grid(row=0, column=3, padx=(0, 12), ipady=4)
        ttk.Checkbutton(
            resize_row,
            text="Lock aspect",
            variable=self.lock_ratio,
            style="Lock.TCheckbutton",
        ).grid(row=0, column=4)

        tk.Label(
            card,
            text="Leave blank to keep original size.",
            bg=CARD,
            fg=MUTED,
            font=("Helvetica Neue", 10, "italic"),
        ).pack(pady=(4, 12))

        bg_row = tk.Frame(card, bg=CARD)
        bg_row.pack(fill="x", padx=20, pady=(0, 4))
        ttk.Checkbutton(
            bg_row,
            text="Remove background",
            variable=self.remove_bg_var,
            style="Lock.TCheckbutton",
        ).pack(side="left")
        self.bg_status = tk.Label(
            bg_row,
            text="",
            bg=CARD,
            fg=MUTED,
            font=("Helvetica Neue", 10, "italic"),
        )
        self.bg_status.pack(side="left", padx=(10, 0))

        extend_row = tk.Frame(card, bg=CARD)
        extend_row.pack(fill="x", padx=40, pady=(0, 16))
        ttk.Checkbutton(
            extend_row,
            text="Pad edges first (helps on tight crops)",
            variable=self.extend_bg_var,
            style="Lock.TCheckbutton",
        ).pack(side="left")

        ttk.Label(card, text="CONVERT TO", style="SectionLabel.TLabel").pack(
            anchor="w", padx=20, pady=(0, 6)
        )
        format_row = tk.Frame(card, bg=CARD)
        format_row.pack(fill="x", padx=20, pady=(0, 20))
        self._chips = {}
        for fmt in OUTPUT_FORMATS:
            chip = self._format_chip(format_row, fmt)
            chip.pack(side="left", padx=(0, 8))
            self._chips[fmt] = chip

        HoverButton(
            self.root, text="Convert", command=self.run_convert, width=504, height=48
        ).pack(pady=(0, 12), padx=28)

        self.status = ttk.Label(self.root, text="", style="Status.TLabel")
        self.status.pack(pady=(0, 20))

    def _format_chip(self, parent, fmt: str) -> tk.Label:
        chip = tk.Label(
            parent,
            text=fmt,
            font=("Helvetica Neue", 12, "bold"),
            padx=14,
            pady=6,
            cursor="arrow",
        )
        self._paint_chip(chip, fmt)
        chip.bind("<Button-1>", lambda _e, f=fmt: self._select_format(f))
        return chip

    def _select_format(self, fmt: str):
        self.format_var.set(fmt)
        for f, chip in self._chips.items():
            self._paint_chip(chip, f)

    def _paint_chip(self, chip: tk.Label, fmt: str):
        if self.format_var.get() == fmt:
            chip.configure(bg=ACCENT, fg="white")
        else:
            chip.configure(bg=FIELD, fg=INK)

    def _draw_placeholder(self, text: str):
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            PREVIEW_BOX // 2,
            PREVIEW_BOX // 2,
            text=text,
            fill=MUTED,
            font=("Helvetica Neue", 12, "italic"),
        )

    def choose_input(self):
        filetypes = [
            ("Supported", "*.pdf *.jpg *.jpeg *.png *.heic"),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(filetypes=filetypes)
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in SUPPORTED_INPUTS:
            messagebox.showerror(
                "Unsupported", f"Can't read {p.suffix} — pick a PDF/JPG/PNG/HEIC."
            )
            return
        try:
            pages = load_pages(p)
        except Exception as e:
            messagebox.showerror("Can't open file", str(e))
            return

        self.input_path = p
        self.source_image = pages[0].copy()
        self.orig_w, self.orig_h = self.source_image.size
        self._bg_removed_cache = None
        self._bg_cache_key = None
        page_note = f"  ({len(pages)} pages)" if len(pages) > 1 else ""
        self.file_label.configure(
            text=f"  {p.name}{page_note}",
            fg=INK,
            font=("Helvetica Neue", 13),
        )

        self._syncing = True
        self.width_var.set(str(self.orig_w))
        self.height_var.set(str(self.orig_h))
        self._syncing = False
        if self.remove_bg_var.get():
            self._start_bg_removal()
        else:
            self._schedule_preview()

    def _on_width_change(self):
        if self._syncing or self.source_image is None:
            return
        if self.lock_ratio.get():
            w = self._parse_int(self.width_var.get())
            if w and self.orig_w:
                h = max(1, round(w * self.orig_h / self.orig_w))
                self._syncing = True
                self.height_var.set(str(h))
                self._syncing = False
        self._schedule_preview()

    def _on_height_change(self):
        if self._syncing or self.source_image is None:
            return
        if self.lock_ratio.get():
            h = self._parse_int(self.height_var.get())
            if h and self.orig_h:
                w = max(1, round(h * self.orig_w / self.orig_h))
                self._syncing = True
                self.width_var.set(str(w))
                self._syncing = False
        self._schedule_preview()

    def _parse_int(self, s: str) -> int | None:
        try:
            v = int(s)
            return v if v > 0 else None
        except ValueError:
            return None

    def _schedule_preview(self):
        if self._preview_after is not None:
            self.root.after_cancel(self._preview_after)
        self._preview_after = self.root.after(120, self._update_preview)

    def _target_size(self) -> tuple[int, int] | None:
        w = self._parse_int(self.width_var.get())
        h = self._parse_int(self.height_var.get())
        if w and h:
            return (w, h)
        return None

    def _preview_source(self) -> Image.Image | None:
        if (
            self.remove_bg_var.get()
            and self._bg_removed_cache is not None
            and self._bg_cache_key == self._current_bg_key()
        ):
            return self._bg_removed_cache
        return self.source_image

    def _update_preview(self):
        self._preview_after = None
        src = self._preview_source()
        if src is None:
            return
        target = self._target_size() or (self.orig_w, self.orig_h)
        tw, th = target
        try:
            resized = src.resize((tw, th), Image.LANCZOS)
        except Exception:
            self._draw_placeholder("Invalid size")
            return

        scale = min(PREVIEW_BOX / tw, PREVIEW_BOX / th, 1.0)
        if scale < 1.0:
            disp_w = max(1, int(tw * scale))
            disp_h = max(1, int(th * scale))
            display_img = resized.resize((disp_w, disp_h), Image.LANCZOS)
        else:
            display_img = resized

        show = display_img.convert("RGBA")
        show_on_checker = self._composite_on_checker(show)
        self._preview_photo = ImageTk.PhotoImage(show_on_checker)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            PREVIEW_BOX // 2, PREVIEW_BOX // 2, image=self._preview_photo
        )
        self.dims_label.configure(
            text=f"Original: {self.orig_w}×{self.orig_h}    Output: {tw}×{th}"
        )

    def _composite_on_checker(self, img: Image.Image) -> Image.Image:
        """Paste RGBA image over a checkerboard so transparency is visible."""
        if img.mode != "RGBA":
            return img
        w, h = img.size
        square = 10
        checker = Image.new("RGBA", (w, h), (40, 40, 40, 255))
        light = (60, 60, 60, 255)
        for y in range(0, h, square):
            for x in range(0, w, square):
                if ((x // square) + (y // square)) % 2 == 0:
                    for dy in range(square):
                        for dx in range(square):
                            if x + dx < w and y + dy < h:
                                checker.putpixel((x + dx, y + dy), light)
        checker.alpha_composite(img)
        return checker

    def _current_bg_key(self) -> tuple:
        return (id(self.source_image), self.extend_bg_var.get())

    def _on_remove_bg_toggle(self):
        if self.source_image is None:
            return
        if self.remove_bg_var.get():
            if self._bg_cache_key != self._current_bg_key():
                self._start_bg_removal()
            else:
                self._schedule_preview()
        else:
            self.bg_status.configure(text="")
            self._schedule_preview()

    def _on_extend_bg_toggle(self):
        if not self.remove_bg_var.get() or self.source_image is None:
            return
        self._start_bg_removal()

    def _start_bg_removal(self):
        if self._bg_thread and self._bg_thread.is_alive():
            return
        src = self.source_image
        if src is None:
            return
        extend = self.extend_bg_var.get()
        note = " (padded)" if extend else ""
        self.bg_status.configure(
            text=f"Removing…{note}", fg=MUTED
        )
        self._draw_placeholder("Removing background…")
        key = self._current_bg_key()

        def worker():
            try:
                result = remove_background(src, extend_bg=extend)
            except Exception as e:
                self.root.after(0, lambda: self._bg_failed(str(e)))
                return
            self.root.after(0, lambda: self._bg_done(result, key))

        self._bg_thread = threading.Thread(target=worker, daemon=True)
        self._bg_thread.start()

    def _bg_done(self, result: Image.Image, key: tuple):
        if key != self._current_bg_key():
            return
        self._bg_removed_cache = result
        self._bg_cache_key = key
        note = " (padded)" if self.extend_bg_var.get() else ""
        self.bg_status.configure(text=f"Background removed{note} ✓", fg=SUCCESS)
        self._schedule_preview()

    def _bg_failed(self, msg: str):
        self.bg_status.configure(text="Failed", fg=DANGER)
        messagebox.showerror("Background removal failed", msg)
        self.remove_bg_var.set(False)

    def run_convert(self):
        if not self.input_path:
            messagebox.showwarning("No file", "Pick an input file first.")
            return
        out_format = self.format_var.get()
        target = self._target_size()
        if target and target == (self.orig_w, self.orig_h):
            target = None
        remove_bg = self.remove_bg_var.get()

        if remove_bg and out_format in {"JPG", "PDF"}:
            proceed = messagebox.askokcancel(
                "Transparency will be lost",
                f"{out_format} doesn't support transparency — the removed background "
                f"will be flattened to white. Continue?",
            )
            if not proceed:
                return

        out_dir = filedialog.askdirectory(title="Save into folder…")
        if not out_dir:
            return

        self.status.configure(
            text="Converting…" + (" (removing background)" if remove_bg else ""),
            foreground=MUTED,
        )
        self.root.update_idletasks()

        try:
            written = convert(
                self.input_path,
                Path(out_dir),
                out_format,
                resize=target,
                remove_bg=remove_bg,
                extend_bg=self.extend_bg_var.get(),
            )
        except Exception as e:
            messagebox.showerror("Conversion failed", str(e))
            self.status.configure(text="Conversion failed.", foreground=DANGER)
            return
        names = ", ".join(p.name for p in written)
        self.status.configure(text=f"✓ Wrote {names}", foreground=SUCCESS)


def main():
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
