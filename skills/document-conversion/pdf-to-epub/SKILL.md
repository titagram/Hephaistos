---
name: pdf-to-epub
description: Convert a PDF to EPUB using Calibre ebook-convert (primary) with Pandoc fallback. Auto-detects scanned PDFs (image-only, needs OCR) vs text PDFs, applies quality flags, and verifies the output archive. Use whenever the user asks to convert a PDF to EPUB/ePub or other ebook formats.
---

# PDF → EPUB conversion

## When to use
- User provides a PDF (path or uploaded file) and wants an EPUB (or other ebook format) back.
- User asks how to convert PDF to ePub and the machine has the tools installed.

## Dependencies (already installed on this machine)
- `ebook-convert` (Calibre ≥ 7) — the ONLY working converter. Install: `sudo apt-get install -y calibre`
- `pdftotext` / `pdfinfo` (poppler-utils) — PDF analysis. Install: `sudo apt-get install -y poppler-utils`
- Optional OCR for scanned PDFs: `tesseract-ocr` + `ocrmypdf` (`sudo apt-get install -y ocrmypdf tesseract-ocr tesseract-ocr-ita`)

⚠️ **Do NOT use Pandoc as PDF→EPUB fallback**: Pandoc ≥ 3.0 removed PDF *input* support entirely ("Unknown input format pdf"). It only converts *to* PDF. If Calibre's calibre engine fails, retry with `--pdf-engine=pdftohtml` instead.

## Workflow

### 1. Run the helper script (preferred)
```bash
bash ~/.hermes/skills/pdf-to-epub/scripts/convert_pdf_to_epub.sh input.pdf [output.epub]
```
The script:
1. Checks the input exists.
2. Detects scanned vs text PDF (extracts text from first 5 pages; near-empty = scanned).
3. Converts with `ebook-convert` using quality flags (calibre 7.x syntax):
   - `--pdf-engine=calibre` (recommended: automatic header/footer removal)
   - `--pdf-header-skip=-1` / `--pdf-footer-skip=-1` (auto-detect and strip headers/footers)
   - `--base-font-size 10` / `--line-height 22` for readable output
   - `--pretty-print` for cleaner HTML markup
4. If the calibre engine fails, retries with `--pdf-engine=pdftohtml` (NOT pandoc — it can't read PDF since v3).
5. Verifies the result: file exists, size > 0, and `unzip -t` passes (EPUB is a ZIP).

### 2. Manual conversion
```bash
# Calibre (best quality; calibre 7.x — note: --pdf-disable-kerning no longer exists, the calibre engine handles kerning natively)
ebook-convert input.pdf output.epub \
  --pdf-engine=calibre \
  --pretty-print \
  --base-font-size 10 --line-height 22 \
  --pdf-header-skip=-1 --pdf-footer-skip=-1

# Fallback engine (pdftohtml) if the calibre engine chokes on the file
ebook-convert input.pdf output.epub --pdf-engine=pdftohtml
```

### 3. Scanned PDFs (image-only)
PDFs without a text layer convert to "blind" EPubs (pages as images, no selectable text).
Detect: `pdftotext -f 1 -l 5 input.pdf - | wc -c` → near 0 means scanned.
Fix with OCR first:
```bash
ocrmypdf --output-type pdf input.pdf ocr_input.pdf   # add -l ita for Italian
ebook-convert ocr_input.pdf output.epub
```
Note: OCR on large books is slow (minutes) and needs the optional deps above.

### 4. Quality checks
- EPUB must be valid: `unzip -t output.epub` (expect "No errors detected")
- Check page/image count: `unzip -l output.epub | grep -c "\.html\|\.xhtml"`
- Compare sizes: output should be significantly smaller than a scanned PDF (images get re-encoded).

## Pitfalls
- **Pandoc ≥ 3 can't read PDFs**: never use pandoc as the PDF→EPUB fallback; retry ebook-convert with `--pdf-engine=pdftohtml`.
- **`--pdf-disable-kerning` removed in calibre 7.x**: the option no longer exists; the calibre PDF engine handles kerning natively. Passing it errors out with "no such option".
- **Two-column layouts**: Calibre may read columns out of order. Fix: try `--pdf-engine=pdftohtml` (different column handling) or manual reflow.
- **Large scanned PDFs**: conversion is slow and output can be huge; prefer OCR first, then convert text.
- **`ebook-convert` GUI deps**: if it fails with missing Qt/X libs on a headless box, run with `--headless` or install `xvfb`. On this machine it works headless via CLI.
- **Never fabricate**: if conversion fails, report the actual error and try the fallback; don't claim success without `unzip -t` passing.

## Verification checklist
- [ ] Output file exists and non-zero size
- [ ] `unzip -t output.epub` reports no errors
- [ ] Text PDF: output contains extracted text (spot-check with `unzip -p output.epub | grep -c '<p'`)
- [ ] Deliver the file to the user (MEDIA:/abs/path in Telegram, report the path, or upload to Google Drive via the google-drive-file-transfer skill)
