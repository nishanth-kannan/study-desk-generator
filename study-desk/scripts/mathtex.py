"""LaTeX maths for the desk: delimiter checks at build time, and the KaTeX bundle the desk embeds when it has maths.

Syntax (in any text field: explanations, concept notes, guide, bank questions):
    \\( ... \\)   inline maths        e.g.  \\(P_0 = \\frac{Div_1}{r_E - g}\\)
    \\[ ... \\]   display maths       e.g.  \\[EV_0 = \\sum_{t=1}^{N} \\frac{FCF_t}{(1+r_{WACC})^t}\\]
Dollar signs are NOT maths delimiters (finance text is full of prices); write a literal dollar inside maths as \\$.
Inside maths, write < and > as \\lt and \\gt (or &lt; / &gt;), because the text is HTML.
Multi-letter names read better upright or as one italic word: \\mathit{Div}_1, \\mathrm{WACC}.
Inline fractions inside running text: prefer \\dfrac or a / b; a display block \\[ ... \\] for anything tall.
meta.mathMacros = {"\\Div": "\\mathit{Div}"} defines shorthand for the whole desk.

KaTeX (MIT, vendor/katex) is inlined, fonts included as data URIs, so the desk stays a single offline file.
Desks without maths embed nothing.
"""
import base64, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
KATEX = HERE / "vendor" / "katex"
MATH_RE = re.compile(r"\\\(|\\\[")
ERROR_COLOR = "#c9001b"   # KaTeX paints unknown commands in this colour; verify.py looks for it
_PAIRS = (("\\(", "\\)"), ("\\[", "\\]"))


def _strings(x, path):
    if isinstance(x, str):
        yield path, x
    elif isinstance(x, dict):
        for k, v in x.items():
            if not str(k).startswith("_") and k not in ("src", "e_src"):
                yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _strings(v, f"{path}[{i}]")


def _label(d, path):
    """Readable location: 'concepts[2].blocks[5].x' -> 'concept unit=eff block 6 (x)'."""
    m = re.match(r"(papers|concepts|questions)\[(\d+)\](.*)", path)
    if not m: return path
    kind, i, rest = m.group(1), int(m.group(2)), m.group(3)
    try:
        obj = d[kind][i]
    except (KeyError, IndexError, TypeError):
        return path
    who = {"papers": f"paper {obj.get('id')}", "concepts": f"concept unit={obj.get('unit')}",
           "questions": f"question id={obj.get('id')}"}[kind]
    q = re.match(r"\.qs\[(\d+)\](.*)", rest)
    if kind == "papers" and q:
        it = (obj.get("qs") or [])[int(q.group(1))]
        who += f" {it.get('name') or it.get('n')}"; rest = q.group(2)
    return f"{who}{rest}"


def check(d, errors, warnings):
    """Unbalanced or nested delimiters are errors; $-delimited LaTeX is a warning. Returns True if the desk uses maths."""
    used = False
    for path, s in _strings(d, ""):
        if not MATH_RE.search(s) and "\\)" not in s and "\\]" not in s:
            if re.search(r"\$[^$\s][^$]*\\[a-zA-Z]+[^$]*\$", s):
                warnings.append(f"{_label(d, path)}: looks like $...$ LaTeX -- dollars are not maths delimiters here; use \\( ... \\)")
            continue
        used = True
        depth = None
        for tok in re.finditer(r"\\[\(\)\[\]]", s):
            t = tok.group(0)
            if t in ("\\(", "\\["):
                if depth: errors.append(f"{_label(d, path)}: maths opened with {t} inside {depth} ... (nested delimiters)"); break
                depth = t
            else:
                want = dict(_PAIRS).get(depth)
                if t != want:
                    errors.append(f"{_label(d, path)}: {t} without a matching opener" if not depth else
                                  f"{_label(d, path)}: {depth} closed by {t}")
                    break
                depth = None
        else:
            if depth: errors.append(f"{_label(d, path)}: {depth} never closed")
    return used


def check_macros(meta, errors):
    mac = meta.get("mathMacros")
    if mac is None: return {}
    if not isinstance(mac, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mac.items()):
        errors.append("meta.mathMacros must map names to strings, e.g. {\"\\\\Div\": \"\\\\mathit{Div}\"} (JSON escapes each backslash)"); return {}
    bad = [k for k in mac if not re.fullmatch(r"\\[A-Za-z]+", k)]
    if bad: errors.append(f"meta.mathMacros: names must look like \\name, not {bad[:3]}")
    return mac


def assets(macros=None):
    """<style> + <script> block for the template's <!--__MATH__--> slot."""
    css = (KATEX / "katex.min.css").read_text(encoding="utf-8")

    def font(m):
        f = KATEX / "fonts" / f"{m.group(1)}.woff2"
        b64 = base64.b64encode(f.read_bytes()).decode()
        return f'src:url(data:font/woff2;base64,{b64}) format("woff2")'
    css = re.sub(r'src:url\(fonts/([\w-]+)\.woff2\) format\("woff2"\)(?:,url\([^)]*\) format\("[a-z]+"\))*', font, css)
    if "url(fonts/" in css:
        raise RuntimeError("KaTeX css still references external fonts -- vendor/katex is out of step with mathtex.py")
    css += (".katex{font-size:1.06em}.katex-display{margin:.5em 0;overflow-x:auto;overflow-y:hidden;padding:2px 0}"
            ".fx .katex{font-size:1.14em}.fx .katex-display{margin:.2em 0}.katex-error{color:var(--bad,#b3261e)!important}")
    js = (KATEX / "katex.min.js").read_text(encoding="utf-8")
    ar = (KATEX / "auto-render.min.js").read_text(encoding="utf-8")
    boot = r"""
(function(){
  if(!window.renderMathInElement) return;
  var SKIP="script,style,textarea,pre,code,noscript,option,.nomath";
  var OPT={delimiters:[{left:"\\[",right:"\\]",display:true},{left:"\\(",right:"\\)",display:false}],
    throwOnError:false, errorColor:"#c9001b", ignoredTags:["script","noscript","style","textarea","pre","code","option"], ignoredClasses:["nomath"]};
  OPT.macros=__MACROS__;
  var mo=null;
  // typeset, then drop the mutations our own typesetting caused: a formula KaTeX can't parse is put back as raw
  // text, and re-reading that as "new content" would loop forever
  function run(ns){try{ns.forEach(function(n){if(n.isConnected)renderMathInElement(n,OPT);});}finally{if(mo)mo.takeRecords();}}
  function want(e){return e&&e.nodeType===1&&!e.closest(SKIP)&&!e.closest(".katex")&&/\\[\(\[]/.test(e.textContent||"");}
  run([document.body]);                       // the desk has rendered by now: this script sits after it
  mo=new MutationObserver(function(ms){       // then typeset whatever the desk adds later (reveals, tab switches)
    var ns=[];
    ms.forEach(function(m){m.addedNodes.forEach(function(n){
      var e=n.nodeType===1?n:n.parentNode;
      if(want(e)&&ns.indexOf(e)<0) ns.push(e);
    });});
    if(ns.length) run(ns);
  });
  mo.observe(document.body,{childList:true,subtree:true});
})();
"""
    import json
    boot = boot.replace("__MACROS__", json.dumps(macros or {}))
    safe = lambda s: s.replace("</script", "<\\/script")
    return (f"<style>{css}</style>\n<script>{safe(js)}</script>\n<script>{safe(ar)}</script>\n<script>{safe(boot)}</script>")
