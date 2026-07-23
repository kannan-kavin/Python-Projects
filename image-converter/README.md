# Image Converter, Resizer, and Remover

A tiny local GUI to convert between **PDF, JPG, PNG, and HEIC** — any direction, with optional resizing and AI background removal.

Pick a file, pick a size, optionally remove the background, pick an output format, pick a save folder. That's it.

Features:
- Convert any direction among PDF / JPG / PNG / HEIC
- Resize with aspect-ratio lock and a live preview
- One-click background removal (via [rembg](https://github.com/danielgatis/rembg))

## Requirements

- **Python 3.10+**
- **Poppler** (needed for reading PDFs)
  - macOS: `brew install poppler`
  - Ubuntu/Debian: `sudo apt install poppler-utils`
  - Windows: install from [poppler-windows](https://github.com/oschwartz10612/poppler-windows) and add its `bin/` to `PATH`

## Install

```bash
git clone https://github.com/<you>/image-converter.git
cd image-converter
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python converter.py
```

## Notes

- Multi-page PDFs converted **to** an image format produce one file per page (`name_p1.png`, `name_p2.png`, …).
- Converting **to** PDF bundles all pages into one PDF.
- JPG and HEIC don't support transparency — transparent pixels are flattened onto a white background.
- **Background removal** downloads a ~170MB model file to `~/.u2net/` on first use (one time). Subsequent runs are instant. If you want to remove the feature entirely to shrink the install, drop `rembg[cpu]` from `requirements.txt` and delete the checkbox — the rest still works.

## License

MIT
