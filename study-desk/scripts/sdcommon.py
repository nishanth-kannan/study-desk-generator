"""Shared helpers for the study-desk tools: source-file format, tokenising, data loading.

Source file format (sources/<id>.txt) -- the verbatim master copy every check compares against:

    #! study-desk source v1 id=setA role=paper file=setA.pdf     (first line: provenance)
    === page 2 ===                       page marker
    #~ PGP-I · Operations                ignored line (repeated header/footer, page number)
    **Section I · Problems**             bold (whole line or a run); *x* is italic
    [[table t1]] ... [[/table]]          a table: one row per line, cells separated by TABs
    [[figure f1 view/setA-p2-f1.png]]    a figure that sits at this point on the page
    [[item]]  [[head]]  [[nobreak]]      scaffold directives Claude may insert (see scaffold.py)
    [[part]]  [[part head]]              the next line starts a sub-part of the current question (its own answer),
                                         or a passage between sub-parts (case text, a second table) with no answer
    [[options]] ... [[/options]]         the printed answer choices, one option per line, in printed order;
                                         ingest writes these where it sees radio buttons / check boxes
    [[blank]]                            an answer box (fill-in / numeric entry) at this point
    [[item 7]]                           start question 7 (for papers that print no "7." -- e.g. Moodle reviews)
    [[key]] The correct answer is: …     an answer key printed with the paper (attempt reviews); not question text

Everything else is text, one printed line per line.
"""
import html, json, re, unicodedata
from pathlib import Path

PAGE_RE = re.compile(r"^=== page (\d+) ===\s*$")
DIRECTIVE_RE = re.compile(r"^\[\[(item(?: \d+)?|head|nobreak|end|part|part head|options|/options|blank)\]\]\s*$")
KEY_LINE_RE = re.compile(r"^\[\[key\]\]\s*(.*)$")
TABLE_OPEN_RE = re.compile(r"^\[\[table (\S+)\]\]\s*$")
FIG_RE = re.compile(r"^\[\[figure (\S+)(?: (\S+))?\]\]\s*$")
TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+")


# ---------------------------------------------------------------- tokens
def plain(x):
    """HTML or source markup -> plain text."""
    x = re.sub(r"</?[A-Za-z!][^>]*>", " ", str(x))   # local patch: a bare "<=" in source text is not a tag
    return html.unescape(x).replace("**", " ").replace("\t", " ")


def norm(text):
    """Lower-case word and number tokens. Keeps one-letter tokens and decimals; 12,000 -> 12000."""
    t = unicodedata.normalize("NFKC", plain(text)).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    out = []
    for m in TOKEN_RE.findall(t):
        if m[0].isdigit():
            m = re.sub(r"(?<=\d),(?=\d{3}\b)", "", m)     # thousands separators
        out.append(m)
    return out


def numbers(text):
    return [t for t in norm(text) if t[0].isdigit()]


def grams(tokens, n=3):
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# ---------------------------------------------------------------- source files
def read_source(path):
    """Parse a source file into line records: {no, text, kind, page, table, fig}.
    kind: text | ignored | page | directive | table_row | figure | meta | blank | key
    Text lines inside an [[options]] block carry opt=True (one printed answer choice per line)."""
    recs, page, table, opts = [], 1, None, False
    for no, raw in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        s = raw.rstrip()
        r = {"no": no, "text": s, "page": page, "table": None, "fig": None, "opt": False}
        if no == 1 and s.startswith("#!"): r["kind"] = "meta"
        elif PAGE_RE.match(s): page = int(PAGE_RE.match(s).group(1)); r["page"] = page; r["kind"] = "page"
        elif s.startswith("#~"): r["kind"] = "ignored"
        elif TABLE_OPEN_RE.match(s): table = TABLE_OPEN_RE.match(s).group(1); r["kind"] = "directive"; r["table"] = table
        elif s == "[[/table]]": r["kind"] = "directive"; r["table"] = table; table = None
        elif table: r["kind"] = "table_row"; r["table"] = table
        elif FIG_RE.match(s): m = FIG_RE.match(s); r["kind"] = "figure"; r["fig"] = {"id": m.group(1), "src": m.group(2)}
        elif KEY_LINE_RE.match(s): r["kind"] = "key"; r["text"] = KEY_LINE_RE.match(s).group(1)
        elif DIRECTIVE_RE.match(s):
            r["kind"] = "directive"
            if s.startswith("[[options]]"): opts = True
            elif s.startswith("[[/options]]") or s.startswith("[[item]]") or s.startswith("[[part"): opts = False
        elif not s.strip(): r["kind"] = "blank"
        else: r["kind"] = "text"; r["opt"] = opts
        recs.append(r)
    return recs


def source_meta(path):
    first = Path(path).read_text(encoding="utf-8").split("\n", 1)[0]
    return dict(re.findall(r"(\w+)=(\S+)", first)) if first.startswith("#!") else {}


def content_lines(recs):
    """Lines whose words belong to the paper (text and table rows)."""
    return [r for r in recs if r["kind"] in ("text", "table_row")]


# ---------------------------------------------------------------- data loading (one file or a folder of small files)
def load_data(path):
    """data.json, or a folder: meta.json (meta, units, guide), papers/*.json (one paper each),
    concepts/*.json (a unit object or a list), bank/*.json (a list of questions)."""
    p = Path(path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8")), p.parent
    d = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    for sub, key in (("papers", "papers"), ("concepts", "concepts"), ("bank", "questions")):
        files = sorted((p / sub).glob("*.json")) if (p / sub).is_dir() else []
        if not files: continue
        d.setdefault(key, [])
        for f in files:
            x = json.loads(f.read_text(encoding="utf-8"))
            d[key] += x if isinstance(x, list) else [x]
    return d, p.resolve().parent          # paths in the data (sources/, work/) are relative to the desk folder


def line_boxes(recs, file_rec):
    """{source line no: bbox} for text lines, from ingest's linemap (k-th text line on each page)."""
    lm, count, out = (file_rec or {}).get("linemap") or {}, {}, {}
    for r in recs:
        if r["kind"] == "text":
            count[r["page"]] = count.get(r["page"], 0) + 1
            b = lm.get(f"{r['page']}:{count[r['page']]}")
            if b: out[r["no"]] = b
    return out


def short_lines(recs, file_rec):
    """Source line numbers that end well before the text's right edge on their page: real line breaks."""
    boxes = line_boxes(recs, file_rec)
    by_page = {}
    for r in recs:
        if r["no"] in boxes: by_page.setdefault(r["page"], []).append(boxes[r["no"]])
    edge = {}
    for pg, bs in by_page.items():
        x1 = sorted(b[2] for b in bs); x0 = min(b[0] for b in bs)
        right = x1[-1] if len(x1) < 10 else x1[int(0.9 * (len(x1) - 1))]     # few lines: the longest one is the margin
        edge[pg] = right - 0.12 * max(1.0, right - x0)
    return {no for no, b in boxes.items() if b[2] < edge[next(r["page"] for r in recs if r["no"] == no)]}
