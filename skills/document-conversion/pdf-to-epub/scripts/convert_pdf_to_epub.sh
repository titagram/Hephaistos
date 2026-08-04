#!/usr/bin/env bash
# Convert PDF → EPUB with quality flags; auto-detect scanned PDFs; verify output.
# Usage: convert_pdf_to_epub.sh input.pdf [output.epub]
set -euo pipefail

INPUT="${1:-}"
if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
  echo "ERROR: pass an existing PDF path: $0 input.pdf [output.epub]" >&2
  exit 1
fi

BASENAME="$(basename "$INPUT" .pdf)"
OUTPUT="${2:-${BASENAME}.epub}"
OUTPUT_DIR="$(cd "$(dirname "$OUTPUT")" && pwd)"
OUTPUT_ABS="$OUTPUT_DIR/$(basename "$OUTPUT")"

# --- Detect scanned (image-only) vs text PDF --------------------------------
TEXT_CHARS="$(pdftotext -f 1 -l 5 "$INPUT" - 2>/dev/null | tr -d '[:space:]' | wc -c || echo 0)"
echo "Text extracted from first 5 pages: ${TEXT_CHARS} chars"
if [[ "$TEXT_CHARS" -lt 200 ]]; then
  echo "WARNING: PDF appears SCANNED (little to no text layer)."
  if command -v ocrmypdf >/dev/null 2>&1; then
    echo "Running OCR first..."
    OCR_TMP="$(mktemp --suffix=.pdf)"
    if ocrmypdf --output-type pdf "$INPUT" "$OCR_TMP"; then
      INPUT="$OCR_TMP"
    else
      echo "OCR failed; converting anyway (output will be image-only)." >&2
      INPUT="$INPUT"
    fi
  else
    echo "ocrmypdf not installed; converting WITHOUT OCR (output will be image-only)."
    echo "  Install: sudo apt-get install -y ocrmypdf tesseract-ocr tesseract-ocr-ita"
  fi
fi

# --- Convert with Calibre (primary: calibre engine) --------------------------
echo "Converting with ebook-convert (calibre engine)..."
if ! ebook-convert "$INPUT" "$OUTPUT_ABS" \
     --pdf-engine=calibre \
     --pretty-print \
     --base-font-size 10 \
     --line-height 22 \
     --pdf-header-skip=-1 \
     --pdf-footer-skip=-1 >/dev/null 2>/tmp/ebook_convert.err; then
  echo "calibre engine failed, retrying with pdftohtml engine..." >&2
  tail -3 /tmp/ebook_convert.err >&2 || true
  if ! ebook-convert "$INPUT" "$OUTPUT_ABS" \
       --pdf-engine=pdftohtml \
       --pretty-print \
       --base-font-size 10 >/dev/null 2>/tmp/ebook_convert2.err; then
    echo "ERROR: both calibre and pdftohtml engines failed. No output produced." >&2
    tail -3 /tmp/ebook_convert2.err >&2 || true
    exit 2
  fi
fi

# --- Verify -----------------------------------------------------------------
if [[ ! -s "$OUTPUT_ABS" ]]; then
  echo "ERROR: output file is empty/missing: $OUTPUT_ABS" >&2
  exit 3
fi
if command -v unzip >/dev/null 2>&1; then
  if unzip -t "$OUTPUT_ABS" >/dev/null 2>&1; then
    echo "OK: archive valid (unzip -t passed)"
  else
    echo "WARNING: output is not a valid ZIP/EPUB" >&2
  fi
fi
SIZE="$(du -h "$OUTPUT_ABS" | cut -f1)"
echo "DONE: $OUTPUT_ABS ($SIZE)"
