#!/usr/bin/env python3
"""scaffold.py -- turn source files into past-paper items, verbatim by construction.

    python3 scaffold.py [--desk DIR] [--paper ID ...] [--force]

Reads work/inventory.json and sources/*.txt (from ingest.py) and writes data/papers/<id>.json:
every printed question as one item with its text, tables and figures copied from the source,
headings and instructions as head items, MCQ options split out, and official solutions from the
paired solution sheet copied into e_official (MCQ keys also set the answer index).

What Claude still writes: e_explain, calc blocks, answers with no key, names/labels that need a
human touch -- and removes each "_check" after confirming the guess. To fix the *structure*, add a
directive line to the source and re-run (Claude's fields are kept):

    [[item]]     the next line starts a new question
    [[head]]     the next line starts a heading/instructions block (no answer box)
    [[nobreak]]  the next line is NOT a new question even though it looks numbered
    [[end]]      close the current item; following lines up to the next question become a head
    [[item 7]]   start question 7 (papers that print no "7." -- Moodle attempt reviews; ingest writes these)
    [[part]]     the next line starts a sub-part of the current question with its own answer
    [[part head]] the next line starts a passage between sub-parts (no answer box)
    [[options]] ... [[/options]]  the printed answer choices, one per line (ingest writes these at radio buttons)
    [[blank]]    an answer box (fill-in / numeric entry)
    [[key]] ...  a printed answer key ("The correct answer is: ...") -> e_official of that question/part

Answer slots ([[options]] blocks and [[blank]]s) decide the item type: one options block -> MCQ (unlettered options
are fine); one or more blanks -> written/numeric; two or more slots -> the question is split into "parts", one per
answer, each with its own label, text, options or blanks and key (the stem before the first labelled sub-part stays
on the item). An automatic split is marked _check for Claude to confirm.
"""
import argparse, html, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdcommon import norm, read_source, short_lines  # noqa: E402

QSTART = re.compile(r"^(?:\*\*)?\s*(?:(?:Q|Question|Problem|Exercise|Case)\s*\.?\s*)?(\d{1,2})\s*[.):]\s*(?=\S)", re.I)
QWORD = re.compile(r"^(?:\*\*)?\s*(Question|Problem|Exercise|Case|Q)\b", re.I)
SECTION = re.compile(r"^(?:\*\*)?\s*(Section|Part|Module)\s+([A-Z0-9IVX]+)\b|^(?:\*\*)?\s*(I|II|III|IV|V|VI|VII|VIII|IX|X)\s*[.)]\s", re.I)
OPT = re.compile(r"^\(?([A-Ea-e])\s*[.)]\s*(.+)$")
INLINE_OPTS = re.compile(r"\(([A-E])\)\s*")
SUBPART = re.compile(r"^(\(?[a-hivx]{1,4}\)|[a-h][.)]|[ivx]{1,4}[.)]|[•\-–▪])\s")
MARKS = re.compile(r"[\[(]\s*(\d+(?:\.\d+)?)\s*marks?\s*[\])]", re.I)
ANS_HINT = re.compile(r"^(Ans(?:wer)?\s*[:.-])", re.I)
SOL_START = re.compile(r"^(?:\*\*)?\s*(Solution|Suggested answer|Model answer)\s*[:.-]", re.I)
SOL_NOTE = re.compile(r"other questions|remaining questions|straightforward|left (?:as an? )?exercise|not (?:covered|provided)", re.I)
KEY_ONLY = re.compile(r"^\(?([A-E])\)?\.?$|^\(([A-E])\)(?=\s)")     # "(B)", "B.", or "(B) L = 6 x 25/60 = 2.5"


def esc(s):
    """Source text -> inline HTML: escape, then **bold** and *italic*."""
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    return s


SHORT = set()          # source line numbers that end short of the margin (filled per source in main)


