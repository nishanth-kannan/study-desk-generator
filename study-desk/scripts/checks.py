"""Fidelity checks for past papers: is every word of the print in the desk, and nothing else?

  precision  each item's text (stem, options, shown text, table cells) appears in the source   (>= 90% of word triples)
  options    each MCQ option appears in the source on its own
  recall     every word of the item's source span is in the item; every source line is in some item
  numbers    the numbers in an item equal the numbers in its source span, as a multiset (catches 459 for 450)
  objects    every table/figure in an item's source span is shown with the item
  official   e_official is checked the same way against the solution source

Items made by scaffold.py carry "src" (and "e_src") spans, so each check is exact and local.
Items written by hand (no span) get the precision check and the paper-level recall check.
"""
from collections import Counter
from pathlib import Path

import re

from sdcommon import content_lines, grams, norm, numbers, plain, read_source

SOL_NOTE = re.compile(r"other questions|remaining questions|straightforward|left (?:as an? )?exercise|not (?:covered|provided)", re.I)


OPT_LABEL = re.compile(r"^\s*\(?[A-Ea-e]\s*[.)]\s+")
INLINE_LBL = re.compile(r"\(([A-Ea-e])\)\s*")


def has_options(it):
    return bool(it.get("o")) and not it.get("sub") and not it.get("head")


def segments(it):
    """The item's printed text in order, one segment per block (stem, shown text, each table row, each option)."""
    segs = [it.get("q", "")]
    for b in it.get("media") or []:
        for k in ("title", "x", "caption"):
            if isinstance(b.get(k), str): segs.append(b[k])
        if b.get("t") == "table":
            segs.append(" ".join(map(str, b.get("head") or [])))
            segs += [" ".join(map(str, r)) for r in b.get("rows") or []]
    if has_options(it): segs += list(it.get("o") or [])
    return segs


def item_text(it):
    return " ".join(map(str, segments(it)))


def line_text(r, mcq):
    """A source line as the desk shows it: MCQ option labels are drawn by the desk, so they are not text."""
    t = r["text"]
    if mcq:
        t = INLINE_LBL.sub(" ", t) if len(INLINE_LBL.findall(t)) >= 3 else OPT_LABEL.sub("", t)
    return t


def official_text(it):
    parts = [it.get("e_official", "")]
    for b in it.get("emedia") or []:
        if b.get("t") == "table" and b.get("_src"):
            parts += list(b.get("head") or []) + [c for r in b.get("rows") or [] for c in r]
    return " ".join(map(str, parts))


class Source:
    def __init__(self, path):
        self.path = Path(path)
        self.recs = read_source(path)
        self.content = content_lines(self.recs)
        allw = [t for r in self.recs if r["kind"] in ("text", "table_row", "ignored") for t in norm(r["text"])]
        self.vocab = set(allw)
        self.grams = set(grams(allw))
        self.by_no = {r["no"]: r for r in self.recs}

    def span(self, lo, hi):
        return [r for r in self.content if lo <= r["no"] <= hi]

    def objects(self, lo, hi):
        tabs, figs = set(), set()
        for r in self.recs:
            if lo <= r["no"] <= hi:
                if r["kind"] == "directive" and r["table"] and r["text"].startswith("[[table"): tabs.add(r["table"])
                if r["kind"] == "figure": figs.add(r["fig"]["id"])
        return tabs, figs


def precision(tokens, src):
    g = grams(tokens)
    if len(g) < 1:
        return 1.0 if all(t in src.vocab for t in tokens) else 0.0
    return sum(x in src.grams for x in g) / len(g)


def seg_precision(segs, src):
    """Word-triple precision scored inside each segment, so joins between blocks don't count against it."""
    hit = tot = 0
    for sgt in segs:
        t = norm(sgt)
        if not t: continue
        g = grams(t)
        if g: hit += sum(x in src.grams for x in g); tot += len(g)
        else: hit += sum(w in src.vocab for w in t); tot += len(t)
    return hit / tot if tot else 1.0


