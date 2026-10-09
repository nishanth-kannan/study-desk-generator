#!/usr/bin/env python3
"""Study Desk builder -- checks the data and writes one self-contained HTML file.

    python3 build_desk.py data/ -o "<Course> Study Desk.html" [--sections concepts,questions,papers,reference]
                                [--theme teal|<hue>|random] [--check]

DATA is a folder (meta.json, papers/*.json, concepts/*.json, bank/*.json -- see sdcommon.load_data)
or a single data.json. Paths inside it (sources/, work/) are relative to the desk folder.

What it does, in order:
  1. fidelity checks on every past paper (checks.py): precision, recall, numbers, tables/figures, official solutions
  2. calc blocks: Excel formulae recalculated in LibreOffice and checked (calc.py); writes the workings .xlsx
  3. composes solutions (official + explanation + Excel working), renders rich blocks, expands bank refs
  4. validates ids, answers, units, tables, blocks; unconfirmed scaffold guesses (_check) are errors
  5. writes work/compare.html (originals beside the digitized items, riskiest first), then the desk
Prints errors and a short summary; the full report goes to work/build_report.txt.
"""
import argparse, base64, html, json, math, mimetypes, os, re, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "desk_template.html"
sys.path.insert(0, str(HERE))
import calc as CALC, checks as CHECKS, compare as COMPARE  # noqa: E402
from sdcommon import load_data  # noqa: E402
BASE = Path(".")
WARN = []
VERSION = (HERE / "VERSION").read_text().strip() if (HERE / "VERSION").exists() else "dev"
DIFFS = {"E", "M", "H"}
BLOCK_TYPES = {"h", "p", "list", "defs", "table", "box", "fig", "pre", "flow", "cpm", "chart", "gantt", "ex"}
SECTION_ALIASES = {"concepts": "concepts", "concept": "concepts", "notes": "concepts", "theory": "concepts",
    "questions": "questions", "question": "questions", "quiz": "questions", "bank": "questions", "mcq": "questions", "mcqs": "questions",
    "papers": "papers", "paper": "papers", "pyq": "papers", "pyqs": "papers", "past": "papers", "past-papers": "papers",
    "reference": "reference", "guide": "reference", "cheatsheet": "reference", "cheat-sheet": "reference", "quick-reference": "reference"}
SECTION_DATA = {"concepts": "concepts", "questions": "questions", "papers": "papers", "reference": "guide"}

E = lambda s: html.escape(str(s), quote=True)
ERR = []

# ------------------------------------------------------------------ renderers
def r_table(b, where):
    head, rows = b.get("head") or [], b.get("rows") or []
    if not head: ERR.append(f"{where}: table needs 'head'")
    for i, r in enumerate(rows):
        if len(r) != len(head):
            ERR.append(f"{where}: table row {i+1} has {len(r)} cells, header has {len(head)}")
    g = ""
    if b.get("groups"):
        if sum(s for _, s in b["groups"]) != len(head):
            ERR.append(f"{where}: table groups span {sum(s for _, s in b['groups'])} columns, header has {len(head)}")
        g = "<tr class='rx-g'>" + "".join(f"<th colspan='{s}'>{l}</th>" for l, s in b["groups"]) + "</tr>"
    t = ("<div class='scroller'><table class='rx-t'><thead>" + g + "<tr>" + "".join(f"<th>{h}</th>" for h in head)
         + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
         + "</tbody></table></div>")
    return fig_wrap(t, b)

def fig_wrap(inner, b):
    ttl = f"<div class='rx-title'>{b['title']}</div>" if b.get("title") else ""
    cap = f"<figcaption>{b['caption']}</figcaption>" if b.get("caption") else ""
    note = f"<div class='rx-note'>{b['note']}</div>" if b.get("note") else ""
    return f"<figure class='rx'>{ttl}{inner}{cap}{note}</figure>"

def r_fig(b, where):
    if b.get("svg"):
        inner = b["svg"]
    elif b.get("src"):
        src = b["src"]
        if not src.startswith("data:"):
            p = Path(src) if Path(src).is_absolute() else BASE / src
            if not p.exists(): ERR.append(f"{where}: image {src} not found"); return ""
            mt = mimetypes.guess_type(p.name)[0] or "image/png"
            src = f"data:{mt};base64," + base64.b64encode(p.read_bytes()).decode()
        mw = f"max-width:{b['maxw']}px;" if b.get("maxw") else ""
        inner = f"<img src='{src}' alt='{E(b.get('alt', b.get('caption', 'figure')))}' style='{mw}'>"
    else:
        ERR.append(f"{where}: fig needs 'svg' or 'src'"); return ""
    return fig_wrap(f"<div class='rx-fig'>{inner}</div>", b)

def r_pre(b, where):
    return fig_wrap(f"<pre class='rx-pre'>{E(b.get('x', ''))}</pre>", b)

_MID = [0]
def svg_open(w, h):
    _MID[0] += 1
    return (f"<svg class='rx-svg' viewBox='0 0 {w:.0f} {h:.0f}' style='width:100%;max-width:{w:.0f}px;min-width:{min(w, 560):.0f}px' "
            f"role='img' xmlns='http://www.w3.org/2000/svg'><defs><marker id='rxa{_MID[0]}' viewBox='0 0 10 10' refX='9' refY='5' "
            f"markerWidth='7' markerHeight='7' orient='auto-start-reverse'><path d='M0,0 L10,5 L0,10 z' class='ah'/></marker></defs>")

def txt(x, y, s, cls="t", anchor="middle", size=None):
    lines = str(s).split("\n")
    fs = f" font-size='{size}'" if size else ""
    off = (len(lines) - 1) * 7
    return "".join(f"<text x='{x:.1f}' y='{y - off + i*14:.1f}' text-anchor='{anchor}' class='{cls}'{fs} dominant-baseline='middle'>{E(l)}</text>"
                   for i, l in enumerate(lines))

