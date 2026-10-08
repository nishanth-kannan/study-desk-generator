#!/usr/bin/env python3
"""Fault injection: every check must catch the mistake it exists for.

    python3 tests/faults.py <desk>      (a desk that builds cleanly; run after fill_fixture.py)

Each fault copies the data folder, makes one realistic mistake, runs build_desk.py --check and
expects it to fail with the named message. Exits 1 if any fault slips through.
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

desk = Path(sys.argv[1]).resolve()
tools = Path(__file__).resolve().parents[1] / "study-desk" / "scripts"


def item(d, name):
    return next(it for it in d["qs"] if it.get("name") == name)


def media_p(it):
    return next(b for b in it["media"] if b["t"] == "p")


FAULTS = [  # (what, paper, mutate(paper_json), expected message)
    ("paraphrased stem", "seta", lambda d: item(d, "Question 1").update(q=item(d, "Question 1")["q"].replace("runs a three-stage line", "operates a line with three stages")), "missing from the item"),
    ("one word dropped", "seta", lambda d: item(d, "Question 1").update(q=item(d, "Question 1")["q"].replace(" per batch", "")), "missing from the item"),
    ("sub-part dropped", "seta", lambda d: media_p(item(d, "Question 1")).update(x=media_p(item(d, "Question 1"))["x"].split("<br>(c)")[0]), "missing from the item"),
    ("number mistyped", "setb_scan", lambda d: item(d, "Question 1").update(q=item(d, "Question 1")["q"].replace("Rs 450", "Rs 459")), "numbers differ"),
    ("table cell mistyped", "seta", lambda d: next(b for b in item(d, "Question 1")["media"] if b["t"] == "table")["rows"][1].__setitem__(1, "54"), "numbers differ"),
    ("table left out", "seta", lambda d: item(d, "Question 1").update(media=[b for b in item(d, "Question 1")["media"] if b["t"] != "table"]), "source table p1t1"),
    ("figure left out", "seta", lambda d: item(d, "Question 2").update(media=[b for b in item(d, "Question 2")["media"] if b["t"] != "fig"]), "not shown with the question"),
    ("option reworded", "seta", lambda d: item(d, "MCQ 3")["o"].__setitem__(1, "Cutting the setup time of the machine"), "option B"),
    ("question left out", "seta", lambda d: d.update(qs=[it for it in d["qs"] if it.get("name") != "MCQ 4"]), "source text not in any item"),
    ("official number changed", "seta", lambda d: item(d, "Question 1").update(e_official=item(d, "Question 1")["e_official"].replace("41.7", "41.6")), "official-solution numbers differ"),
    ("official sentence dropped", "seta", lambda d: item(d, "Question 1").update(e_official=item(d, "Question 1")["e_official"].replace(" The bottleneck is proofing and the line capacity is 160 loaves per hour.", "")), "missing from e_official"),
    ("calc disagrees with key", "setb_scan", lambda d: item(d, "Question 1")["calc"]["rows"][2].__setitem__(1, 40), "is not in the official solution"),
    ("calc disagrees with option", "seta", lambda d: item(d, "MCQ 4")["calc"]["rows"][1].__setitem__(1, 15), "the correct option says"),
    ("answer not set", "setb_scan", lambda d: item(d, "MCQ 2").update(a=None), "answer not set"),
    ("explanation missing", "seta", lambda d: item(d, "MCQ 3").pop("e_explain"), "no e_explain"),
    ("guess left unconfirmed", "seta", lambda d: item(d, "MCQ 5").update(_check=["lower-case options read as MCQ"]), "unconfirmed scaffold guess"),
]


def main():
    caught = 0
    for what, pid, mutate, expect in FAULTS:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            for sub in ("data", "sources", "work"):
                shutil.copytree(desk / sub, t / sub, ignore=shutil.ignore_patterns("cmp", "ocr", "view") if sub == "work" else None)
            shutil.copytree(desk / "work" / "view", t / "work" / "view")
            p = t / "data" / "papers" / f"{pid}.json"
            d = json.loads(p.read_text()); mutate(d); p.write_text(json.dumps(d))
            r = subprocess.run([sys.executable, str(tools / "build_desk.py"), str(t / "data"), "--check", "-o", str(t / "x.html")],
                               capture_output=True, text=True)
            ok = r.returncode == 1 and expect in r.stdout
            caught += ok
            print(f"  {'caught' if ok else 'MISSED'}  {what:28s} {'' if ok else (r.stdout or r.stderr)[-400:]}")
    print(f"  faults caught: {caught}/{len(FAULTS)}")
    sys.exit(0 if caught == len(FAULTS) else 1)


if __name__ == "__main__":
    main()