def uncovered_runs(src_tokens, item_tokens, min_run=1):
    """Source tokens not covered by any shared word triple (or, for tiny spans, by the item's words)."""
    ig = set(grams(item_tokens))
    cov = [False] * len(src_tokens)
    if len(src_tokens) < 3:
        bag = Counter(item_tokens)
        return [] if all(bag[t] for t in src_tokens) else [" ".join(src_tokens)]
    for i, g in enumerate(grams(src_tokens)):
        if g in ig: cov[i] = cov[i + 1] = cov[i + 2] = True
    runs, cur = [], []
    for i, c in enumerate(cov):
        if not c: cur.append(i)
        elif cur: runs.append(cur); cur = []
    if cur: runs.append(cur)
    out = []
    for r in runs:
        if len(r) >= min_run:
            a, b = max(0, r[0] - 3), min(len(src_tokens), r[-1] + 4)
            ctx = src_tokens[a:r[0]] + ["[" + " ".join(src_tokens[r[0]:r[-1] + 1]) + "]"] + src_tokens[r[-1] + 1:b]
            out.append(" ".join(ctx))
    return out


def num_diff(src_text, item_text_):
    s, i = Counter(numbers(src_text)), Counter(numbers(item_text_))
    return sorted((s - i).elements()), sorted((i - s).elements())


