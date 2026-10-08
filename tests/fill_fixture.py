#!/usr/bin/env python3
"""Play Claude's part on the fixture desk: the edits Claude would make after reading summary.txt.

    python3 tests/fill_fixture.py <desk>      (run after ingest; it re-runs scaffold itself)

1. corrects the OCR mistakes it would see on the review strip (small line edits in sources/)
2. re-runs scaffold (Claude's fields survive re-runs)
3. writes e_explain, calc blocks, meta, one concept unit and a small bank (one ref question)
"""
import json, subprocess, sys
from pathlib import Path

desk = Path(sys.argv[1]).resolve()
tools = Path(__file__).resolve().parents[1] / "study-desk" / "scripts"

# 1. OCR corrections, as Claude would make them from the strip (only lines whose OCR differs from the page)
src = desk / "sources" / "setb_solutions_scan.txt"
lines = src.read_text().split("\n")
fixed = ["1. (a) EOQ = sqrt(2 x 12000 x 450 / 30) = 600 units", "(b) Orders per year = 12000 / 600 = 20",
         "Ordering cost = 20 x 450 = Rs 9000", "2. (B) L = 6 x 25/60 = 2.5"]
k = 0
for i, l in enumerate(lines):
    if l and not l.startswith(("#", "===", "[[")):
        if k < len(fixed): lines[i] = fixed[k]; k += 1
src.write_text("\n".join(lines))

# 2. scaffold again
subprocess.run([sys.executable, str(tools / "scaffold.py"), "--desk", str(desk)], check=True, capture_output=True)

# 3. Claude's fields
def edit(pid, fn):
    p = desk / "data" / "papers" / f"{pid}.json"
    d = json.loads(p.read_text()); fn(d); p.write_text(json.dumps(d, indent=1, ensure_ascii=False))

def item(d, name):
    return next(it for it in d["qs"] if it.get("name") == name)

def fill_a(d):
    d["meta"] = "Synthetic fixture · typed paper with a typed solution sheet"
    q1 = item(d, "Question 1")
    q1["e_explain"] = ("Capacity of a stage = units × batch size ÷ time per batch. Proofing is slowest at 160 loaves/hour, so it is the "
                       "bottleneck and sets the line's capacity. Utilisation of baking = demand ÷ baking capacity. Adding a fourth proofer "
                       "lifts proofing to 213.3, so mixing (200) becomes the bottleneck: capacity rises, but only to 200.")
    q1["calc"] = {"rows": [["Batch size (loaves)", 40], ["Mixing time (min)", 12], ["Mixing units", 1], ["Proofing time (min)", 45],
                           ["Proofing units", 3], ["Baking time (min)", 20], ["Baking units", 2],
                           ["Mixing capacity (loaves/h)", "=B3*B1/B2*60"], ["Proofing capacity (loaves/h)", "=B5*B1/B4*60"],
                           ["Baking capacity (loaves/h)", "=B7*B1/B6*60"], ["Line capacity (loaves/h)", "=MIN(B8:B10)"],
                           ["Baking utilisation at 100/h", "=100/B10"], ["Capacity with 4 proofers", "=MIN(B8,4*B1/B4*60,B10)"]],
                  "answer": ["Line capacity (loaves/h)", "Baking utilisation at 100/h", "Capacity with 4 proofers"]}
    q2 = item(d, "Question 2")
    q2["e_explain"] = ("(a) The queue is the vertical gap between the cumulative arrival and departure curves; it is widest at 10:00, "
                       "where 70 have arrived and 40 have left, so 30 are waiting. (b) Little's Law: W = L ÷ λ = 18 ÷ 50 per hour.")
    q2["calc"] = {"rows": [["Average queue L", 18], ["Arrival rate λ (per hour)", 50], ["Waiting time W (hours)", "=B1/B2"],
                           ["Waiting time W (minutes)", "=B3*60"]], "answer": ["Waiting time W (minutes)"], "expect": [21.6]}
    item(d, "MCQ 3")["e_explain"] = "Only time saved at the bottleneck adds capacity; buffers and faster non-bottlenecks do not."
    m4 = item(d, "MCQ 4")
    m4["e_explain"] = "Little's Law: flow time = inventory ÷ flow rate = 15 ÷ 30 = 0.5 hours. 2 hours inverts the ratio."
    m4["calc"] = {"rows": [["Inventory", 15], ["Flow rate (per hour)", 30], ["Flow time (hours)", "=B1/B2"]]}
    item(d, "MCQ 5")["e_explain"] = "Little's Law needs only a stable system and long-run averages; it makes no assumption about arrival distributions."

