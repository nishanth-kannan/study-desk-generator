#!/usr/bin/env python3
"""How good is OCR on the fixtures, and does the review strip catch every mistake?

    python3 tests/ocr_eval.py <fixtures dir> <desk>

For each scanned fixture: words OCR got wrong (vs the known truth), and whether every wrong line
was put on the review strip. Exits 1 if a wrong line was not flagged.
"""
import difflib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "study-desk" / "scripts"))
from sdcommon import norm, read_source  # noqa: E402

fx, desk = Path(sys.argv[1]), Path(sys.argv[2])
inv = {f["id"]: f for f in json.loads((desk / "work" / "inventory.json").read_text())["files"]}
missed = 0
for fid, f in inv.items():
    ocr = desk / "sources" / f"{fid}.ocr.txt"
    truth = fx / "truth" / (Path(f["path"]).stem + ".txt")
    if not ocr.exists() or not truth.exists(): continue

    got = [(t, r["no"]) for r in read_source(ocr) if r["kind"] == "text" for t in norm(r["text"])]
    want = norm(truth.read_text().replace("<i>", "").replace("</i>", ""))
    flagged = {x["line"] for x in f["review"]}
    bad_lines, wrong_words = set(), 0
    sm = difflib.SequenceMatcher(None, [t for t, _ in got], want, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        wrong_words += max(i2 - i1, j2 - j1)
        bad_lines.update(got[i][1] for i in range(i1, max(i2, i1 + 1)) if i < len(got))
    unflagged = len(bad_lines - flagged)
    print(f"  {fid:22s} {wrong_words} wrong words on {len(bad_lines)} lines; "
          f"{len(bad_lines) - unflagged}/{len(bad_lines)} wrong lines on the review strip; {len(flagged)} lines flagged in all")
    missed += unflagged
sys.exit(1 if missed else 0)
