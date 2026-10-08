"""compare.html: each past-paper item's original page crop beside the digitized version, riskiest first.

Risk comes from the checks: errors, OCR lines Claude corrected, scanned or handwritten sources, tables,
figures, imperfect text match, items made by hand. Claude reviews the top rows (verify.py --compare
screenshots them); the student can skim the rest.
"""
import base64, html, io, json, subprocess
from pathlib import Path

from sdcommon import read_source

WEIGHTS = {"error": 10, "OCR corrected": 4, "handwritten source": 4, "scanned source": 3, "official solution from a scan": 3,
           "hand-made item (no source span)": 3, "table": 2, "figure": 1, "MCQ": 0.5}

CSS = """body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1d2330}
header{padding:16px;background:#fff;border-bottom:1px solid #dde}h1{font-size:18px;margin:0 0 4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;background:#fff;margin:12px;padding:12px;border:1px solid #dde;border-radius:6px}
.row h2{grid-column:1/-1;font-size:15px;margin:0}.chip{display:inline-block;font-size:12px;padding:1px 7px;margin:0 4px 0 0;border-radius:9px;background:#eef}
.chip.err{background:#fde2e2;color:#8a1111}.score{float:right;color:#667}
.orig img{max-width:100%;border:1px solid #ccd}.dig{border-left:3px solid #ccd;padding-left:12px;overflow-x:auto}
.dig table{border-collapse:collapse;margin:6px 0}.dig td,.dig th{border:1px solid #bbc;padding:3px 8px}
.dig img{max-width:100%}.dig ol{margin:6px 0}.lab{font-size:12px;color:#667;text-transform:uppercase;letter-spacing:.04em;margin:8px 0 2px}
@media (max-width:700px){.row{grid-template-columns:1fr}}"""


