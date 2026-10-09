#!/usr/bin/env python3
"""ingest.py -- sort the uploads, write verbatim source text, inventory every page, OCR scans.

    python3 ingest.py FILE [FILE ...] [--desk DIR] [--role ID=paper|solution|reading] [--pair SOL=PAPER]

Run it once, from the desk folder (default: current directory). It writes:

  sources/<id>.txt        the verbatim master text of each file. Fix OCR mistakes here; never paraphrase.
  sources/<id>.ocr.txt    raw OCR of scanned files. Never edit; the build diffs the master against it.
  work/inventory.json     every page: mode (typed/scanned/handwriting), tables, figures, OCR review list
  work/summary.txt        what needs eyes and nothing else -- read this instead of every page
  work/view/              only the images worth viewing: table/figure crops, OCR review strips,
                          layout thumbnails of scanned pages, full pages that look handwritten

Typed PDFs are read with pdfplumber (words, fonts, positions), so bold, tables, figures and running
headers are found without looking. Scanned pages go through Tesseract with per-word confidence;
every low-confidence word and every number lands on a review strip. Office files are converted to
PDF with LibreOffice first; images are treated as one scanned page; spreadsheets and text are copied.
"""
import argparse, difflib, json, os, re, shutil, statistics, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdcommon import norm  # noqa: E402

VIEW_DPI, OCR_DPI = 150, 300
LOW_CONF, HAND_MEAN = 80, 72                    # word confidence to review; page mean below which it looks handwritten
BOLD_RE = re.compile(r"bold|black|heavy|semibold|demi|,b$", re.I)
ITAL_RE = re.compile(r"italic|oblique|,i$", re.I)
PAGENO_RE = re.compile(r"^(page\s*)?[-–—]?\s*\d+\s*[-–—]?(\s*(of|/)\s*\d+)?$", re.I)
SOL_WORDS = re.compile(r"(solutions?|soln?s?|answers?|answer[_ -]?key|key|ans|marking[_ -]?scheme)", re.I)
READ_WORDS = re.compile(r"(notes?|reading|chapter|lecture|slides?|deck|handout|case[_ -]?note|textbook)", re.I)
Q_LINE = re.compile(r"^\s*(q\.?\s*)?\d{1,2}[.)]\s+\S|\[\s*\d+\s*marks?\s*\]|\(\s*\d+\s*marks?\s*\)", re.I)


def slug(s):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s.lower())).strip("_") or "file"


def img_tokens(w, h):
    """Rough Claude image-token cost: images are scaled to fit 1568 px on the long edge, ~750 px per token."""
    s = min(1.0, 1568 / max(w, h))
    return int(w * s * h * s / 750)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def render(pdf, page, dpi, out, gray=False):
    """Render one page to PNG (returns the path)."""
    stem = str(out)[:-4]
    run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-png", "-singlefile"] + (["-gray"] if gray else []) + [str(pdf), stem])
    return Path(stem + ".png")


# ---------------------------------------------------------------- typed pages
def group_lines(words, tol=3.0):
    """Words -> lines (sorted top to bottom, left to right)."""
    lines = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        for ln in lines:
            if abs(ln["top"] - w["top"]) <= tol and abs(ln["bottom"] - w["bottom"]) <= tol + 2:
                ln["words"].append(w); ln["x1"] = max(ln["x1"], w["x1"]); ln["x0"] = min(ln["x0"], w["x0"]); break
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "x0": w["x0"], "x1": w["x1"], "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
    return sorted(lines, key=lambda l: (l["top"], l["x0"]))


def styled(words):
    """Line text with **bold** and *italic* runs from font names; no space where the print has none."""
    kind = lambda w: "**" if BOLD_RE.search(w.get("fontname", "")) else ("*" if ITAL_RE.search(w.get("fontname", "")) else "")
    out, prev = "", None
    runs = []
    for w in words:
        k = kind(w)
        glued = prev is not None and 0 <= w["x0"] - prev["x1"] < 0.8 and abs(w["top"] - prev["top"]) < 4   # a wrapped line restarts left: not glued
        if runs and runs[-1][0] == k:
            runs[-1][1].append(("" if glued else " ") + w["text"])
        else:
            runs.append([k, [w["text"]], "" if (glued or not runs) else " "])
        prev = w
    for k, parts, sep in runs:
        out += sep + (f"{k}{''.join(parts)}{k}" if k else "".join(parts))
    return out


def inside(w, box, pad=1.5):
    x0, top, x1, bottom = box
    return w["x0"] >= x0 - pad and w["x1"] <= x1 + pad and w["top"] >= top - pad and w["bottom"] <= bottom + pad


PUA_RE = re.compile(r"[\ue000-\uf8ff]")          # icon-font glyphs (tick / cross marks): not text