def join_lines(lines, head=False):
    """Printed lines -> HTML, joining wrapped lines and keeping real breaks (sub-parts, bullets, lines that end short).
    lines: strings, or (text, line_no) pairs when page geometry is known."""
    if not lines: return ""
    nos = [l[1] if isinstance(l, tuple) else None for l in lines]
    lines = [l[0] if isinstance(l, tuple) else l for l in lines]
    longest = max(len(l) for l in lines)
    out = esc(lines[0].strip())
    for i, (prev, cur) in enumerate(zip(lines, lines[1:])):
        c = cur.strip()
        short = (nos[i] in SHORT) if nos[i] is not None and SHORT else len(prev.rstrip()) < 0.72 * longest
        hard = ((head and i == 0) or SUBPART.match(c) or OPT.match(c) or ANS_HINT.match(c) or c.startswith("**") or prev.rstrip().endswith((":", "**"))
                or short)
        out += ("<br>" if hard else " ") + esc(c)
    out = re.sub(r"(?:^|(?<=<br>))(Ans(?:wer)?\s*[:.-].*?)(?=<br>|$)", r"<b class='ans'>\1</b>", out)
    return out


# ---------------------------------------------------------------- split a source into blocks
def blocks(recs):
    """Yield ('line', rec) | ('table', id, rows, recs) | ('figure', fig, rec) | ('dir', name, rec) | ('key', rec) in order."""
    i = 0
    while i < len(recs):
        r = recs[i]
        if r["kind"] == "directive" and r["table"] and r["text"].startswith("[[table"):
            rows, j = [], i + 1
            while j < len(recs) and recs[j]["kind"] == "table_row":
                rows.append(recs[j]); j += 1
            yield ("table", r["table"], [x["text"].split("\t") for x in rows], [r] + rows + ([recs[j]] if j < len(recs) else []))
            i = j + 1; continue
        if r["kind"] == "figure": yield ("figure", r["fig"], r)
        elif r["kind"] == "directive": yield ("dir", r["text"].strip().strip("[]"), r)
        elif r["kind"] == "key": yield ("key", r)
        elif r["kind"] == "text": yield ("line", r)
        i += 1

