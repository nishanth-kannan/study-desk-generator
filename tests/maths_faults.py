#!/usr/bin/env python3
"""LaTeX faults that only show in the browser: verify.py must report them, and the page must not hang.

    python3 tests/maths_faults.py <built desk dir>

Each case copies the desk, adds one bad formula to a concept unit, builds, and runs verify.py.
A formula KaTeX can't parse used to send the renderer into an endless loop (the page never loaded);
that now has to come back as an ordinary FAIL line.
"""
import glob, json, shutil, subprocess, sys, tempfile
from pathlib import Path

desk = Path(sys.argv[1]).resolve()
tools = Path(__file__).resolve().parent.parent / "study-desk" / "scripts"
CASES = [  # (what, text added to a concept block, expected verify message)
    ("unparseable formula", "Broken: \\(\\frac{1}{\\)", "unrendered maths"),
    ("unknown command", "Unknown: \\(\\notacommand{x}\\)", "unknown LaTeX command"),
]


def main():
    caught = 0
    for what, text, expect in CASES:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            for sub in ("data", "sources", "work"):
                shutil.copytree(desk / sub, t / sub)
            f = sorted(glob.glob(str(t / "data" / "concepts" / "*.json")))[0]
            d = json.loads(Path(f).read_text()); d["blocks"].insert(0, {"t": "p", "x": text}); Path(f).write_text(json.dumps(d))
            out = t / "x.html"
            b = subprocess.run([sys.executable, str(tools / "build_desk.py"), str(t / "data"), "-o", str(out)], capture_output=True, text=True)
            if b.returncode:
                print(f"  MISSED  {what:22s} build failed: {(b.stdout + b.stderr)[-300:]}"); continue
            try:
                r = subprocess.run([sys.executable, str(tools / "verify.py"), str(out)], capture_output=True, text=True, timeout=240)
                ok = r.returncode == 1 and expect in r.stdout and "Traceback" not in r.stderr
                msg = "" if ok else (r.stdout + r.stderr)[-400:]
            except subprocess.TimeoutExpired:
                ok, msg = False, "verify timed out (page hung)"
            caught += ok
            print(f"  {'caught' if ok else 'MISSED'}  {what:22s} {msg}")
    print(f"  maths faults caught: {caught}/{len(CASES)}")
    sys.exit(0 if caught == len(CASES) else 1)


if __name__ == "__main__":
    main()