def radio_marks(pg):
    """Radio buttons and check boxes: small square-ish curves/rects (6-13 pt). Returns ([(x0, top, x1, bottom, selected)], ids).
    A smaller filled shape inside one marks the option the candidate selected (attempt reviews)."""
    objs = list(pg.curves) + list(pg.rects)
    small = [o for o in objs if 6 <= o["x1"] - o["x0"] <= 13 and 6 <= o["bottom"] - o["top"] <= 13
             and abs((o["x1"] - o["x0"]) - (o["bottom"] - o["top"])) < 1.6]
    dots = [o for o in objs if 3 <= o["x1"] - o["x0"] < 6 and abs((o["x1"] - o["x0"]) - (o["bottom"] - o["top"])) < 1.2]
    marks = []
    for o in sorted(small, key=lambda o: (round(o["top"]), o["x0"])):
        if any(abs(o["x0"] - m[0]) < 2.5 and abs(o["top"] - m[1]) < 2.5 for m in marks): continue
        sel = any(d["x0"] > o["x0"] and d["x1"] < o["x1"] and d["top"] > o["top"] and d["bottom"] < o["bottom"] for d in dots)
        marks.append((o["x0"], o["top"], o["x1"], o["bottom"], sel))
    return marks


def option_lines(lines, marks):
    """Split lines that carry radio buttons into one line per option (a row of side-by-side options becomes
    several lines; words are untouched), and join a wrapped option's continuation line onto it."""
    out, last = [], None
    for ln in lines:
        mid = (ln["top"] + ln["bottom"]) / 2
        row = sorted([m for m in marks if m[1] - 3 <= mid <= m[3] + 3 and m[0] < ln["x1"] and m[2] > ln["x0"] - 18], key=lambda m: m[0])
        if row:
            lead = [w for w in ln["words"] if w["x1"] <= row[0][2] - 1]
            if lead: out.append(dict(ln, words=lead, x1=max(w["x1"] for w in lead)))
            for k, m in enumerate(row):
                nxt = row[k + 1][0] if k + 1 < len(row) else 1e9
                ws = [w for w in ln["words"] if w["x0"] >= m[2] - 1 and w["x0"] < nxt]
                if not ws: continue
                o = {"top": ln["top"], "bottom": ln["bottom"], "x0": ws[0]["x0"], "x1": max(w["x1"] for w in ws), "words": ws,
                     "opt": True, "selected": m[4], "order": ln.get("order"), "solo": len(row) == 1}
                out.append(o)
            last = out[-1] if out and out[-1].get("opt") else None
            continue
        if (last and last["solo"] and abs(ln["x0"] - last["x0"]) < 3 and ln["top"] - last["bottom"] < (last["bottom"] - last["top"]) * 1.2):
            last["words"] = last["words"] + ln["words"]; last["x1"] = max(last["x1"], ln["x1"]); last["bottom"] = ln["bottom"]
            continue
        last = None
        out.append(ln)
    return out


def merge_small(lines):
    """Superscripts/subscripts (smaller type, raised or lowered) land on a line of their own; put them back in
    their line at their x position (sigma2, X1, R2). Text order is unchanged."""
    sizes = [w.get("size", 0) for ln in lines for w in ln["words"] if w.get("size")]
    if not sizes: return lines
    med = statistics.median(sizes)
    out = []
    for ln in lines:
        small = all(w.get("size", med) < 0.85 * med for w in ln["words"]) and len(ln["words"]) <= 3
        host = None
        if small:
            for o in lines:
                if o is ln or any(w.get("size", med) < 0.85 * med for w in o["words"]): continue
                if abs((o["top"] + o["bottom"]) / 2 - (ln["top"] + ln["bottom"]) / 2) <= 7 and o["x0"] <= ln["x0"] <= o["x1"] + 2:
                    host = o; break
        if host is None: out.append(ln); continue
        ws = list(host["words"])
        for sw in ln["words"]:
            inner = next((w for w in ws if w["x0"] < sw["x0"] < w["x1"] - 0.5 and w.get("chars")), None)
            if inner:                                   # "(X," with a subscript 1 after the X -> "(X1,"
                k = next((i for i, c in enumerate(inner["chars"]) if c["x0"] >= sw["x0"] - 0.5), len(inner["chars"]))
                inner["text"] = inner["text"][:k] + sw["text"] + inner["text"][k:]
            else:
                ws.append(dict(sw, top=host["top"]))
        host["words"] = sorted(ws, key=lambda w: w["x0"])
        host["x1"] = max(host["x1"], ln["x1"])
    return out


def entry_boxes(pg, words, marks):
    """Answer fields in an attempt review (typed entries, drop-downs): a framed box 18-34 pt tall holding 0-4 words,
    not a table cell."""
    boxes, tabs = [], [t.bbox for t in pg.find_tables() if len(t.rows) >= 2 and max(len(r.cells) for r in t.rows) >= 2]
    for o in list(pg.curves) + list(pg.rects):
        w, h = o["x1"] - o["x0"], o["bottom"] - o["top"]
        if not (18 <= h <= 34 and 30 <= w <= 320): continue
        b = (o["x0"], o["top"], o["x1"], o["bottom"])
        if any(abs(b[0] - c[0]) < 3 and abs(b[1] - c[1]) < 3 for c in boxes): continue
        if any(b[0] >= t[0] - 2 and b[2] <= t[2] + 2 and b[1] >= t[1] - 2 and b[3] <= t[3] + 2 for t in tabs): continue
        inn = [x for x in words if inside(x, b, 2)]
        if len(inn) > 4 or any(m[0] >= b[0] - 1 and m[2] <= b[2] + 1 and m[1] >= b[1] - 1 and m[3] <= b[3] + 1 for m in marks): continue
        boxes.append(b)
    return boxes