def segment(recs, solution=False):
    """Group a source into raw items: {kind: head|q, num, parts: [...], lines: [nos], pages: {..}}.
    parts are pieces in printed order: ('line', text, no) ('table', rows, id) ('figure', fig) ('opts', [(text, no)])
    ('blank', no) ('partbreak', is_head) ('key', text, no)."""
    items, cur, expect, force, nobreak, num_forced = [], None, None, None, False, None
    new_opts = True

    def new(kind, num=None):
        it = {"kind": kind, "num": num, "parts": [], "lines": [], "pages": set()}
        items.append(it); return it

    for b in blocks(recs):
        if b[0] == "dir":
            name, r = b[1], b[2]
            m = re.match(r"item (\d+)$", name)
            if m: force, num_forced = "q", int(m.group(1))
            elif name == "item": force = "q"
            elif name == "head": force = "head"
            elif name == "nobreak": nobreak = True
            elif name == "end": cur = None; force = "head"
            elif name == "options": new_opts = True
            elif name == "/options": new_opts = True
            elif name in ("part", "part head") and cur is not None:
                cur["parts"].append(("partbreak", name == "part head")); cur["lines"].append(r["no"])
            elif name == "blank" and cur is not None:
                cur["parts"].append(("blank", r["no"])); cur["lines"].append(r["no"]); cur["pages"].add(r["page"])
            continue
        if b[0] == "key":
            r = b[1]
            if cur is None and items: cur = items[-1]
            if cur is None: continue
            cur["parts"].append(("key", r["text"].strip(), r["no"])); cur["lines"].append(r["no"]); cur["pages"].add(r["page"])
            continue
        if b[0] == "line" and b[1].get("opt") and cur is not None and force is None:
            r = b[1]; t = r["text"].strip()
            if new_opts or not cur["parts"] or cur["parts"][-1][0] != "opts":
                cur["parts"].append(("opts", []))
            cur["parts"][-1][1].append((t, r["no"])); new_opts = False
            cur["lines"].append(r["no"]); cur["pages"].add(r["page"])
            continue
        if b[0] == "line":
            r = b[1]; t = r["text"].strip()
            m = QSTART.match(t)
            num = int(m.group(1)) if m else None
            is_q = False
            if force == "q": is_q = True; num = num_forced if num_forced is not None else num
            elif m and not nobreak and expect is not None and num_forced is None:
                if solution: is_q = num > (expect - 1)                            # solution sheets skip numbers
                else: is_q = num == expect or (num == 1 and cur and cur["kind"] == "head")
            elif m and not nobreak and expect is None and num_forced is None:
                is_q = True
            roman_sub = bool(re.match(r"^\(?[ivx]{1,4}\)\s", t)) and cur is not None and cur["kind"] == "q"   # i) after h) is a sub-part
            is_section = not is_q and not nobreak and (force == "head" or (SECTION.match(t) and len(t) < 90 and not roman_sub) or
                                       (t.startswith("**") and t.endswith("**") and len(t) < 90 and (cur is None or cur["kind"] == "q")))
            if num_forced is not None and force != "head" and not is_q: is_section = is_section and force == "head"
            if is_q:
                cur = new("q", num); expect = (num or 0) + 1
                if force == "q" and num_forced is not None: cur["forced"] = True
            elif is_section and not solution:
                if cur is None or cur["kind"] != "head" or cur["parts"]: cur = new("head")
            elif cur is None:
                cur = new("head")
            force, nobreak, num_forced = None, False, None if is_q else num_forced
            new_opts = True
            cur["parts"].append(("line", t, r["no"])); cur["lines"].append(r["no"]); cur["pages"].add(r["page"])
        elif b[0] == "table":
            if cur is None: cur = new("head")
            cur["parts"].append(("table", b[2], b[1])); cur["lines"] += [x["no"] for x in b[3]]
            cur["pages"].update(x["page"] for x in b[3])
        elif b[0] == "figure":
            if cur is None: cur = new("head")
            cur["parts"].append(("figure", b[1])); cur["lines"].append(b[2]["no"]); cur["pages"].add(b[2]["page"])
    return items

def split_options(lines):
    """Trailing (A)..(D) lines -> (stem lines, options, guessed?). Wrapped option text is joined."""
    if lines and len(INLINE_OPTS.findall(lines[-1])) >= 3 and lines[-1].strip().startswith("("):   # "(A) 0.5 (B) 2 (C) 15"
        parts = [p.strip() for p in INLINE_OPTS.split(lines[-1].strip())[1:]]
        return lines[:-1], [parts[i + 1] for i in range(0, len(parts) - 1, 2)], False
    for k in range(len(lines) - 1, 0, -1):
        m = OPT.match(lines[k].strip())
        if m and m.group(1) in "Aa": break
    else:
        return lines, [], False
    opts = []
    for l in lines[k:]:
        m = OPT.match(l.strip())
        if m and m.group(1).upper() == "ABCDE"[len(opts)] if len(opts) < 5 else False:
            opts.append([m.group(1), m.group(2).strip()])
        elif opts and not QSTART.match(l.strip()):
            opts[-1][1] += " " + l.strip()                  # wrapped option text
        else:
            return lines, [], False
    if len(opts) < 2: return lines, [], False
    if any(MARKS.search(t) for _, t in opts) or any(len(t) > 160 for _, t in opts): return lines, [], False
    return lines[:k], [t for _, t in opts], not all(l.isupper() for l, _ in opts)


def raw_key(raw):
    first = next((p[1] for p in raw["parts"] if p[0] == "line"), "")
    return " ".join(norm(first)[:12])


LABEL = re.compile(r"^\(?([a-z]|[ivx]{1,4})\)\s")
KEY_TEXT = re.compile(r"^The correct answers? (?:is|are)\s*:\s*", re.I)