def fill_b(d):
    q1 = item(d, "Question 1")
    q1["e_explain"] = "EOQ = √(2DS/H). Orders per year = D ÷ EOQ; ordering cost = orders × S."
    q1["calc"] = {"rows": [["Annual demand D", 12000], ["Ordering cost S (Rs)", 450], ["Holding cost H (Rs/unit/year)", 30],
                           ["EOQ", "=SQRT(2*B1*B2/B3)"], ["Orders per year", "=B1/B4"], ["Annual ordering cost (Rs)", "=B5*B2"]],
                  "answer": ["EOQ", "Orders per year", "Annual ordering cost (Rs)"]}
    m2 = item(d, "MCQ 2")
    m2["e_explain"] = "Little's Law: L = λW = 6 per hour × 25/60 hour = 2.5. 150 forgets to convert minutes to hours."
    m2["calc"] = {"rows": [["Arrival rate (per hour)", 6], ["Time in clinic (min)", 25], ["Patients in clinic", "=B1*B2/60"]]}

edit("seta", fill_a)
edit("setb_scan", fill_b)
(desk / "data" / "meta.json").write_text(json.dumps({
    "meta": {"title": "Fixture Ops Study Desk", "subtitle": "Synthetic test desk", "unitLabel": "Unit", "storageKey": "fixture-ops"},
    "units": [{"id": 1, "name": "Capacity and flow"}],
    "guide": {"intro": "Fixture.", "frameworks": [{"h": "Little's Law", "x": "L = λW"}], "traps": [{"h": "Units", "x": "Convert minutes to hours."}], "sources": "Synthetic."}},
    indent=1))
(desk / "data" / "concepts").mkdir(exist_ok=True)
(desk / "data" / "concepts" / "u1.json").write_text(json.dumps({"unit": 1, "title": "Bottlenecks set capacity", "gist": "The slowest stage sets the pace.",
    "blocks": [{"t": "p", "x": "Capacity of a stage is units × batch ÷ time."}, {"t": "box", "title": "In the exam", "x": "Bottleneck + utilisation pairs."}]}))
(desk / "data" / "bank").mkdir(exist_ok=True)
(desk / "data" / "bank" / "u1.json").write_text(json.dumps([
    {"id": 1, "unit": 1, "topic": "Bottleneck", "diff": "M", "q": "Answer part (b) of the problem below: the utilisation of baking.",
     "o": ["41.7%", "62.5%", "50%", "100%"], "a": 0, "ref": {"paper": "seta", "n": 3},
     "e_explain": "Baking capacity is 240/h; 100 ÷ 240 = 41.7%. 62.5% divides by the line capacity (160) instead of the stage's own capacity.",
     "calc": {"rows": [["Demand (per hour)", 100], ["Baking capacity (per hour)", 240], ["Utilisation", "=B1/B2"]]}},
    {"id": 2, "unit": 1, "topic": "Little's Law", "diff": "H", "q": "A call centre holds 12 callers on average and answers 48 per hour. Average time in system?",
     "o": ["15 minutes", "4 minutes", "4 hours", "36 minutes"], "a": 0,
     "e_explain": "W = L ÷ λ = 12 ÷ 48 hour = 0.25 hour = 15 minutes. 4 hours inverts the ratio; 4 minutes forgets the hour.",
     "calc": {"rows": [["Callers in system L", 12], ["Throughput λ (per hour)", 48], ["Time in system (min)", "=B1/B2*60"]]}}]))
print("filled", desk)