def clip(cx, cy, hw, hh, tx, ty):
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0: return cx, cy
    s = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return cx + dx * s, cy + dy * s

def r_flow(b, where, boxes=None):
    nodes = b.get("nodes") or []
    ids = {n["id"] for n in nodes}
    if len(ids) != len(nodes): ERR.append(f"{where}: flow has duplicate node ids")
    for e in b.get("edges") or []:
        for k in e[:2]:
            if k not in ids: ERR.append(f"{where}: flow edge references unknown node {k!r}")
    cw, rh = b.get("colw", 150), b.get("rowh", 90)
    bw, bh = b.get("boxw", 112), b.get("boxh", 48)
    longest = max([len(l) for n in nodes if n.get("shape") != "store" for l in str(n.get("label", n["id"])).split("\n")] or [0])
    bw = max(bw, longest * 7.6 + 16)          # never let a label spill out of its box
    cw = max(cw, bw + 28)
    pos = {n["id"]: (40 + n["col"] * cw + bw / 2, 30 + n["row"] * rh + bh / 2) for n in nodes}
    W = max(p[0] for p in pos.values()) + bw / 2 + 30 if pos else 100
    H = max(p[1] for p in pos.values()) + bh / 2 + 30 if pos else 100
    out = [svg_open(W, H)]
    shape = {n["id"]: n.get("shape", "task") for n in nodes}
    for e in b.get("edges") or []:
        a, c = pos.get(e[0]), pos.get(e[1])
        if not a or not c: continue
        ha = (24, 20) if shape[e[0]] == "store" else (bw / 2, bh / 2)
        hc = (24, 20) if shape[e[1]] == "store" else (bw / 2, bh / 2)
        x1, y1 = clip(*a, *ha, *c); x2, y2 = clip(*c, hc[0] + 2, hc[1] + 2, *a)
        cls = "ln dash" if (len(e) > 3 and e[3] == "info") else "ln"
        out.append(f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' class='{cls}' marker-end='url(#rxa{_MID[0]})'/>")
        if len(e) > 2 and e[2]:
            out.append(txt((x1 + x2) / 2, (y1 + y2) / 2 - 9, e[2], "t s"))
    for n in nodes:
        x, y = pos[n["id"]]; sh = shape[n["id"]]
        crit = " crit" if n.get("hl") else ""
        if sh == "store":
            out.append(f"<path d='M{x-22},{y-18} L{x+22},{y-18} L{x},{y+18} z' class='bx{crit}'/>")
            out.append(txt(x, y + bh / 2 + 10, n.get("label", ""), "t s"))
            continue
        if sh == "decision":
            out.append(f"<path d='M{x},{y-bh/2} L{x+bw/2},{y} L{x},{y+bh/2} L{x-bw/2},{y} z' class='bx{crit}'/>")
        elif sh == "terminal":
            out.append(f"<rect x='{x-bw/2}' y='{y-bh/2}' width='{bw}' height='{bh}' rx='{bh/2}' class='bx{crit}'/>")
        else:
            out.append(f"<rect x='{x-bw/2}' y='{y-bh/2}' width='{bw}' height='{bh}' rx='4' class='bx{crit}'/>")
        out.append(txt(x, y, n.get("label", n["id"]), "t b"))
        if n.get("sub"): out.append(txt(x, y + bh / 2 + 11, n["sub"], "t s"))
        if n.get("tl"): out.append(txt(x - bw / 2 + 4, y - bh / 2 - 8, n["tl"], "t s", "start"))
        if n.get("tr"): out.append(txt(x + bw / 2 - 4, y - bh / 2 - 8, n["tr"], "t s", "end"))
        if n.get("bl"): out.append(txt(x - bw / 2 + 4, y + bh / 2 + 10, n["bl"], "t s", "start"))
        if n.get("br"): out.append(txt(x + bw / 2 - 4, y + bh / 2 + 10, n["br"], "t s", "end"))
    out.append("</svg>")
    return fig_wrap("<div class='rx-fig'>" + "".join(out) + "</div>", b)

def cpm_compute(acts, where):
    A = {a["id"]: a for a in acts}
    for a in acts:
        for p in a.get("pred", []):
            if p not in A: ERR.append(f"{where}: cpm activity {a['id']} has unknown predecessor {p!r}")
    order, seen, tmp = [], set(), set()
    def visit(k):
        if k in seen: return
        if k in tmp: ERR.append(f"{where}: cpm network has a cycle at {k}"); return
        tmp.add(k)
        for p in A[k].get("pred", []):
            if p in A: visit(p)
        tmp.discard(k); seen.add(k); order.append(k)
    for k in A: visit(k)
    es, ef = {}, {}
    for k in order:
        es[k] = max([ef[p] for p in A[k].get("pred", []) if p in ef] or [0]); ef[k] = es[k] + A[k]["d"]
    T = max(ef.values()) if ef else 0
    succ = {k: [j for j in A if k in A[j].get("pred", [])] for k in A}
    lf, ls = {}, {}
    for k in reversed(order):
        lf[k] = min([ls[s] for s in succ[k]] or [T]); ls[k] = lf[k] - A[k]["d"]
    return order, es, ef, ls, lf, T, succ

def r_cpm(b, where):
    acts = b.get("acts") or []
    order, es, ef, ls, lf, T, succ = cpm_compute(acts, where)
    if ERR: return ""
    A = {a["id"]: a for a in acts}
    depth = {}
    for k in order:
        depth[k] = 1 + max([depth[p] for p in A[k].get("pred", [])] or [0])
    cols = {}
    for k in order: cols.setdefault(depth[k], []).append(k)
    nodes = [{"id": "__start", "label": "Start", "col": 0, "row": (max(len(v) for v in cols.values()) - 1) / 2, "shape": "terminal"}]
    for d, ks in cols.items():
        off = (max(len(v) for v in cols.values()) - len(ks)) / 2
        for i, k in enumerate(ks):
            crit = abs(ls[k] - es[k]) < 1e-9
            nodes.append({"id": k, "label": f"{k}  ({A[k]['d']:g})", "col": d, "row": off + i, "hl": crit,
                          "tl": f"ES {es[k]:g}", "tr": f"EF {ef[k]:g}", "bl": f"LS {ls[k]:g}", "br": f"LF {lf[k]:g}"})
    maxd = max(cols) + 1
    nodes.append({"id": "__finish", "label": f"Finish  {T:g}", "col": maxd, "row": nodes[0]["row"], "shape": "terminal"})
    edges = [["__start", k] for k in A if not A[k].get("pred")] + \
            [[p, k] for k in A for p in A[k].get("pred", [])] + [[k, "__finish"] for k in A if not succ[k]]
    crit_path = [k for k in order if abs(ls[k] - es[k]) < 1e-9]
    cap = b.get("caption") or ""
    cap += f"{' · ' if cap else ''}Critical activities (highlighted): {', '.join(crit_path)} · duration {T:g}"
    return r_flow({"nodes": nodes, "edges": edges, "caption": cap, "colw": b.get("colw", 150), "rowh": b.get("rowh", 100),
                   "boxw": 96, "boxh": 40, "note": b.get("note")}, where)

def nice_ticks(lo, hi, n=5):
    if hi <= lo: hi = lo + 1
    raw = (hi - lo) / n; mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    t, out = math.floor(lo / step) * step, []
    while t <= hi + 1e-9: out.append(round(t, 10)); t += step
    return out

def fmt(v): return f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:g}"

def r_chart(b, where):
    kind = b.get("kind", "line")
    W, H, L, R, T, B = b.get("w", 620), b.get("h", 300), 62, 20, 22, 46
    out = []
    if kind == "bar":
        bars = b.get("bars") or []
        if not bars: ERR.append(f"{where}: bar chart needs 'bars'"); return ""
        ys = nice_ticks(min(0, min(v for _, v in bars)), max(v for _, v in bars))
        y0, y1 = ys[0], ys[-1]
        sy = lambda v: T + (H - T - B) * (1 - (v - y0) / (y1 - y0))
        out.append(svg_open(W, H))
        for v in ys:
            out.append(f"<line x1='{L}' x2='{W-R}' y1='{sy(v):.1f}' y2='{sy(v):.1f}' class='gd'/>" + txt(L - 6, sy(v), fmt(v), "t s", "end"))
        slot = (W - L - R) / len(bars)
        for i, (lab, v) in enumerate(bars):
            x = L + i * slot + slot * 0.18; bw = slot * 0.64
            out.append(f"<rect x='{x:.1f}' y='{min(sy(v), sy(0)):.1f}' width='{bw:.1f}' height='{abs(sy(v)-sy(0)):.1f}' class='bar'/>")
            out.append(txt(x + bw / 2, sy(v) - 9, fmt(v), "t s"))
            out.append(txt(x + bw / 2, H - B + 16, lab, "t s"))
    else:
        series = b.get("series") or [{"name": "", "points": b.get("points") or []}]
        pts = [p for s in series for p in s["points"]]
        if not pts: ERR.append(f"{where}: line chart needs 'points' or 'series'"); return ""
        xs = [p[0] for p in pts]; x0, x1 = min(xs), max(xs)
        if b.get("xrange"): x0, x1 = b["xrange"]
        ys = nice_ticks(min(0, min(p[1] for p in pts)), max(p[1] for p in pts) * 1.08)
        y0, y1 = ys[0], ys[-1]
        sx = lambda v: L + (W - L - R) * (v - x0) / ((x1 - x0) or 1)
        sy = lambda v: T + (H - T - B) * (1 - (v - y0) / ((y1 - y0) or 1))
        out.append(svg_open(W, H))
        for v in ys:
            out.append(f"<line x1='{L}' x2='{W-R}' y1='{sy(v):.1f}' y2='{sy(v):.1f}' class='gd'/>" + txt(L - 6, sy(v), fmt(v), "t s", "end"))
        xt = b.get("xticks") or [[v, fmt(v)] for v in nice_ticks(x0, x1, 8) if x0 <= v <= x1]
        for v, lab in xt:
            out.append(f"<line x1='{sx(v):.1f}' x2='{sx(v):.1f}' y1='{H-B}' y2='{H-B+4}' class='ax'/>" + txt(sx(v), H - B + 15, lab, "t s"))
        out.append(f"<line x1='{L}' x2='{W-R}' y1='{H-B}' y2='{H-B}' class='ax'/><line x1='{L}' x2='{L}' y1='{T}' y2='{H-B}' class='ax'/>")
        for si, s in enumerate(series):
            d = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(s["points"]))
            out.append(f"<path d='{d} L{sx(s['points'][-1][0]):.1f},{sy(y0):.1f} L{sx(s['points'][0][0]):.1f},{sy(y0):.1f} z' class='area s{si}'/>" if b.get("fill", True) and len(series) == 1 else "")
            out.append(f"<path d='{d}' class='pl s{si}'/>")
            if b.get("annotate"):
                for x, y in s["points"]:
                    if y > 0 or b.get("annotate") == "all":
                        out.append(f"<circle cx='{sx(x):.1f}' cy='{sy(y):.1f}' r='3' class='pt s{si}'/>" + txt(sx(x), sy(y) - 11, fmt(y), "t s"))
        if len(series) > 1:
            for si, s in enumerate(series):
                out.append(f"<line x1='{W-R-150}' x2='{W-R-130}' y1='{T+8+si*16}' y2='{T+8+si*16}' class='pl s{si}'/>" + txt(W - R - 125, T + 8 + si * 16, s["name"], "t s", "start"))
    if b.get("xlabel"): out.append(txt((L + W - R) / 2, H - 10, b["xlabel"], "t s"))
    if b.get("ylabel"): out.append(f"<text x='14' y='{(T+H-B)/2}' transform='rotate(-90 14 {(T+H-B)/2})' text-anchor='middle' class='t s'>{E(b['ylabel'])}</text>")
    out.append("</svg>")
    return fig_wrap("<div class='rx-fig'>" + "".join(out) + "</div>", b)