def media_of(pieces, crop_ok, checks, head=False):
    """line/table/figure pieces -> (q html, media blocks): text before the first table/figure is q."""
    q_lines, media, buf, seen_block = [], [], [], False
    for p in pieces:
        if p[0] == "line":
            (buf if seen_block else q_lines).append((p[1], p[2]))
        elif p[0] in ("table", "figure"):
            if buf: media.append({"t": "p", "x": join_lines(buf)}); buf = []
            seen_block = True
            if p[0] == "table":
                rows = [[esc(c) for c in r] for r in p[1]]
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                media.append({"t": "table", "head": rows[0], "rows": rows[1:], "_src": p[2]})
            else:
                f = p[1]
                if f.get("src") and crop_ok(f["src"]):
                    media.append({"t": "fig", "src": f["src"], "_src": f["id"]})
                else:
                    checks.append(f"figure {f['id']} has no crop: add a fig block (crop with pdftoppm) or a drawn block")
    if buf: media.append({"t": "p", "x": join_lines(buf)})
    return join_lines(q_lines, head=head), media, q_lines


def apply_key(target, keys):
    """A printed key ("The correct answer is: X") becomes e_official; for options it also sets the answer index."""
    if not keys: return
    target["e_official"] = "<br>".join(esc(k) for k in keys)
    if target.get("o"):
        flat = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip().lower().replace("\u2212", "-")
        val = flat(KEY_TEXT.sub("", keys[0]))
        hits = [i for i, o in enumerate(target["o"]) if flat(o) == val]
        if not hits: hits = [i for i, o in enumerate(target["o"]) if norm(o) == norm(val)]
        if len(hits) == 1: target["a"] = hits[0]


def is_slot(p): return p[0] in ("opts", "blank")


def build_part(pieces, crop_ok, checks):
    head = any(p[0] == "partbreak" and p[1] for p in pieces[:1])
    body = [p for p in pieces if p[0] != "partbreak"]
    part = {}
    q, media, q_lines = media_of(body, crop_ok, checks, head=head)
    first = next((p for p in body if p[0] == "line"), None)
    part["_k"] = " ".join(norm(first[1])[:12]) if first else ""
    if head:
        part["head"] = True
    else:
        m = LABEL.match(first[1]) if first else None
        part["label"] = m.group(0).strip() if m else ""
    part["q"] = q
    if media: part["media"] = media
    opts = [p for p in body if p[0] == "opts"]
    blanks = sum(1 for p in body if p[0] == "blank")
    if opts and not head:
        part["o"] = [esc(t) for t, _ in opts[0][1]]; part["a"] = None
        if len(opts) > 1: part["o"] += [esc(t) for o in opts[1:] for t, _ in o[1]]     # an options list split by a page break
    elif not head:
        part["sub"] = True
        if blanks: part["blanks"] = blanks
    apply_key(part, [p[1] for p in body if p[0] == "key"])
    nos = [p[2] for p in body if p[0] in ("line", "key")] + [n for o in opts for _, n in o[1]] + [p[1] for p in body if p[0] == "blank"]
    if nos: part["src"] = {"lines": [min(nos), max(nos)]}
    return part


def split_parts(pieces):
    """Split an item's pieces at its answer slots. Returns (stem pieces, [part pieces], automatic?)."""
    if any(p[0] == "partbreak" for p in pieces):
        groups, cur = [[]], None
        for p in pieces:
            if p[0] == "partbreak": groups.append([p])
            else: groups[-1].append(p)
        return groups[0], groups[1:], False
    # automatic: one part per answer slot (adjacent slots, e.g. two boxes on one line, share a part)
    first_label = next((i for i, p in enumerate(pieces) if p[0] == "line" and LABEL.match(p[1])), None)
    first_slot = next(i for i, p in enumerate(pieces) if is_slot(p))
    cut = first_label if first_label is not None and first_label < first_slot else 0
    stem, rest = pieces[:cut], pieces[cut:]
    parts, cur, closed = [], [], False
    for p in rest:
        if closed and p[0] in ("line", "table", "figure"):
            parts.append(cur); cur, closed = [], False
        cur.append(p)
        if is_slot(p): closed = True
    if cur:
        if closed or not parts: parts.append(cur)
        else: parts[-1] += cur                      # trailing note after the last answer box
    out = []
    for g in parts:                                 # text between sub-parts (a new case, a second table) -> a head part
        lab = next((i for i, p in enumerate(g) if p[0] == "line" and LABEL.match(p[1])), None)
        if lab and any(p[0] in ("line", "table", "figure") for p in g[:lab]) and out:
            out.append([("partbreak", True)] + g[:lab]); out.append(g[lab:])
        else: out.append(g)
    return stem, out, True