QBOX_RE = re.compile(r"^Question\s+(\d+)$")
FEED_MARK_RE = re.compile(r"^Mark\s+-?[\d.]+\s+out\s+of\s+[\d.]+$")
KEY_RE = re.compile(r"^The correct answers? (?:is|are)\s*:", re.I)


def margin_column(words, W):
    """Attempt reviews put a status box (Question N / Correct / Mark x out of y) in a left margin column.
    Returns the x that separates it from the question text, or None."""
    q = [w for w in words if w["text"] == "Question" and w["x0"] < 0.3 * W]
    if not q: return None
    edge = max(w["x1"] for w in q) + 20
    right = [w["x0"] for w in words if w["x0"] > edge and w["x0"] < 0.5 * W]
    return (min(right) - 2) if right else None


def cluster(boxes, gap=12):
    """Merge nearby rectangles (x0, top, x1, bottom) into clusters: [(box, count)]."""
    cl = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for c in cl:
            x0, t, x1, bt = c[0]
            if b[0] <= x1 + gap and b[2] >= x0 - gap and b[1] <= bt + gap and b[3] >= t - gap:
                c[0] = (min(x0, b[0]), min(t, b[1]), max(x1, b[2]), max(bt, b[3])); c[1] += 1; break
        else:
            cl.append([b, 1])
    changed = True                                   # second pass: clusters that now overlap
    while changed:
        changed = False
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                a, b = cl[i][0], cl[j][0]
                if b[0] <= a[2] + gap and b[2] >= a[0] - gap and b[1] <= a[3] + gap and b[3] >= a[1] - gap:
                    cl[i] = [(min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])), cl[i][1] + cl[j][1]]
                    del cl[j]; changed = True; break
            if changed: break
    return cl


def two_columns(words, width):
    """True when a clear vertical gutter splits the text near the middle of the page."""
    if len(words) < 40: return False
    lo, hi = int(width * 0.38), int(width * 0.62)
    cols = [0] * (hi - lo)
    for w in words:
        for x in range(max(int(w["x0"]), lo), min(int(w["x1"]), hi)):
            cols[x - lo] += 1
    empty = [i for i, c in enumerate(cols) if c == 0]
    left = sum(1 for w in words if w["x1"] < width * 0.5)
    right = sum(1 for w in words if w["x0"] > width * 0.5)
    return len(empty) >= 6 and left > 15 and right > 15


def gappy(line, factor=2.6):
    """A line with several wide gaps between words looks like an unruled table row."""
    ws = line["words"]
    if len(ws) < 3: return False
    gaps = [b["x0"] - a["x1"] for a, b in zip(ws, ws[1:])]
    space = max(1.0, statistics.median([w["x1"] - w["x0"] for w in ws]) / max(1, statistics.median([len(w["text"]) for w in ws])))
    return sum(g > factor * space * 1.6 for g in gaps) >= 2