def r_gantt(b, where):
    rows = b.get("rows") or []
    if not rows: ERR.append(f"{where}: gantt needs rows"); return ""
    end = max(e for r in rows for s, e, *_ in r["bars"])
    W, L, R, rh = b.get("w", 640), b.get("lw", 90), 20, 30
    H = 30 + len(rows) * rh + 34
    sx = lambda v: L + (W - L - R) * v / end
    out = [svg_open(W, H)]
    ticks = b.get("ticks") or nice_ticks(0, end, 10)
    for v in ticks:
        if v <= end: out.append(f"<line x1='{sx(v):.1f}' x2='{sx(v):.1f}' y1='20' y2='{H-30}' class='gd'/>" + txt(sx(v), H - 20, fmt(v), "t s"))
    for i, r in enumerate(rows):
        y = 24 + i * rh
        out.append(txt(L - 8, y + rh / 2 - 2, r["label"], "t s", "end"))
        for bar in r["bars"]:
            s, e = bar[0], bar[1]; lab = bar[2] if len(bar) > 2 else ""
            cls = "gb alt" if (len(bar) > 3 and bar[3]) else "gb"
            out.append(f"<rect x='{sx(s):.1f}' y='{y+3}' width='{max(sx(e)-sx(s),1):.1f}' height='{rh-10}' rx='2' class='{cls}'/>")
            if lab: out.append(txt((sx(s) + sx(e)) / 2, y + rh / 2 - 2, lab, "t s on"))
    if b.get("xlabel"): out.append(txt((L + W - R) / 2, H - 6, b["xlabel"], "t s"))
    out.append("</svg>")
    return fig_wrap("<div class='rx-fig'>" + "".join(out) + "</div>", b)