def build_item(raw, n, crop_ok, as_sub=False):
    pieces = raw["parts"]
    it = {"n": n, "_k": raw_key(raw)}
    checks = []
    if raw["kind"] == "head":
        it["head"] = True
    slots = [p for p in pieces if is_slot(p)]
    nslots = sum(1 for i, p in enumerate(pieces) if is_slot(p) and not (i and is_slot(pieces[i - 1])))
    keys = [p[1] for p in pieces if p[0] == "key"]
    if raw["kind"] == "q" and (nslots >= 2 or any(p[0] == "partbreak" for p in pieces)):
        stem, groups, auto = split_parts(pieces)
        q, media, q_lines = media_of(stem, crop_ok, checks)
        it["q"] = q
        if media: it["media"] = media
        it["parts"] = [build_part(g, crop_ok, checks) for g in groups]
        if auto: checks.append(f"split into {len(it['parts'])} parts at its answer boxes/option lists: confirm the cuts "
                               "(add [[part]] / [[part head]] lines to the source to move them)")
    elif raw["kind"] == "q" and slots and slots[0][0] == "opts":
        body = [p for p in pieces if p[0] not in ("opts", "blank", "key")]
        q, media, q_lines = media_of(body, crop_ok, checks)
        it["q"] = q
        if media: it["media"] = media
        it["o"] = [esc(t) for p in slots if p[0] == "opts" for t, _ in p[1]]; it["a"] = None
        apply_key(it, keys)
    else:
        lines = [p[1] for p in pieces if p[0] == "line"]
        nos = {p[1]: p[2] for p in pieces if p[0] == "line"}
        only_lines = all(p[0] in ("line", "key", "blank") for p in pieces)
        opts, guess = [], False
        body = [p for p in pieces if p[0] not in ("key", "blank")]
        if raw["kind"] == "q" and only_lines and not as_sub and not slots:
            stem_l, opts, guess = split_options(lines)
            if opts: body = [("line", l, nos.get(l)) for l in stem_l]
        q, media, q_lines = media_of(body, crop_ok, checks, head=raw["kind"] == "head")
        it["q"] = q
        if media: it["media"] = media
        if raw["kind"] == "q":
            if opts:
                it["o"] = [esc(o) for o in opts]; it["a"] = None
                if guess: checks.append('lower-case (a)-(d) read as MCQ options; if they are sub-parts, set "sub": true and re-run scaffold')
            else:
                it["sub"] = True
                nb = sum(1 for p in pieces if p[0] == "blank")
                if nb: it["blanks"] = nb
            apply_key(it, keys)
    if raw["kind"] == "q":
        it["label"] = ""
        first = next((p[1] for p in pieces if p[0] == "line"), "")
        word = QWORD.match(first) if first else None
        kind = "Question" if raw.get("forced") else "MCQ" if it.get("o") else (word.group(1).title() if word and word.group(1).lower() != "q" else "Question")
        it["name"] = f"{kind} {raw['num']}" if raw["num"] is not None else kind
        alltext = " ".join(p[1] for p in pieces if p[0] == "line")
        marks = [float(m) for m in MARKS.findall(alltext)]
        if marks: it["marks"] = int(sum(marks)) if float(sum(marks)).is_integer() else sum(marks)
    it["src"] = {"lines": [min(raw["lines"]), max(raw["lines"])], "pages": sorted(raw["pages"])}
    if checks: it["_check"] = checks
    return it

