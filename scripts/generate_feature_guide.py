"""Render the OXPOS plan guide with Chromium's production PDF engine."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "feature_guide_source.html"
DESTINATION = ROOT / "static" / "pos" / "docs" / "oxpos-feature-guide.pdf"


def find_chrome() -> Path:
    candidates = [
        os.getenv("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("Chrome or Chromium is required to generate the feature guide.")


def build_pdf() -> Path:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    command = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--allow-file-access-from-files",
        "--no-pdf-header-footer",
        f"--print-to-pdf={DESTINATION}",
        SOURCE.as_uri(),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Chrome could not generate the feature guide.")
    data = DESTINATION.read_bytes()
    if not data.startswith(b"%PDF") or b"%%EOF" not in data[-2048:]:
        raise RuntimeError("Chrome returned an invalid PDF file.")
    return DESTINATION


if __name__ == "__main__":
    output = build_pdf()
    print(f"Wrote {output} ({output.stat().st_size:,} bytes)")