def r_simple(b, where):
    t = b.get("t")
    if t == "p": return f"<p>{b['x']}</p>"
    if t == "list": return "<ul>" + "".join(f"<li>{i}</li>" for i in b["x"]) + "</ul>"
    if t == "box": return f"<div class='cbox'><h4>{b.get('title','')}</h4><p>{b['x']}</p></div>"

RENDER = {"table": r_table, "fig": r_fig, "pre": r_pre, "flow": r_flow, "cpm": r_cpm,
          "chart": r_chart, "gantt": r_gantt, "p": r_simple, "list": r_simple, "box": r_simple}
RICH_IN_NOTES = {"fig", "pre", "flow", "cpm", "chart", "gantt"}

def render_blocks(blocks, where):
    out = []
    for i, b in enumerate(blocks or []):
        f = RENDER.get(b.get("t"))
        if not f: ERR.append(f"{where}: unknown media block type {b.get('t')!r} (allowed: {', '.join(sorted(RENDER))})"); continue
        out.append(f(b, f"{where} block {i+1}"))
    return "".join(out)

def attach(item, where, stats):
    m, em = item.pop("media", None), item.pop("emedia", None)
    if m:
        item["q"] = f"<div>{item.get('q','')}</div>" + render_blocks(m, where + " media")
        stats.append((where, "stem", [b.get("t") for b in m]))
    if em:
        item["e"] = item.get("e", "") + "<div class='rx-e'>" + render_blocks(em, where + " emedia") + "</div>"
        stats.append((where, "answer", [b.get("t") for b in em]))




# ------------------------------------------------------------------ colour themes
import colorsys, hashlib, random
# Named hues (degrees). Amber/orange (25-55) and the red and green families are avoided so the
# accent never collides with the desk's flag (amber), wrong (red) and right (green) colours.
THEMES = {"indigo": 228, "cobalt": 212, "ocean": 198, "teal": 178, "moss": 95,
          "plum": 290, "violet": 265, "orchid": 310, "berry": 330, "rose": 345, "slate": 220}

def _hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l, s)
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in (r, g, b))

def _lum(hx):
    c = [int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)

def make_theme(hue, sat=0.55):
    """Accent family + hue-tinted neutrals for light and dark, contrast-checked (>= 6:1, above WCAG AA)."""
    s = 0.12 if hue == THEMES["slate"] else sat
    L = 0.36
    while _contrast(_hex(hue, s, L), "#ffffff") < 6 and L > 0.12: L -= 0.02     # accent on white / white on accent
    D = 0.72
    while _contrast(_hex(hue, s, D), "#1c1f25") < 6 and D < 0.92: D += 0.02     # accent on dark surface
    light = {"--accent": _hex(hue, s, L), "--accent-soft": _hex(hue, min(s, .6), .94), "--accent-ink": _hex(hue, s, L - .08),
             "--on-accent": "#ffffff", "--paper": _hex(hue, .22, .968), "--surface-2": _hex(hue, .18, .935),
             "--line": _hex(hue, .14, .855), "--line-2": _hex(hue, .11, .78)}
    dark = {"--accent": _hex(hue, s, D), "--accent-soft": _hex(hue, min(s, .35), .17), "--accent-ink": _hex(hue, s, min(D + .1, .95)),
            "--on-accent": "#14161a", "--paper": _hex(hue, .16, .085), "--surface": _hex(hue, .13, .12),
            "--surface-2": _hex(hue, .12, .15), "--line": _hex(hue, .1, .215), "--line-2": _hex(hue, .09, .29)}
    decl = lambda m: ";".join(f"{k}:{v}" for k, v in m.items())
    return (f"\n/* theme hue {hue} */\n:root{{{decl(light)}}}\n"
            f"@media (prefers-color-scheme:dark){{:root:not([data-theme=\"light\"]){{{decl(dark)}}}}}\n"
            f":root[data-theme=\"dark\"]{{{decl(dark)}}}\n"
            ".top{border-top:4px solid var(--accent)}\n"), light["--accent"], dark["--accent"]