def typed_page(pg, n):
    """Return (segments, info) for a page with a text layer. Segments are in reading order."""
    W, H = float(pg.width), float(pg.height)
    words = pg.extract_words(extra_attrs=["fontname", "size", "non_stroking_color"], keep_blank_chars=False, use_text_flow=False,
                             return_chars=True)
    words = [dict(w, text=PUA_RE.sub("", w["text"])) for w in words if PUA_RE.sub("", w["text"]).strip()]
    marks = radio_marks(pg)
    info = {"n": n, "mode": "typed", "w": W, "h": H, "words": len(words), "tables": [], "figures": [], "flags": []}
    segs = []
    # attempt-review furniture: the left status column, typed entries in answer boxes
    mx = margin_column(words, W)
    if mx:
        mw = [w for w in words if w["x1"] <= mx and H * 0.05 < w["top"] < H * 0.95]
        words = [w for w in words if w not in mw]
        boxes_ = []
        for ln in group_lines(mw, tol=4):
            t = " ".join(w["text"] for w in ln["words"])
            if QBOX_RE.match(t) or not boxes_ or ln["top"] - boxes_[-1]["bottom"] > 30:
                boxes_.append({"top": ln["top"], "bottom": ln["bottom"], "text": [t]})
            else:
                boxes_[-1]["text"].append(t); boxes_[-1]["bottom"] = ln["bottom"]
        for b in boxes_:
            m = QBOX_RE.match(b["text"][0])
            segs.append({"kind": "margin", "top": b["top"] - 0.5, "num": int(m.group(1)) if m else None, "text": " · ".join(b["text"])})
        info["flags"].append("attempt review: status column -> [[item N]] + ignored lines")
    eboxes = entry_boxes(pg, words, marks)
    nav = [b for b in eboxes if any(w["text"].startswith("Jump") for w in words if inside(w, b, 2))]
    nav_cut = (min(b[1] for b in nav) - 40) if nav else None
    if nav:                                          # Moodle's activity navigation (Previous / Jump to... / Next) closes the page
        cut = min(b[1] for b in nav) - 40
        navw = [w for w in words if w["top"] >= cut and w["top"] < H * 0.95]
        words = [w for w in words if w not in navw]
        eboxes = [b for b in eboxes if b[1] < cut]
        if navw: segs.append({"kind": "margin", "top": cut, "num": None, "text": " ".join(w["text"] for w in navw)})
    for b in eboxes:
        inn = [w for w in words if inside(w, b, 2)]
        words = [w for w in words if w not in inn]
        segs.append({"kind": "blank", "top": (b[1] + b[3]) / 2, "x": b[0], "text": " ".join(w["text"] for w in sorted(inn, key=lambda w: w["x0"])),
                     "bbox": b})
    if eboxes: info["flags"].append(f"{len(eboxes)} answer boxes -> [[blank]] (typed entries kept as ignored lines)")
    tboxes = []
    for k, t in enumerate(pg.find_tables(), 1):
        rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in t.extract()]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2 or max(len(r) for r in rows) < 2: continue
        tid = f"p{n}t{k}"
        tboxes.append(t.bbox)
        info["tables"].append({"id": tid, "bbox": [round(v, 1) for v in t.bbox], "shape": [len(rows), max(len(r) for r in rows)], "ruled": True})
        segs.append({"kind": "table", "top": t.bbox[1], "id": tid, "rows": rows, "bbox": t.bbox})
    graphics = []
    for o in list(pg.curves) + list(pg.lines) + list(pg.rects):
        b = (o["x0"], o["top"], o["x1"], o["bottom"])
        if any(b[0] >= tb[0] - 3 and b[2] <= tb[2] + 3 and b[1] >= tb[1] - 3 and b[3] <= tb[3] + 3 for tb in tboxes): continue
        if (b[3] - b[1]) < 1.5 and (b[2] - b[0]) > W * 0.5: continue        # horizontal rules
        if (b[2] - b[0]) <= 13 and (b[3] - b[1]) <= 13 and any(abs(b[0] - m[0]) < 4 and abs(b[1] - m[1]) < 4 for m in marks):
            continue                                                         # radio buttons / check boxes and their dots
        if sum(1 for w in words if inside(w, b)) >= 8: continue              # a frame around text (card, feedback box), not a figure
        if any(abs(b[0] - e[0]) < 4 and abs(b[1] - e[1]) < 4 for e in eboxes): continue   # answer boxes
        if nav_cut is not None and b[1] >= nav_cut: continue                  # navigation buttons
        graphics.append(b)
    for im in pg.images:
        graphics.append((im["x0"], im["top"], im["x1"], im["bottom"]))
    fboxes = []
    for box, cnt in cluster(graphics):
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area < 2500 or (cnt < 3 and not any(abs(im["x0"] - box[0]) < 2 for im in pg.images)): continue
        fid = f"p{n}f{len(fboxes) + 1}"
        # include the figure's own labels: words level with the box (legends, y-axis ticks) and short
        # numeric words just above/below it (x-axis ticks). Grow until stable.
        while True:
            lab = [w for w in words if not inside(w, box) and (
                (w["top"] >= box[1] - 2 and w["bottom"] <= box[3] + 2 and w["x0"] <= box[2] + 60 and w["x1"] >= box[0] - 60) or
                (w["x0"] >= box[0] - 20 and w["x1"] <= box[2] + 20 and (w["top"] - box[3] < 14 and box[1] - w["bottom"] < 14)
                 and re.fullmatch(r"[\d.,:%$-]{1,6}", w["text"])))]
            if not lab: break
            box = (min([box[0]] + [w["x0"] for w in lab]), min([box[1]] + [w["top"] for w in lab]),
                   max([box[2]] + [w["x1"] for w in lab]), max([box[3]] + [w["bottom"] for w in lab]))
        fboxes.append(box)
        info["figures"].append({"id": fid, "bbox": [round(v, 1) for v in box], "share": round(area / (W * H), 2)})
        segs.append({"kind": "figure", "top": box[1], "id": fid, "bbox": box})
    free = [w for w in words if not any(inside(w, b) for b in tboxes + fboxes)]
    if two_columns(free, W):
        info["flags"].append("two-column: reading order is left column, then right")
        mid = W / 2
        cols = [[w for w in free if w["x1"] <= mid + 2], [w for w in free if w["x1"] > mid + 2]]
        lines = group_lines(cols[0]) + group_lines(cols[1])
        for i, ln in enumerate(lines): ln["order"] = i
    else:
        lines = group_lines(free)
    run_ = [ln for ln in lines if gappy(ln)]
    if len(run_) >= 3 and not tboxes:
        info["flags"].append(f"possible unruled table ({len(run_)} gappy lines)")
        tops = [l["top"] for l in run_]; bots = [l["bottom"] for l in run_]
        info["tables"].append({"id": f"p{n}u1", "bbox": [round(min(l['x0'] for l in run_), 1), round(min(tops), 1),
                               round(max(l['x1'] for l in run_), 1), round(max(bots), 1)], "ruled": False})
    lines = merge_small(lines)
    if marks:
        lines = option_lines(lines, marks)
        nopt = sum(1 for ln in lines if ln.get("opt"))
        if nopt:
            info["flags"].append(f"{nopt} answer options next to radio buttons/check boxes -> [[options]] blocks")
            if any(ln.get("selected") for ln in lines): info["flags"].append("a selected option is marked (attempt review): evidence, not part of the paper")
    key = None                                       # attempt feedback: "The correct answer is: ..." (+ its wrapped lines)
    for i, ln in enumerate(lines):
        t = styled(ln["words"])
        col = ln["words"][0].get("non_stroking_color")
        if KEY_RE.match(t) and not ln.get("opt"):
            key = {"kind": "key", "top": ln["top"], "text": t, "col": col, "x0": ln["x0"], "bottom": ln["bottom"]}
            segs.append(key); continue
        if key and col == key["col"] and abs(ln["x0"] - key["x0"]) < 3 and ln["top"] - key["bottom"] < 22 and not ln.get("opt"):
            key["text"] += " " + t; key["bottom"] = ln["bottom"]; continue
        key = None
        segs.append({"kind": "line", "top": ln["top"], "order": ln.get("order"), "text": t, "feedback": bool(FEED_MARK_RE.match(t)),
                     "bbox": (ln["x0"], ln["top"], ln["x1"], ln["bottom"]), "opt": bool(ln.get("opt")), "x": ln["x0"]})
    if any(s.get("order") is not None for s in segs):
        segs.sort(key=lambda s: (s["order"] if s.get("order") is not None else 1e9, s["top"]))
    else:
        segs.sort(key=lambda s: (s["top"], s.get("x", 0)))
    return segs, info


