#!/usr/bin/env python3
"""check_env.py -- confirm these scripts suit the SKILL.md that is running them, and that the tools exist.

    python3 check_env.py                 # tools only
    python3 check_env.py --expect v2     # any v2.x release (what SKILL.md needs)
    python3 check_env.py --expect v2.1   # exactly v2.1 (tests, releases)

Exits 1 if the version doesn't match or a required tool is missing. Missing Python
packages are installed with pip first. Missing optional tools only switch features off, and it says which.
"""
import argparse, importlib, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIP = {"pdfplumber": "pdfplumber", "PIL": "pillow", "pytesseract": "pytesseract", "openpyxl": "openpyxl", "playwright": "playwright"}
REQUIRED_PY, REQUIRED_BIN = ["pdfplumber", "PIL"], ["pdftoppm"]
OPTIONAL = {"pytesseract": "OCR of scanned pages", "openpyxl": "calc blocks / spreadsheets", "playwright": "verify.py smoke test",
            "tesseract": "OCR of scanned pages", "soffice": "calc recalculation and Office uploads"}


def has(mod):
    try: importlib.import_module(mod); return True
    except Exception: return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--expect"); a = ap.parse_args()
    ver = (HERE / "VERSION").read_text().strip() if (HERE / "VERSION").exists() else "unknown"
    ok = ver == a.expect or ("." not in (a.expect or "") and ver.split(".")[0] == a.expect)
    if a.expect and not ok:
        sys.exit(f"VERSION MISMATCH: expected tools {a.expect}{'.x' if '.' not in a.expect else ''}, these scripts are {ver} ({HERE}). "
                 f"Fetch a matching release instead of running these.")
    missing = [m for m in PIP if not has(m)]
    if missing:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"] + [PIP[m] for m in missing],
                       capture_output=True)
        importlib.invalidate_caches()
        missing = [m for m in missing if not has(m)]
    bins = {b: shutil.which(b) for b in ["pdftoppm", "pdftotext", "tesseract", "soffice"]}
    bad = [m for m in REQUIRED_PY if m in missing] + [b for b in REQUIRED_BIN if not bins[b]]
    off = sorted({OPTIONAL[x] for x in missing + [b for b, p in bins.items() if not p] if x in OPTIONAL})
    if bad: sys.exit(f"tools {ver}: MISSING REQUIRED {', '.join(bad)} -- install them before building")
    print(f"tools {ver} OK ({HERE})" + (f"; switched off: {', '.join(off)}" if off else "; all features available"))


if __name__ == "__main__":
    main()