def pick_theme(d, cli):
    """--theme / meta.theme: a name from THEMES, a hue 0-359, or 'random'.
    Default: a hue derived from meta.storageKey, so each desk gets its own colour and keeps it on rebuilds."""
    want = cli or (d.get("meta") or {}).get("theme")
    if want in (None, "", "auto"):
        key = (d.get("meta") or {}).get("storageKey") or (d.get("meta") or {}).get("title", "")
        names = sorted(THEMES)
        name = names[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(names)]
        return name, THEMES[name]
    if want == "random":
        name = random.choice(sorted(THEMES)); return name, THEMES[name]
    if str(want).lower() in THEMES: return str(want).lower(), THEMES[str(want).lower()]
    try:
        h = int(want) % 360; return f"hue {h}", h
    except ValueError:
        sys.exit(f"Unknown theme {want!r}. Use one of: {', '.join(sorted(THEMES))}, a hue 0-359, or 'random'.")


# ------------------------------------------------------------------ refs: verbatim source inside bank questions
def strip_tags(x): return re.sub(r"</?[A-Za-z!][^>]*>", " ", str(x))

def expand_refs(d):
    idx = {}
    for z in d.get("papers") or []:
        last_head = None
        for it in z.get("qs") or []:
            if it.get("head"): last_head = it; continue
            idx[(z["id"], it.get("n"))] = (z, last_head, it)
    for q in d.get("questions") or []:
        r = q.pop("ref", None)
        if not r: continue
        k = (r.get("paper"), r.get("n"))
        if k not in idx:
            ERR.append(f"question {q.get('id')}: ref {k} does not match any past-paper item"); continue
        z, hd, it = idx[k]
        lab = it.get("name") or it.get("label") or f"Q{it.get('n')}"
        body = (f"<div>{hd['q']}</div>" if hd and r.get("with_head", True) else "") + f"<div>{it['q']}</div>"
        q["q"] = (f"<p>{q['q']}</p><details class='rx-src'{' open' if r.get('open') else ''}><summary>{z['title']} · {lab} "
                  f"(verbatim)</summary>{body}</details>")

# ------------------------------------------------------------------ validation (same rules as the original study-desk)
def validate(d):
    errors, warnings = [], []
    meta = d.get("meta") or {}
    for k in ("title", "storageKey"):
        if not meta.get(k): errors.append(f"meta.{k} is required")
    units = d.get("units") or []
    if not units: errors.append("units is empty -- declare every chapter/module/week")
    unit_ids = set()
    for u in units:
        if "id" not in u or "name" not in u: errors.append(f"unit missing id or name: {u!r}"); continue
        if u["id"] in unit_ids: errors.append(f"duplicate unit id {u['id']!r}")
        unit_ids.add(u["id"])
    seen = set()
    for q in d.get("questions") or []:
        tag = f"question id={q.get('id')!r}"
        for k in ("id", "unit", "q", "o", "a", "e"):
            if k not in q: errors.append(f"{tag}: missing '{k}'")
        if q.get("id") in seen: errors.append(f"{tag}: duplicate id")
        seen.add(q.get("id"))
        if unit_ids and q.get("unit") not in unit_ids: errors.append(f"{tag}: unit {q.get('unit')!r} not declared")
        opts = q.get("o")
        if isinstance(opts, list):
            if len(opts) < 2: errors.append(f"{tag}: needs at least 2 options")
            if len(set(opts)) != len(opts): warnings.append(f"{tag}: duplicate option text")
            if not isinstance(q.get("a"), int) or not (0 <= q["a"] < len(opts)): errors.append(f"{tag}: answer index {q.get('a')!r} out of range")
        if q.get("diff") and q["diff"] not in DIFFS: errors.append(f"{tag}: diff must be E/M/H")
        if not q.get("topic"): warnings.append(f"{tag}: no topic")
        if q.get("e") and len(strip_tags(q["e"])) < 60: warnings.append(f"{tag}: explanation is very short")
    pids = set()
    for z in d.get("papers") or []:
        pid = z.get("id")
        if not pid or not z.get("title"): errors.append(f"paper missing id or title: {z.get('title')!r}")
        if pid in pids: errors.append(f"duplicate paper id {pid!r}")
        pids.add(pid)
        ns = set()
        for it in z.get("qs") or []:
            t = f"paper {pid!r} item n={it.get('n')}"
            if not isinstance(it.get("n"), int): errors.append(f"{t}: 'n' must be an integer (use 'label' for the printed number)")
            elif it["n"] in ns: errors.append(f"{t}: duplicate n")
            ns.add(it.get("n"))
            if it.get("head"): continue
            if it.get("parts"):
                for k, pt in enumerate(it["parts"]):
                    if pt.get("head"): continue
                    pl = f"{t} ({it.get('name')} {pt.get('label') or 'part ' + str(k + 1)})"
                    if not pt.get("e") and not it.get("e"): errors.append(f"{pl}: needs a solution -- write e_explain on the part")
                    elif pt.pop("_needs_explain", False): errors.append(f"{pl}: has the printed key but no e_explain")
                    if pt.get("num") is not None:
                        nv = pt["num"] if isinstance(pt["num"], list) else [pt["num"]]
                        if not nv or not all(isinstance(v, (int, float)) for v in nv): errors.append(f"{pl}: num must be a number or a list of numbers")
                        continue
                    ch = pt.get("o") or pt.get("choices")
                    if ch:
                        if pt.get("a") is None: errors.append(f"{pl}: answer not set -- set 'a' (zero-based)")
                        elif not isinstance(pt.get("a"), int) or not (0 <= pt["a"] < len(ch)): errors.append(f"{pl}: answer index out of range")
                it.pop("_needs_explain", None)
                continue
            if not it.get("e"): errors.append(f"{t} ({it.get('name')}): needs a solution -- write e_explain")
            elif it.pop("_needs_explain", False): errors.append(f"{t} ({it.get('name')}): has the official solution but no e_explain")
            if it.get("sub") or it.get("num") is not None: continue
            o = it.get("o")
            if not isinstance(o, list) or len(o) < 2: errors.append(f"{t}: needs options (or \"sub\": true)"); continue
            if it.get("a") is None: errors.append(f"{t} ({it.get('name')}): answer not set -- no key was supplied, so solve it and set 'a'")
            elif not isinstance(it.get("a"), int) or not (0 <= it["a"] < len(o)): errors.append(f"{t}: answer index out of range")
    cu = []
    for c in d.get("concepts") or []:
        t = f"concept unit={c.get('unit')!r}"
        if unit_ids and c.get("unit") not in unit_ids: errors.append(f"{t}: unit not declared")
        cu.append(c.get("unit"))
        bl = c.get("blocks") or []
        if not bl: errors.append(f"{t}: has no blocks")
        for b in bl:
            if b.get("t") not in BLOCK_TYPES and not b.get("raw"): errors.append(f"{t}: unknown block type {b.get('t')!r}")
            if b.get("t") == "defs" and any(len(p) != 2 for p in b.get("x") or []): errors.append(f"{t}: defs need [term, definition] pairs")
            if b.get("t") == "box" and not b.get("title"): errors.append(f"{t}: box needs a title")
    qs = d.get("questions") or []
    if qs and unit_ids:
        per = Counter(q.get("unit") for q in qs)
        for u in unit_ids:
            if per.get(u, 0) == 0: warnings.append(f"unit {u!r} has no questions")
            elif per[u] < 8: warnings.append(f"unit {u!r} has only {per[u]} questions")
            if cu and u not in cu: warnings.append(f"unit {u!r} has no concept notes")
    return errors, warnings