def attach_solutions(items, sol_recs, sol_id):
    """Copy each numbered solution into the matching item's e_official; MCQ keys set 'a'."""
    raw = segment(sol_recs, solution=True)
    by_num = {it["name"].split()[-1]: it for it in items if not it.get("head") and it.get("name")}
    notes, unmatched = [], []
    for s in raw:
        pairs = [(p[1], p[2]) for p in s["parts"] if p[0] == "line"]
        lines = [l for l, _ in pairs]
        keep = [l for l in lines if not SOL_NOTE.search(l)]
        no_of = dict(pairs)
        notes += [l for l in lines if SOL_NOTE.search(l)]
        if s["kind"] != "q":
            unmatched += [l for l in keep if not (l.startswith("**") and l.endswith("**"))]; continue
        it = by_num.get(str(s["num"]))
        if not it: unmatched += keep; continue
        first = QSTART.sub("", keep[0], count=1).strip() if keep else ""
        body = [first] + keep[1:] if first else keep[1:]
        km = KEY_ONLY.match(body[0]) if it.get("o") is not None and body else None
        if km and (len(body) == 1 or km.group(2)):
            it["a"] = "ABCDE".index(km.group(1) or km.group(2))
        parts = [p for p in s["parts"] if p[0] != "line"]
        it["e_official"] = join_lines([(b, no_of.get(k)) for b, k in zip(body, ([keep[0]] if first else []) + keep[1:])])
        if parts:
            it["emedia"] = [{"t": "table", "head": [esc(c) for c in p[1][0]], "rows": [[esc(c) for c in r] for r in p[1][1:]], "_src": p[2]}
                            if p[0] == "table" else {"t": "fig", "src": p[1].get("src"), "_src": p[1]["id"]} for p in parts]
        it["e_src"] = {"source": sol_id, "lines": [min(s["lines"]), max(s["lines"])], "pages": sorted(s["pages"])}
    return notes, unmatched


# every field not produced here is Claude's and survives a re-run
SCAFFOLD_FIELDS = {"n", "_k", "q", "o", "media", "src", "e_official", "emedia", "e_src", "label", "marks", "head", "parts", "blanks"}
PART_FIELDS = {"_k", "q", "o", "media", "src", "e_official", "label", "head", "blanks"}


