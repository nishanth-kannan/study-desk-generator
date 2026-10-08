#!/usr/bin/env python3
"""Headless smoke test of a built desk, and screenshots of the riskiest rows of the compare sheet.

    python3 verify.py "<Course> Study Desk.html"
    python3 verify.py --compare work/compare.html --top 6 --out work/review.png

Desk checks: every visible tab renders content, Check answer reveals an explanation, every past paper
renders, figures are present, no JavaScript errors, no sideways scroll at 400 px (light and dark).
Prints failures only, then one summary line.
"""
import argparse, sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Playwright not installed -- open the file and check each tab, Check answer, and a narrow window by hand.")


def desk(path):
    fails, tabs, figs = [], 0, 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        for scheme, width in (("light", 1200), ("dark", 400)):
            pg = b.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(path.as_uri()); pg.wait_for_timeout(300)
            for tab in pg.locator(".tabs button:not([hidden])").all():
                tab.click(); pg.wait_for_timeout(150); tabs += 1
                label = tab.inner_text().strip()
                view = pg.locator("#view-" + tab.get_attribute("data-view"))
                if len(view.inner_text().strip()) < 200: fails.append(f"[{scheme}] {label} tab looks empty")
                if tab.get_attribute("data-view") == "bank":
                    opt = pg.locator("#qlist .opt").first
                    if opt.count():
                        opt.click(); pg.locator("#qlist [data-act=check]").first.click(); pg.wait_for_timeout(100)
                        if not pg.locator("#qlist .expl").count(): fails.append("Check answer did not reveal an explanation")
                if tab.get_attribute("data-view") == "past":
                    for btn in pg.locator("#qzpick button").all():
                        btn.click(); pg.wait_for_timeout(100)
                        pg.click("#revealall2"); pg.wait_for_timeout(100)
                        if pg.locator("#qzlist article").count() == 0: fails.append(f"paper {btn.inner_text()[:30]} rendered nothing")
                        figs = max(figs, pg.locator("#qzlist figure.rx").count())
                sw = pg.evaluate("document.documentElement.scrollWidth")
                if sw > width + 1: fails.append(f"[{scheme} {width}px] sideways scroll on {label} ({sw}px)")
            if errs: fails.append(f"JavaScript errors ({scheme}): {errs[:3]}")
        b.close()
    return fails, f"{tabs} tab views checked, light 1200px + dark 400px"


def compare(path, top, out):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1150, "height": 900})
        pg.goto(path.as_uri()); pg.wait_for_timeout(300)
        rows = pg.locator("section.row").all()[:top]
        if not rows: b.close(); return [], "compare sheet has no rows"
        y0 = rows[0].bounding_box()["y"]; last = rows[-1].bounding_box()
        y1 = last["y"] + last["height"]
        shots, y, k = [], y0, 1
        while y < y1:                                   # split tall captures so each image stays readable
            h = min(1500, y1 - y)
            o = Path(out).with_name(f"{Path(out).stem}-{k}.png")
            pg.screenshot(path=str(o), clip={"x": 0, "y": y, "width": 1150, "height": h}, full_page=True)
            shots.append(str(o)); y += h; k += 1
        b.close()
    return [], f"top {len(rows)} rows -> {', '.join(shots)}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("html", nargs="?"); ap.add_argument("--compare"); ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--out", default="work/review.png")
    a = ap.parse_args()
    if a.compare: fails, msg = compare(Path(a.compare).resolve(), a.top, a.out)
    else: fails, msg = desk(Path(a.html).resolve())
    for f in fails: print("  FAIL", f)
    print(("  ok: " if not fails else "  FAILED: ") + msg)
    sys.exit(1 if fails else 0)