def report(d, out):
    qs = d.get("questions") or []
    out.append(f"{d['meta']['title']} -- sections: {', '.join(d['meta'].get('sections') or ['(all present)'])}")
    out.append(f"{len(qs)} generated questions · {len(d.get('concepts') or [])} concept chapters · {len(d.get('papers') or [])} past papers")
    per = Counter(q.get("unit") for q in qs)
    for u in d.get("units") or []:
        n = per.get(u["id"], 0)
        if qs: out.append(f"   {str(u['id']):>3}  {u['name'][:34]:34s} {n:3d}  {'#' * n}")
    if qs:
        dc = Counter(q.get("diff") for q in qs)
        out.append("   difficulty  " + "  ".join(f"{k}={dc.get(k,0)} ({dc.get(k,0)/len(qs):.0%})" for k in "EMH"))
    for z in d.get("papers") or []:
        items = [i for i in z.get("qs") or [] if not i.get("head")]
        parts = [p for i in items for p in i.get("parts") or [] if not p.get("head")]
        mcq = sum(1 for i in items if i.get("o") and not i.get("sub")) + sum(1 for p in parts if p.get("o") or p.get("choices"))
        num = sum(1 for i in items if i.get("num") is not None) + sum(1 for p in parts if p.get("num") is not None)
        out.append(f"   paper  {z['title'][:40]:40s} {len(items):3d} items, {len(parts)} sub-parts · {mcq} MCQ, {num} numeric, "
                   f"{len(items) + len(parts) - sum(1 for i in items if i.get('parts')) - mcq - num} written")


def compose(it, paper):
    """e = official solution (as supplied) + explanation + Excel working. Official tables/figures sit with the official text."""
    off, exp, calc_html = it.pop("e_official", None), it.pop("e_explain", None), it.pop("_calc_html", "")
    if it.get("e"):
        it["e"] += calc_html; return
    if off is None and exp is None and not calc_html: return
    em = it.get("emedia") or []
    off_media = [b for b in em if b.get("_src")]
    if off_media: it["emedia"] = [b for b in em if not b.get("_src")] or None
    if not paper:
        it["e"] = (exp or "") + calc_html; return
    if off is not None:
        off_html = off + (render_blocks(off_media, f"{it.get('name')} official emedia") if off_media else "")
        it["_official_html"] = off_html
        it["e"] = f"<h5>Official solution (as supplied)</h5>{off_html}<h5>Explanation</h5>{exp or ''}{calc_html}"
        if not exp: it["_needs_explain"] = True
    else:
        it["e"] = f"<h5>Worked solution (no official solution was supplied)</h5>{exp or ''}{calc_html}" if (exp or calc_html) else ""


# ------------------------------------------------------------------ past-paper examples inside concept notes
def _answer_line(x):
    ch = x.get("o") or x.get("choices")
    if x.get("num") is not None:
        v = x["num"] if isinstance(x["num"], list) else [x["num"]]
        return "<p class='ex-key'><b>Answer:</b> " + " , ".join(f"{n:g}" for n in v) + "</p>"
    if ch and isinstance(x.get("a"), int) and 0 <= x["a"] < len(ch):
        return f"<p class='ex-key'><b>Answer:</b> {'ABCDEFGHIJ'[x['a']]}. {ch[x['a']]}</p>"
    return ""