# ---------------------------------------------------------------- scanned pages
def ocr_page(png, n, W_pt, H_pt):
    import pytesseract
    from PIL import Image
    img = Image.open(png)
    d = pytesseract.image_to_data(img, config="--psm 3", output_type=pytesseract.Output.DICT)
    sx, sy = W_pt / img.width, H_pt / img.height
    lines = {}
    for i, t in enumerate(d["text"]):
        if not t.strip(): continue
        k = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        ln = lines.setdefault(k, {"words": [], "conf": []})
        ln["words"].append({"text": t, "x0": d["left"][i], "top": d["top"][i], "x1": d["left"][i] + d["width"][i],
                            "bottom": d["top"][i] + d["height"][i], "conf": float(d["conf"][i])})
    segs, confs = [], []
    for k in sorted(lines, key=lambda k: (min(w["top"] for w in lines[k]["words"]), k)):
        ws = lines[k]["words"]
        box = (min(w["x0"] for w in ws), min(w["top"] for w in ws), max(w["x1"] for w in ws), max(w["bottom"] for w in ws))
        confs += [w["conf"] for w in ws]
        segs.append({"kind": "line", "top": box[1] * sy, "text": " ".join(w["text"] for w in ws),
                     "bbox": (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy), "px": box,
                     "flag": [(w["text"], round(w["conf"])) for w in ws if w["conf"] < LOW_CONF or re.search(r"\d", w["text"])],
                     "low": sum(w["conf"] < LOW_CONF for w in ws)})
    mean = statistics.mean(confs) if confs else 0
    low = sum(c < LOW_CONF for c in confs)
    mode = "handwriting" if confs and (mean < HAND_MEAN or low > 0.3 * len(confs)) else "scanned"
    info = {"n": n, "mode": mode, "w": W_pt, "h": H_pt, "words": len(confs), "tables": [], "figures": [], "flags": [],
            "ocr": {"mean": round(mean, 1), "low": low, "numbers": sum(1 for s in segs for t, _ in s["flag"] if re.search(r"\d", t))}}
    if mode == "handwriting": info["flags"].append(f"looks handwritten or poor (OCR mean {mean:.0f}%): transcribe the whole page")
    return segs, info