def merge(new, old):
    """Keep Claude's work from the previous file: every non-scaffold field, matched by the item's first line
    (sub-parts by their own first line)."""
    olds = {o.get("_k"): o for o in old}
    for it in new:
        o = olds.get(it["_k"])
        if not o: continue
        for k, v in o.items():
            if k not in SCAFFOLD_FIELDS and not (k == "a" and it.get("a") is not None): it[k] = v
        if it.get("o") and o.get("sub") and not o.get("o"): it.pop("sub", None)     # a written item that now has its options
        if "_check" not in o: it.pop("_check", None)
        oldp = {p.get("_k"): p for p in o.get("parts") or []}
        for p in it.get("parts") or []:
            op = oldp.get(p["_k"])
            if not op: continue
            for k, v in op.items():
                if k not in PART_FIELDS and not (k == "a" and p.get("a") is not None): p[k] = v
            if op.get("num") is not None: p.pop("sub", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desk", default=".")
    ap.add_argument("--paper", action="append", help="paper id(s); default: every file ingest marked as a paper")
    ap.add_argument("--force", action="store_true", help="discard Claude's fields instead of merging them")
    a = ap.parse_args()
    desk = Path(a.desk).resolve()
    inv = json.loads((desk / "work" / "inventory.json").read_text())
    files = {f["id"]: f for f in inv["files"]}
    want = a.paper or [f["id"] for f in inv["files"] if f["role"] == "paper"]
    out_dir = desk / "data" / "papers"; out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for pid in want:
        f = files[pid]
        recs = read_source(desk / f["source"])
        SHORT.clear(); SHORT.update(short_lines(recs, f))
        raw = segment(recs)
        path = out_dir / f"{pid}.json"
        old = json.loads(path.read_text()) if path.exists() and not a.force else {}
        as_sub = {o.get("_k") for o in old.get("qs", []) if o.get("sub")}
        items = [build_item(r, i + 1, lambda p: (desk / p).exists(), raw_key(r) in as_sub) for i, r in enumerate(raw)]
        title = next((re.sub(r"\*+", "", p[1]) for r in raw for p in r["parts"] if p[0] == "line"), pid)
        modes = {pg["mode"] for pg in f["pages"]}
        paper = {"id": pid, "title": title, "meta": "", "note": "", "source": f["source"], "qs": items}
        for pg in f["pages"]:
            for fl in pg["flags"]:
                if "two-column" in fl:
                    for it in items:
                        if pg["n"] in it["src"]["pages"]: it.setdefault("_check", []).append(f"page {pg['n']} is two-column: confirm reading order")
        notes, unmatched, sol_bits = [], [], []
        for sid in f.get("solutions") or []:
            sf = files[sid]
            srecs = read_source(desk / sf["source"])
            SHORT.clear(); SHORT.update(short_lines(srecs, sf))
            n_, u_ = attach_solutions(items, srecs, sid)
            notes += n_; unmatched += u_
            sm = {pg["mode"] for pg in sf["pages"]}
            sol_bits.append(f"{sid} ({'/'.join(sorted(sm))})")
            paper.setdefault("solution_source", []).append(sf["source"])
        qs = [it for it in items if not it.get("head")]
        missing = [it["name"] for it in qs if "e_official" not in it and not (it.get("parts") and
                   all(p.get("e_official") for p in it["parts"] if not p.get("head")))]
        paper["note"] = " ".join(filter(None, [
            f"Official solutions digitized from {', '.join(sol_bits)}." if sol_bits else
            ("Answer keys are the ones printed on the paper." if any(it.get("e_official") or any(p.get("e_official") for p in it.get("parts") or []) for it in qs)
             else "No official solutions were supplied."),
            f"Not covered by the official solutions: {', '.join(missing)}." if sol_bits and missing else "",
            ("Solution sheet says: " + " ".join(notes)) if notes else "",
            "Scanned paper: text is OCR checked against the page images." if modes & {"scanned", "handwriting"} else ""]))
        if unmatched:
            paper["_check"] = [f"solution lines not matched to a question: {' | '.join(unmatched)[:300]}"]
        if old:
            merge(items, old.get("qs", []))
            for k in ("title", "meta", "note"):
                if old.get(k): paper[k] = old[k]
            if "_check" not in old: paper.pop("_check", None)
        path.write_text(json.dumps(paper, indent=1, ensure_ascii=False))
        mcq = [it for it in qs if "o" in it]
        need_a = [it["name"] for it in mcq if it.get("a") is None]
        need_a += [f"{it['name']} {p.get('label') or '#' + str(k + 1)}" for it in qs for k, p in enumerate(it.get("parts") or [])
                   if p.get("o") and p.get("a") is None]
        nparts = sum(len(it.get("parts") or []) for it in qs)
        pmcq = sum(1 for it in qs for p in it.get("parts") or [] if p.get("o"))
        checks = [(it.get("name") or f"head {it['n']}", c) for it in items for c in it.get("_check", [])] + \
                 [("paper", c) for c in paper.get("_check", [])]
        report.append(f"{pid}: {len(items)} items ({len(items) - len(qs)} head, {len(mcq)} MCQ, {len(qs) - len(mcq)} other)"
                      + (f"; {nparts} sub-parts ({pmcq} MCQ)" if nparts else "") + f" -> {path.relative_to(desk)}")
        report.append(f"   official solutions: {len(qs) - len(missing)}/{len(qs)}" + (f"; none for {', '.join(missing)}" if missing else ""))
        if need_a: report.append(f"   answer needed (no key): {', '.join(need_a)}")
        for who, c in checks: report.append(f"   _check {who}: {c}")
    print("SCAFFOLD\n" + "\n".join(report))


if __name__ == "__main__":
    main()