def _opts(x):
    ch = x.get("o") or x.get("choices")
    return ("<ol class='ex-opts' type='A'>" + "".join(f"<li>{o}</li>" for o in ch) + "</ol>") if ch else ""


def expand_examples(d, papers):
    """{"t": "ex", "paper": id, "n": item n, "part": label or 1-based index?, "why": html?, "with_stem": bool?}
    -> the verbatim past-paper question (copied from the built paper, never retyped), its answer and
    explanation behind a toggle, and a link to practise it in Past Papers."""
    idx = {(z["id"], it.get("n")): (z, it) for z in papers for it in z.get("qs") or []}
    for c in d.get("concepts") or []:
        for i, b in enumerate(c.get("blocks") or []):
            if b.get("t") != "ex": continue
            where = f"concept unit={c.get('unit')} block {i+1}"
            hit = idx.get((b.get("paper"), b.get("n")))
            if not hit: ERR.append(f"{where}: ex block points to paper {b.get('paper')!r} n={b.get('n')!r}, which is not in the desk"); continue
            z, it = hit
            key = f"{z['id']}-{it.get('n')}"
            parts = it.get("parts") or []
            pt, pk = None, key
            if b.get("part") is not None:
                if isinstance(b["part"], int) and 1 <= b["part"] <= len(parts): k = b["part"] - 1
                else: k = next((j for j, p in enumerate(parts) if (p.get("label") or "").strip() == str(b["part"]).strip()), None)
                if k is None: ERR.append(f"{where}: {it.get('name')} has no part {b['part']!r}"); continue
                pt, pk = parts[k], f"{key}.{k}"
            name = f"{z['title']} · {it.get('name') or 'Q' + str(it.get('n'))}" + (f" {pt.get('label')}" if pt and pt.get("label") else "")
            h = f"<div class='ex-card'><div class='ex-head'>From the past paper · {name}</div>"
            if b.get("why"): h += f"<p class='ex-why'>{b['why']}</p>"
            if pt is not None:
                if b.get("with_stem", True) and strip_tags(it.get("q", "")).strip():
                    h += f"<details class='rx-src'><summary>Question stem (verbatim)</summary><div>{it['q']}</div></details>"
                pre = next((p for p in parts[:parts.index(pt)][::-1] if p.get("head")), None)
                if pre and b.get("with_stem", True):
                    h += f"<details class='rx-src'><summary>Text before this part (verbatim)</summary><div>{pre['q']}</div></details>"
                h += f"<div class='ex-q'>{pt.get('q', '')}</div>{_opts(pt)}"
                ans = _answer_line(pt) + (pt.get("e") or "")
            else:
                h += f"<div class='ex-q'>{it.get('q', '')}</div>{_opts(it)}"
                ans = _answer_line(it)
                for p in parts:
                    if p.get("head"): h += f"<div class='ex-q ex-mid'>{p.get('q', '')}</div>"; continue
                    h += f"<div class='ex-q'>{p.get('q', '')}</div>{_opts(p)}"
                    ans += (f"<h5>{p.get('label') or ''}</h5>" if p.get("label") else "") + _answer_line(p) + (p.get("e") or "")
                ans += it.get("e") or ""
            h += f"<details class='ex-ans'><summary>Show answer and explanation</summary>{ans}</details>"
            h += f"<button class='chip ex-go' data-paper='{z['id']}' data-pkey='{pk}'>Practise it in Past Papers →</button></div>"
            c["blocks"][i] = {"t": "p", "raw": 1, "x": h}