def _pages(pdf, cache_dir, dpi=100):
    """Render every page of a PDF once (JPEG, grey); returns {page: path}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = cache_dir / pdf.stem
    have = sorted(cache_dir.glob(pdf.stem + "-*.jpg"))
    if not have or have[0].stat().st_mtime < pdf.stat().st_mtime:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-jpeg", "-jpegopt", "quality=70", str(pdf), str(stem)],
                       check=True, capture_output=True)
        have = sorted(cache_dir.glob(pdf.stem + "-*.jpg"))
    return {int(p.stem.rsplit("-", 1)[1]): p for p in have}


class Crops:
    def __init__(self, base):
        self.base = base
        inv = base / "work" / "inventory.json"
        self.files = {f["id"]: f for f in json.loads(inv.read_text())["files"]} if inv.exists() else {}
        self.cache, self.recs = {}, {}

    def kmap(self, fid, src):
        if fid not in self.recs:
            count, m, objs = {}, {}, {}
            for r in read_source(self.base / src):
                if r["kind"] in ("text", "ignored"):
                    if r["kind"] == "text":
                        count[r["page"]] = count.get(r["page"], 0) + 1; m[r["no"]] = (r["page"], count[r["page"]])
                elif r["kind"] == "directive" and r["table"] and r["text"].startswith("[[table"): m[r["no"]] = (r["page"], "t:" + r["table"])
                elif r["kind"] == "figure": m[r["no"]] = (r["page"], "f:" + r["fig"]["id"])
            self.recs[fid] = m
        return self.recs[fid]

    def crop(self, fid, src, lo, hi, dpi=100):
        from PIL import Image
        f = self.files.get(fid)
        if not f or not f.get("pdf"): return []
        if fid not in self.cache: self.cache[fid] = _pages(self.base / f["pdf"], self.base / "work" / "cmp", dpi)
        pages = {pg["n"]: pg for pg in f["pages"]}
        boxes = {}
        for no, (page, k) in self.kmap(fid, src).items():
            if not lo <= no <= hi: continue
            if isinstance(k, int): b = f["linemap"].get(f"{page}:{k}")
            else:
                kind, oid = k.split(":", 1)
                objs = pages[page]["tables"] if kind == "t" else pages[page]["figures"]
                b = next((o["bbox"] for o in objs if o["id"] == oid), None)
            if not b: continue
            cur = boxes.get(page)
            boxes[page] = b if not cur else [min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])]
        out = []
        for page, b in sorted(boxes.items()):
            p = self.cache[fid].get(page)
            if not p: continue
            im = Image.open(p); s = dpi / 72; pad = 6
            W = pages[page]["w"]
            c = im.crop((max(0, (min(b[0], 40) - pad) * s), max(0, (b[1] - pad) * s), min(im.width, (max(b[2], W - 40) + pad) * s),
                         min(im.height, (b[3] + pad) * s)))
            buf = io.BytesIO(); c.save(buf, "JPEG", quality=70)
            out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        return out


def build(papers, base, risk, item_errors, out_path):
    cr = Crops(base)
    rows = []
    for z in papers:
        if not z.get("source"): continue
        fid = Path(z["source"]).stem
        f = cr.files.get(fid, {})
        modes = {pg["mode"] for pg in f.get("pages", [])}
        from checks import ocr_changes
        changed = ocr_changes(base / z["source"])
        sol_changed = {}
        for it in z.get("qs") or []:
            if it.get("head") and not item_errors.get((z["id"], it.get("n"))): continue
            reasons = list(dict.fromkeys(risk.get((z["id"], it.get("n")), [])))
            errs = item_errors.get((z["id"], it.get("n")), [])
            s = it.get("src") or {}
            if s:
                if any(s["lines"][0] <= n <= s["lines"][1] for n in changed): reasons.append("OCR corrected")
                if "handwriting" in modes: reasons.append("handwritten source")
                elif "scanned" in modes: reasons.append("scanned source")
            es = it.get("e_src") or {}
            if es.get("source"):
                sf = cr.files.get(es["source"], {})
                if {pg["mode"] for pg in sf.get("pages", [])} & {"scanned", "handwriting"}: reasons.append("official solution from a scan")
                if es["source"] not in sol_changed: sol_changed[es["source"]] = ocr_changes(base / sf.get("source", "")) if sf.get("source") else set()
                if any(es["lines"][0] <= n <= es["lines"][1] for n in sol_changed[es["source"]]): reasons.append("OCR corrected")
            score = 10 * len(errs) + sum(WEIGHTS.get(r, 3 if r.startswith("text match") else 0) for r in set(reasons))
            orig = cr.crop(fid, z["source"], *s["lines"]) if s else []
            sol = cr.crop(es["source"], cr.files[es["source"]]["source"], *es["lines"]) if es.get("source") in cr.files else []
            rows.append((score, z, it, reasons, errs, orig, sol))
    rows.sort(key=lambda r: -r[0])
    body = []
    for score, z, it, reasons, errs, orig, sol in rows:
        name = html.escape(it.get("name") or f"item {it.get('n')}")
        chips = "".join(f"<span class='chip err'>{html.escape(e[:140])}</span>" for e in errs) + \
                "".join(f"<span class='chip'>{html.escape(r)}</span>" for r in dict.fromkeys(reasons))
        opts = "<ol type='A'>" + "".join(f"<li>{o}</li>" for o in it.get("o") or []) + "</ol>" if it.get("o") and not it.get("sub") else ""
        left = "<div class='lab'>Original</div>" + ("".join(f"<img src='{c}'>" for c in orig) or "<p><em>no source crop</em></p>")
        right = f"<div class='lab'>Digitized</div>{it.get('q', '')}{opts}"
        if sol or it.get("_official_html"):
            left += "<div class='lab'>Official solution (original)</div>" + "".join(f"<img src='{c}'>" for c in sol)
            right += f"<div class='lab'>Official solution (digitized)</div>{it.get('_official_html', '')}"
        body.append(f"<section class='row' id='{html.escape(z['id'])}-{it.get('n')}'><h2>{html.escape(z['title'])} · {name}"
                    f"<span class='score'>risk {score:g}</span><br>{chips}</h2><div class='orig'>{left}</div><div class='dig'>{right}</div></section>")
    out = (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Digitization check</title><style>{CSS}</style><header><h1>Digitization check</h1>"
           f"{len(rows)} items, riskiest first. Compare each original with its digitized version.</header>{''.join(body)}")
    Path(out_path).write_text(out, encoding="utf-8")
    return len(rows), [(r[0], r[1]["id"], r[2].get("name")) for r in rows[:8]]