def check_paper(z, base, threshold, ERR, rows, risk, IE=None):
    """Run every check for one paper. ERR gets errors; rows gets (score, paper, name); risk[(paper, n)] gets reasons."""
    if not z.get("source"): return
    sp = base / z["source"]
    if not sp.exists(): ERR.append(f"paper {z['id']}: source file {z['source']} not found"); return
    src = Source(sp)
    sols = {}
    for p in (z.get("solution_source") or []) if isinstance(z.get("solution_source"), list) else [z.get("solution_source")] if z.get("solution_source") else []:
        if (base / p).exists(): sols[Path(p).stem] = Source(base / p)
        else: ERR.append(f"paper {z['id']}: solution source {p} not found")
    thr = z.get("verbatim_min", threshold)
    covered, mcq_lines = [], set()
    for it in z.get("qs") or []:
        name = it.get("name") or it.get("label") or f"item {it.get('n')}"
        where = f"paper {z['id']} {name}"
        reasons = risk.setdefault((z["id"], it.get("n")), [])
        mine = (IE if IE is not None else {}).setdefault((z["id"], it.get("n")), [])

        class _E:                                      # errors go to the build and to this item's row on the compare sheet
            def append(self, m): ERR.append(m); mine.append(m)
        E = _E()
        text = item_text(it)
        toks = norm(text)
        covered += toks
        score = seg_precision(segments(it), src)
        rows.append((score, z["id"], name))
        if score < thr:
            E.append(f"{where}: only {score:.0%} of its word sequences are in the source -- paraphrased or mis-copied. Copy it verbatim.")
        elif score < 0.995: reasons.append(f"text match {score:.0%}")
        if not it.get("sub") and not it.get("head"):
            for k, o in enumerate(it.get("o") or []):
                if precision(norm(o), src) < thr:
                    E.append(f"{where}: option {'ABCDEFG'[k]} {plain(o)[:60]!r} is not in the source as printed")
        for b in it.get("media") or []:
            if b.get("t") == "table":
                for cell in list(b.get("head") or []) + [c for r in b.get("rows") or [] for c in r]:
                    miss = [w for w in norm(cell) if w not in src.vocab]
                    if miss: E.append(f"{where}: table cell {plain(cell).strip()!r} has words not in the source: {miss}")
        s = it.get("src")
        if s:
            lo, hi = s["lines"]
            span = src.span(lo, hi)
            mcq = has_options(it)
            if mcq: mcq_lines.update(r["no"] for r in span)
            stoks = [t for r in span for t in norm(line_text(r, mcq))]
            for run in uncovered_runs(stoks, toks):
                E.append(f"{where}: missing from the item (source lines {lo}-{hi}): ...{run}...")
            miss, extra = num_diff(" ".join(line_text(r, mcq) for r in span), text)
            if miss or extra:
                E.append(f"{where}: numbers differ from the source -- missing {miss or '[]'}, not in source {extra or '[]'}")
            tabs, figs = src.objects(lo, hi)
            shown = {b.get("_src") for b in (it.get("media") or []) + (it.get("emedia") or [])}
            drawn = sum(b.get("t") in ("fig", "flow", "chart", "gantt", "cpm") for b in it.get("media") or [])
            for t in sorted(tabs - shown):
                E.append(f"{where}: source table {t} (lines {lo}-{hi}) is not shown with the question")
            if len(figs - shown) > drawn - len(figs & shown):
                E.append(f"{where}: source figure(s) {sorted(figs - shown)} not shown with the question")
            if tabs: reasons.append("table")
            if figs: reasons.append("figure")
        elif not it.get("head"):
            reasons.append("hand-made item (no source span)")
        if it.get("o") is not None and not it.get("sub"): reasons.append("MCQ")
        # official solution
        if it.get("e_official"):
            otext = official_text(it)
            if not (it.get("e_src") or {}).get("source") or (it.get("e_src") or {}).get("source") == z["id"]:
                covered += norm(otext)
            otoks = norm(otext)
            es = it.get("e_src") or {}
            ssrc = sols.get(es.get("source")) or (src if es.get("source") in (None, z["id"]) else None)
            if ssrc is None and sols: ssrc = next(iter(sols.values()))
            if ssrc is None:
                E.append(f"{where}: e_official given but no solution_source to check it against"); continue
            sc = seg_precision([it.get("e_official", "")] + [" ".join(map(str, b.get("head") or [])) for b in it.get("emedia") or [] if b.get("_src")]
                               + [" ".join(map(str, r)) for b in it.get("emedia") or [] if b.get("_src") for r in b.get("rows") or []], ssrc)
            if sc < thr: E.append(f"{where}: official solution only {sc:.0%} matches {ssrc.path.name} -- copy it as given")
            if es.get("lines"):
                lo, hi = es["lines"]
                span = [r for r in ssrc.span(lo, hi) if not SOL_NOTE.search(r["text"])]
                first = norm(span[0]["text"])[:1] if span else []
                stoks = [t for r in span for t in norm(r["text"])]
                if first and stoks[:1] == first and first[0].isdigit(): stoks = stoks[1:]      # printed question number
                for run in uncovered_runs(stoks, otoks):
                    E.append(f"{where}: missing from e_official ({ssrc.path.name} lines {lo}-{hi}): ...{run}...")
                stext = " ".join(r["text"] for r in span)
                miss, extra = num_diff(stext, otext)
                if first and first[0].isdigit() and first[0] in miss: miss.remove(first[0])
                if miss or extra:
                    E.append(f"{where}: official-solution numbers differ from {ssrc.path.name} -- missing {miss or '[]'}, not in source {extra or '[]'}")
    # paper-level recall: every source word is in some item (catches whole questions or lines left out)
    union = set(grams(covered))
    stoks = [t for r in src.content if not SOL_NOTE.search(r["text"]) for t in norm(line_text(r, r["no"] in mcq_lines))]
    for run in _paper_runs(stoks, union):
        ERR.append(f"paper {z['id']}: source text not in any item: ...{run}...")
    return src, sols


def _paper_runs(stoks, union):
    cov = [False] * len(stoks)
    for i in range(len(stoks) - 2):
        if tuple(stoks[i:i + 3]) in union: cov[i] = cov[i + 1] = cov[i + 2] = True
    out, cur = [], []
    for i, c in enumerate(cov + [True]):
        if not c: cur.append(i)
        elif cur:
            if len(cur) >= 3: out.append(" ".join(stoks[max(0, cur[0] - 2):cur[-1] + 3]))
            cur = []
    return out


def ocr_changes(src_path):
    """Line numbers Claude changed in a scanned source (vs the untouched .ocr.txt)."""
    p = Path(src_path); o = p.with_suffix(".ocr.txt")
    if not o.exists(): return set()
    a, b = p.read_text(encoding="utf-8").split("\n"), o.read_text(encoding="utf-8").split("\n")
    import difflib
    changed = set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if tag != "equal": changed.update(range(j1 + 1, j2 + 1))
    return changed