def strip_private(d):
    for z in d.get("papers") or []:
        for k in ("source", "solution_source", "_check", "verbatim_min"): z.pop(k, None)
        for it in z.get("qs") or []:
            for k in [k for k in it if k.startswith("_") or k in ("src", "e_src", "calc")]: it.pop(k)
            for pt in it.get("parts") or []:
                for k in [k for k in pt if k.startswith("_") or k in ("src", "calc", "blanks")]: pt.pop(k)
    for q in d.get("questions") or []:
        for k in [k for k in q if k.startswith("_") or k == "calc"]: q.pop(k)


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("-o", "--out"); ap.add_argument("--check", action="store_true")
    ap.add_argument("--sections", help="any of concepts, questions, papers, reference (synonyms accepted)")
    ap.add_argument("--theme", help="theme name (" + ", ".join(sorted(THEMES)) + "), a hue 0-359, or random")
    ap.add_argument("--verbatim-threshold", type=float, default=0.9, help="minimum share of past-paper word 3-grams found in the source")
    ap.add_argument("--template", default=str(TEMPLATE))
    a = ap.parse_args()
    d, BASE = load_data(a.data)
    meta = d.setdefault("meta", {})
    if a.sections:
        chosen = []
        for s in re.split(r"[,\s]+", a.sections.strip().lower()):
            if not s: continue
            if s not in SECTION_ALIASES: sys.exit(f"Unknown section {s!r}. Use concepts, questions, papers, reference.")
            if SECTION_ALIASES[s] not in chosen: chosen.append(SECTION_ALIASES[s])
        meta["sections"] = chosen
    if meta.get("sections"):
        for sec, key in SECTION_DATA.items():
            if sec not in meta["sections"]: d.pop(key, None)
    out = Path(a.out) if a.out else Path(f"{meta.get('title', 'Study Desk')}.html")
    rep, summary = [f"study-desk tools {VERSION}"], []
    papers = d.get("papers") or []

    # 1. fidelity checks
    rows, risk, IE = [], {}, {}
    for z in papers:
        CHECKS.check_paper(z, BASE, a.verbatim_threshold, ERR, rows, risk, IE)
    if rows:
        lo = sorted(rows, key=lambda r: r[0])[:3]
        n_off = sum(1 for z in papers for it in z.get("qs") or [] if it.get("e_official"))
        summary.append(f"checks: {len(rows)} paper items, lowest match " + ", ".join(f"{i}/{n} {s:.0%}" for s, i, n in lo) +
                       f"; recall, numbers, tables/figures; {n_off} official solutions")
    # 2. calc
    jobs = [(f"{z['id']}-{(it.get('name') or str(it.get('n'))).replace(' ', '')}", it, f"paper {z['id']} {it.get('name') or it.get('n')}")
            for z in papers for it in z.get("qs") or [] if it.get("calc")]
    jobs += [(f"{z['id']}-{(it.get('name') or str(it.get('n'))).replace(' ', '')}-{(pt.get('label') or str(k + 1)).strip('()')}", pt,
              f"paper {z['id']} {it.get('name') or it.get('n')} {pt.get('label') or 'part ' + str(k + 1)}")
             for z in papers for it in z.get("qs") or [] for k, pt in enumerate(it.get("parts") or []) if pt.get("calc")]
    jobs += [(f"bank-{q.get('id')}", q, f"question {q.get('id')}") for q in d.get("questions") or [] if q.get("calc")]
    if jobs:
        res = CALC.run(jobs, out.resolve().parent, ERR, WARN, out.stem)
        if res: summary.append(f"calc: {res['sheets']} workings, {res['proven']} results proven -> {res['xlsx'].name}")
    # 3. compose and render
    for z in papers:
        for it in z.get("qs") or []:
            if not it.get("head"): compose(it, True)
            for pt in it.get("parts") or []:
                if not pt.get("head"): compose(pt, True)
    for q in d.get("questions") or []: compose(q, False)
    stats = []
    for z in papers:          # papers first, so a bank "ref" copies the rendered tables too
        for it in z.get("qs") or []:
            attach(it, f"paper {z.get('id')} {it.get('name') or it.get('label') or it.get('n')}", stats)
            for k, pt in enumerate(it.get("parts") or []):
                attach(pt, f"paper {z.get('id')} {it.get('name')} {pt.get('label') or 'part ' + str(k + 1)}", stats)
    expand_refs(d)
    for q in d.get("questions") or []: attach(q, f"question {q.get('id')}", stats)
    expand_examples(d, papers)
    for c in d.get("concepts") or []:
        c["blocks"] = [({"t": "p", "raw": 1, "x": RENDER[b["t"]](b, f"concept unit={c.get('unit')} block {i+1}")}
                        if b.get("t") in RICH_IN_NOTES or (b.get("t") == "table" and b.get("groups")) else b)
                       for i, b in enumerate(c.get("blocks") or [])]
    # 4. validate
    errors, warnings = validate(d)
    for z in papers:
        for c in z.get("_check") or []: errors.append(f"paper {z['id']}: unconfirmed: {c} -- resolve it, then delete _check")
        for it in z.get("qs") or []:
            for c in it.get("_check") or []:
                errors.append(f"paper {z['id']} {it.get('name') or it.get('n')}: unconfirmed scaffold guess: {c} -- confirm, then delete _check")
    errors = ERR + errors
    warnings = WARN + warnings
    # 5. compare sheet (also when the build fails: it shows where)
    if any(it.get("src") for z in papers for it in z.get("qs") or []):
        cmp_path = BASE / "work" / "compare.html"
        n, top = COMPARE.build(papers, BASE, risk, IE, cmp_path)
        summary.append(f"compare: {n} items -> {cmp_path.relative_to(BASE)} (riskiest: " +
                       ", ".join(f"{p}/{nm} {sc:g}" for sc, p, nm in top[:4]) + ")")
    report(d, rep)
    if stats:
        rep.append(f"Media: {len(stats)} items carry tables/figures")
        for w, where, ts in stats: rep.append(f"    {w:30s} {where:6s} {', '.join(ts)}")
    rep += ["", "WARNINGS"] + warnings + ["", "ERRORS"] + errors
    rp = BASE / "work" / "build_report.txt"
    rp.parent.mkdir(parents=True, exist_ok=True); rp.write_text("\n".join(rep) + "\n")
    if errors:
        print(f"ERRORS ({len(errors)}) -- nothing written:")
        for e in errors[:25]: print("  -", e)
        if len(errors) > 25: print(f"  ... {len(errors) - 25} more in work/build_report.txt")
        for s in summary: print("  " + s)
        sys.exit(1)
    qs = d.get("questions") or []
    if qs:
        dc = Counter(q.get("diff") for q in qs)
        summary.append(f"bank: {len(qs)} questions in {len(set(q.get('unit') for q in qs))} units, " +
                       " / ".join(f"{k} {dc.get(k,0)/len(qs):.0%}" for k in "EMH"))
    for w in warnings[:8]: print("  warning:", w)
    if len(warnings) > 8: print(f"  ... {len(warnings) - 8} more warnings in work/build_report.txt")
    tname, hue = pick_theme(d, a.theme)
    theme_css, acc_l, acc_d = make_theme(hue)
    for s in summary: print("  " + s)
    if a.check: print(f"  theme: {tname} · valid; nothing written (--check)"); return
    strip_private(d)
    tpl = Path(a.template).read_text(encoding="utf-8").replace("</style>", theme_css + "</style>", 1)
    payload = json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</script", "<\\/script")
    htmlout = tpl.replace("__TITLE__", html.escape(meta["title"])).replace("__DESK_DATA__", payload)
    out.write_text(htmlout, encoding="utf-8")
    print(f"  theme: {tname} · wrote {out} ({len(htmlout)/1024:.0f} KB, self-contained)")


if __name__ == "__main__":
    main()