def review_strips(png, segs, out_stem, max_h=1500, width=1100):
    """Stack the flagged lines of one scanned page into strips: crop, flagged words boxed, OCR text under it."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(png).convert("RGB")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    tiles = []
    for s in segs:
        if not s.get("flag") or not s.get("px"): continue
        x0, t, x1, b = s["px"]; pad = 10
        crop = img.crop((max(0, x0 - pad), max(0, t - pad), min(img.width, x1 + pad), min(img.height, b + pad)))
        if crop.width > width:
            crop = crop.resize((width, int(crop.height * width / crop.width)))
        tile = Image.new("RGB", (width, crop.height + 30), "white")
        tile.paste(crop, (0, 0))
        dr = ImageDraw.Draw(tile)
        dr.text((4, crop.height + 4), f"L{s['line_no']}: {s['text']}"[:110], fill=(170, 0, 0), font=font)
        tiles.append(tile)
    pages, cur, h = [], [], 0
    for t in tiles:
        if h + t.height > max_h and cur: pages.append(cur); cur, h = [], 0
        cur.append(t); h += t.height + 6
    if cur: pages.append(cur)
    out = []
    for i, group in enumerate(pages, 1):
        H = sum(t.height + 6 for t in group)
        sheet = Image.new("RGB", (width, H), (235, 235, 235)); y = 0
        for t in group: sheet.paste(t, (0, y)); y += t.height + 6
        p = Path(f"{out_stem}-{i}.png"); sheet.save(p); out.append(p)
    return out


# ---------------------------------------------------------------- per file
def to_pdf(path, tmp):
    ext = path.suffix.lower()
    if ext == ".pdf": return path, "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"):
        from PIL import Image
        im = Image.open(path).convert("RGB"); out = Path(tmp) / (path.stem + ".pdf")
        im.save(out, "PDF", resolution=200); return out, "image"
    if ext in (".pptx", ".ppt", ".docx", ".doc", ".odt", ".odp", ".rtf"):
        run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(path)], timeout=180)
        return Path(tmp) / (path.stem + ".pdf"), "office"
    return None, "text"


def sheet_source(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        out += [f"=== page {len([l for l in out if l.startswith('=== page')]) + 1} ===", f"**{ws.title}**", f"[[table {slug(ws.title)}]]"]
        for row in ws.iter_rows(values_only=True):
            if any(v is not None for v in row):
                out.append("\t".join("" if v is None else str(v) for v in row))
        out.append("[[/table]]")
    return out


EXAMPLE = {}


def find_furniture(pages_lines):
    """Lines repeated near the top/bottom of most pages, and lone page numbers."""
    from collections import Counter
    cnt, n = Counter(), len(pages_lines)
    for segs, info in pages_lines:
        seen = set()
        for s in segs:
            if s["kind"] != "line": continue
            if s["bbox"][1] < info["h"] * 0.09 or s["bbox"][3] > info["h"] * 0.92:
                key = re.sub(r"\d+", "#", s["text"].strip().lower())
                if key not in seen: cnt[key] += 1; seen.add(key); EXAMPLE.setdefault(key, s["text"].strip("* "))
    rep = {k for k, c in cnt.items() if n >= 2 and c >= max(2, (n + 1) // 2)}
    return rep


def ingest_file(path, fid, desk, role_hint=None):
    view = desk / "work" / "view"; view.mkdir(parents=True, exist_ok=True)
    rec = {"id": fid, "path": str(path), "pages": [], "review": [], "view": []}
    with tempfile.TemporaryDirectory() as tmp:
        pdf, kind = to_pdf(path, tmp)
        rec["kind"] = kind
        if pdf is None:
            if path.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".csv"):
                lines = sheet_source(path) if path.suffix.lower() != ".csv" else \
                    ["=== page 1 ===", "[[table csv]]"] + [l.replace(",", "\t") for l in path.read_text(errors="ignore").splitlines()] + ["[[/table]]"]
            else:
                lines = ["=== page 1 ==="] + path.read_text(errors="ignore").splitlines()
            rec["role"] = role_hint or "reading"
            return rec, lines, None
        import pdfplumber
        keep_pdf = desk / "work" / "pdf" / (fid + ".pdf")
        keep_pdf.parent.mkdir(parents=True, exist_ok=True)
        if pdf.resolve() != keep_pdf.resolve(): shutil.copy(pdf, keep_pdf)
        rec["pdf"] = str(keep_pdf.relative_to(desk))
        pages, scans = [], []
        with pdfplumber.open(keep_pdf) as doc:
            for n, pg in enumerate(doc.pages, 1):
                words = pg.extract_words()
                cover = max([(im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) / (float(pg.width) * float(pg.height)) for im in pg.images] or [0])
                if kind == "image" or (cover > 0.5 and len(words) < 5) or cover > 0.8:   # >0.8: scanner text layer -> re-OCR for confidences
                    pages.append(None); scans.append((n, float(pg.width), float(pg.height)))
                else:
                    pages.append(typed_page(pg, n))
        ocr_dir = desk / "work" / "ocr"; ocr_dir.mkdir(parents=True, exist_ok=True)

        def page_image(n, W):
            """The scan's own image when the page is one full-page image of decent resolution (fast, no resampling);
            otherwise a render at OCR_DPI."""
            try:
                lst = run(["pdfimages", "-list", "-f", str(n), "-l", str(n), str(keep_pdf)], text=True).stdout.splitlines()[2:]
                if len(lst) == 1 and int(lst[0].split()[3]) / (W / 72) >= 170:
                    stem = ocr_dir / f"{fid}-p{n}-img"
                    run(["pdfimages", "-j", "-f", str(n), "-l", str(n), str(keep_pdf), str(stem)])
                    got = sorted(ocr_dir.glob(f"{fid}-p{n}-img-*"))
                    if got: return got[0]
            except (subprocess.CalledProcessError, ValueError, IndexError):
                pass
            return render(keep_pdf, n, OCR_DPI, ocr_dir / f"{fid}-p{n}.png", gray=True)

        def do_ocr(job):
            n, W, H = job
            png = page_image(n, W)
            return n, png, ocr_page(png, n, W, H)
        with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() or 1)) as ex:
            for n, png, (segs, info) in ex.map(do_ocr, scans):
                info["png"] = str(png)
                pages[n - 1] = (segs, info)
    furniture = find_furniture(pages)
    lines = []
    for segs, info in pages:
        lines.append(f"=== page {info['n']} ===")
        in_opts = False
        for s in segs:
            if in_opts and not (s["kind"] == "line" and s.get("opt")):
                lines.append("[[/options]]"); in_opts = False
            if s["kind"] == "line" and s.get("opt") and not in_opts:
                lines.append("[[options]]"); in_opts = True
            if s["kind"] == "margin":
                if s.get("num") is not None: lines.append(f"[[item {s['num']}]]")
                lines.append("#~ " + s["text"]); continue
            if s["kind"] == "blank":
                lines.append("[[blank]]")
                if s["text"]: lines.append("#~ [entry] " + s["text"])
                continue
            if s["kind"] == "key":
                lines.append("[[key]] " + s["text"]); continue
            if s["kind"] == "line" and s.get("feedback"):
                lines.append("#~ " + s["text"]); s["ign"] = True; continue
            if s["kind"] == "line":
                key = re.sub(r"\d+", "#", s["text"].strip().lower())
                ign = key in furniture or (PAGENO_RE.match(s["text"].strip()) and
                                           (s["bbox"][1] < info["h"] * 0.09 or s["bbox"][3] > info["h"] * 0.92))
                s["ign"] = bool(ign)
                lines.append(("#~ " if ign else "") + s["text"])
                s["line_no"] = len(lines) + 1           # +1 for the provenance line written first
                if s.get("flag") and not ign:
                    rec["review"].append({"line": s["line_no"], "page": info["n"], "flag": s["flag"]})
            elif s["kind"] == "table":
                lines.append(f"[[table {s['id']}]]")
                lines += ["\t".join(r) for r in s["rows"]]
                lines.append("[[/table]]")
            elif s["kind"] == "figure":
                s["crop"] = f"work/view/{fid}-{s['id']}.png"
                lines.append(f"[[figure {s['id']} {s['crop']}]]")
        if in_opts: lines.append("[[/options]]")
        rec["pages"].append(info)
    rec["furniture"] = sorted(EXAMPLE.get(k, k) for k in furniture)
    rec["linemap"] = {}                              # "page:k" (k-th text line on the page) -> bbox in points
    for segs, info in pages:
        k = 0
        for s in segs:
            if s["kind"] == "line" and s["text"] and not s.get("ign"):
                k += 1; rec["linemap"][f"{info['n']}:{k}"] = [round(v, 1) for v in s["bbox"]]
    return rec, lines, pages


def make_views(rec, pages, desk):
    """Render only what needs eyes."""
    from PIL import Image
    view, pdf = desk / "work" / "view", desk / rec["pdf"]
    out = []
    for segs, info in pages:
        n, scale = info["n"], VIEW_DPI / 72
        need_crop = [s for s in segs if s["kind"] in ("table", "figure")] + \
                    [{"kind": "table", "id": t["id"], "bbox": t["bbox"]} for t in info["tables"] if not t.get("ruled")]
        if info["mode"] == "typed" and need_crop:
            full = render(pdf, n, VIEW_DPI, view / f"_{rec['id']}-p{n}.png")
            im = Image.open(full)
            for s in need_crop:
                x0, t, x1, b = s["bbox"]; pad = 8
                c = im.crop((max(0, (x0 - pad) * scale), max(0, (t - pad) * scale), min(im.width, (x1 + pad) * scale), min(im.height, (b + pad) * scale)))
                p = view / f"{rec['id']}-{s['id']}.png"; c.save(p)
                out.append({"page": n, "what": s["kind"], "id": s["id"], "path": str(p.relative_to(desk)), "tokens": img_tokens(*c.size)})
            full.unlink()
            if any("two-column" in f for f in info["flags"]):
                p = render(pdf, n, 70, view / f"{rec['id']}-p{n}-thumb.png")
                out.append({"page": n, "what": "layout thumbnail (two columns)", "path": str(p.relative_to(desk)), "tokens": img_tokens(*Image.open(p).size)})
        elif info["mode"] == "handwriting":
            p = render(pdf, n, VIEW_DPI, view / f"{rec['id']}-p{n}.png", gray=True)
            out.append({"page": n, "what": "full page (transcribe)", "path": str(p.relative_to(desk)), "tokens": img_tokens(*Image.open(p).size)})
        elif info["mode"] == "scanned":
            p = render(pdf, n, 60, view / f"{rec['id']}-p{n}-thumb.png", gray=True)
            out.append({"page": n, "what": "layout thumbnail (tables? figures?)", "path": str(p.relative_to(desk)), "tokens": img_tokens(*Image.open(p).size)})
            for sp in review_strips(info["png"], segs, view / f"{rec['id']}-p{n}-review"):
                out.append({"page": n, "what": "OCR review strip", "path": str(sp.relative_to(desk)), "tokens": img_tokens(*Image.open(sp).size)})
    return out


def classify(rec, lines):
    name = Path(rec["path"]).stem
    if SOL_WORDS.search(name): return "solution"
    if READ_WORDS.search(name): return "reading"
    head = " ".join(lines[:40]).lower()
    if re.search(r"\b(solutions?|answer key|suggested answers?)\b", head): return "solution"
    return "paper" if sum(bool(Q_LINE.search(l)) for l in lines) >= 2 else "reading"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--desk", default=".")
    ap.add_argument("--role", action="append", default=[], help="ID=paper|solution|reading (override the guess)")
    ap.add_argument("--pair", action="append", default=[], help="SOLUTION_ID=PAPER_ID (override the guess)")
    a = ap.parse_args()
    desk = Path(a.desk).resolve()
    (desk / "sources").mkdir(parents=True, exist_ok=True)
    roles = dict(x.split("=", 1) for x in a.role)
    inv_path = desk / "work" / "inventory.json"
    inv = json.loads(inv_path.read_text()) if inv_path.exists() else {"files": []}
    known = {f["id"] for f in inv["files"]}
    for f in a.files:
        p = Path(f).resolve()
        fid = slug(p.stem)
        base, k = fid, 2
        while fid in known and not any(x["id"] == fid and Path(x["path"]) == p for x in inv["files"]):
            fid = f"{base}_{k}"; k += 1
        rec, lines, pages = ingest_file(p, fid, desk, roles.get(fid))
        rec["role"] = roles.get(fid) or rec.get("role") or classify(rec, lines)
        if pages: rec["view"] = make_views(rec, pages, desk)
        header = f"#! study-desk source v1 id={fid} role={rec['role']} file={p.name}"
        src = desk / "sources" / f"{fid}.txt"
        if src.exists() and src.read_text(encoding="utf-8").split("\n", 1)[0] != header:
            print(f"  ! sources/{fid}.txt exists from another file; not overwritten"); continue
        if src.exists():
            print(f"  = sources/{fid}.txt already exists (keeping your edits); delete it to re-extract")
        else:
            src.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        if any(pg["mode"] != "typed" for pg in rec["pages"]):
            ocr = desk / "sources" / f"{fid}.ocr.txt"
            if not ocr.exists(): ocr.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        rec["source"] = f"sources/{fid}.txt"
        rec["lines"] = len(lines)
        inv["files"] = [x for x in inv["files"] if x["id"] != fid] + [rec]
        known.add(fid)
    # pair solutions with papers
    papers = [x["id"] for x in inv["files"] if x["role"] == "paper"]
    forced = dict(x.split("=", 1) for x in a.pair)
    for x in inv["files"]:
        if x["role"] != "solution": continue
        if x["id"] in forced: x["pair"] = forced[x["id"]]; continue
        bare = slug(SOL_WORDS.sub("", x["id"]).replace("scan", ""))
        best = max(papers, key=lambda pid: difflib.SequenceMatcher(None, bare, slug(pid.replace("scan", ""))).ratio(), default=None)
        x["pair"] = best
    for x in inv["files"]:
        if x["role"] == "paper":
            x["solutions"] = [s["id"] for s in inv["files"] if s.get("pair") == x["id"]]
    inv_path.write_text(json.dumps(inv, indent=1, default=str))
    summary = write_summary(inv, desk)
    print(summary)


def write_summary(inv, desk):
    L, tok, nimg = [], 0, 0
    pages = sum(len(f["pages"]) for f in inv["files"])
    for f in inv["files"]:
        modes = {pg["mode"] for pg in f["pages"]} or {"text"}
        mode = "/".join(sorted(modes))
        extra = f"  solutions: {', '.join(f['solutions'])}" if f.get("solutions") else (f"  for: {f['pair']}" if f.get("pair") else "")
        L.append(f"{f['id']}  {f['role']}  {mode} {len(f['pages'])}pp  -> {f['source']} ({f['lines']} lines){extra}")
        for pg in f["pages"]:
            bits = []
            if pg.get("ocr"): bits.append(f"OCR mean {pg['ocr']['mean']:.0f}%, {pg['ocr']['low']} low-conf words, {pg['ocr']['numbers']} numbers")
            for t in pg["tables"]:
                bits.append(f"table {t['id']} {'x'.join(map(str, t['shape'])) if t.get('shape') else ''}{'' if t.get('ruled') else ' (unruled? check)'}".rstrip())
            for g in pg["figures"]: bits.append(f"figure {g['id']} ({int(g['share'] * 100)}% of page)")
            bits += pg["flags"]
            views = [v for v in f["view"] if v["page"] == pg["n"]]
            for v in views: tok += v["tokens"]; nimg += 1
            if bits or views:
                L.append(f"  p{pg['n']}  " + "; ".join(bits) + ("".join(f"\n      view {v['path']}  [{v['what']}]" for v in views)))
        if f.get("furniture"): L.append(f"  ignored header/footer: {'; '.join(f['furniture'][:3])}")
        if f.get("review"): L.append(f"  OCR review: {len(f['review'])} lines -> fix in {f['source']} (line numbers on the strips)")
    head = f"INGEST  {len(inv['files'])} files, {pages} pages; view {nimg} images (~{tok/1000:.1f}k tokens) instead of {pages} full pages"
    txt = head + "\n" + "\n".join(L) + "\n"
    (desk / "work" / "summary.txt").write_text(txt)
    return txt


if __name__ == "__main__":
    main()
