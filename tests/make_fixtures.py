#!/usr/bin/env python3
"""Generate synthetic test papers (nothing here comes from real course material).

    python3 tests/make_fixtures.py <out_dir>

Writes:
  setA.pdf        typed paper: headers/footers, bold headings, a table between text, a chart, sub-parts, marks, MCQs
  setA_solutions.pdf   typed official solutions that skip a question ("other questions are straightforward")
  setB_scan.pdf   a typed paper degraded into an image-only "scan" (skew, blur, noise)
  setB_solutions_scan.pdf  a handwritten-style solution page (jittered glyphs) as an image-only scan
  truth/<id>.txt  the exact text of each fixture, for measuring OCR accuracy
"""
import io, math, random, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
(OUT / "truth").mkdir(parents=True, exist_ok=True)
random.seed(7)

BODY = ParagraphStyle("b", fontName="Times-Roman", fontSize=11, leading=14.5, spaceAfter=5)
BOLD = ParagraphStyle("h", parent=BODY, fontName="Times-Bold", fontSize=12, spaceBefore=6)
TITLE = ParagraphStyle("t", parent=BODY, fontName="Times-Bold", fontSize=15, leading=19, spaceAfter=8)
SUB = ParagraphStyle("s", parent=BODY, leftIndent=18)


def page_furniture(label):
    def draw(c, doc):
        c.saveState(); c.setFont("Times-Italic", 9)
        c.drawString(20 * mm, A4[1] - 12 * mm, label)
        c.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page} of 2")
        c.restoreState()
    return draw


def table(rows):
    t = Table(rows, hAlign="LEFT", colWidths=[45 * mm, 45 * mm, 30 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Times-Roman", 10.5),
                           ("FONT", (0, 0), (-1, 0), "Times-Bold", 10.5),
                           ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def chart():
    d = Drawing(150 * mm, 62 * mm)
    lp = LinePlot(); lp.x, lp.y, lp.width, lp.height = 14 * mm, 10 * mm, 125 * mm, 46 * mm
    lp.data = [[(9, 0), (10, 70), (11, 130), (12, 170), (13, 200)],
               [(9, 0), (10, 40), (11, 100), (12, 160), (13, 200)]]
    lp.lines[0].strokeColor = colors.black; lp.lines[1].strokeColor = colors.grey
    lp.lines[1].strokeDashArray = [3, 2]
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax, lp.xValueAxis.valueSteps = 9, 13, [9, 10, 11, 12, 13]
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = 0, 200
    d.add(lp)
    d.add(String(100 * mm, 52 * mm, "Cumulative arrivals (solid) and departures (dashed)", fontSize=8, fontName="Times-Italic"))
    return d


# ---------------------------------------------------------------- Set A (typed)
A_PAGES = [
    [("t", "Operations Management — Practice Set A"),
     ("p", "Time: 60 minutes · Maximum marks: 30"),
     ("p", "Instructions: Answer all questions. Show your working. Calculators are permitted."),
     ("h", "Section I · Problems"),
     ("p", "1. Kavya Bakers runs a three-stage line: mixing, proofing and baking. The table below gives the time "
           "each stage takes per batch and the number of parallel units at each stage."),
     ("table", [["Stage", "Time per batch (min)", "Units"], ["Mixing", "12", "1"], ["Proofing", "45", "3"], ["Baking", "20", "2"]]),
     ("p", "Each batch yields 40 loaves. The bakery operates 8 hours a day."),
     ("s", "(a) Identify the bottleneck stage and compute the capacity of the line in loaves per hour. [4 marks]"),
     ("s", "(b) What is the utilisation of the baking stage if demand is 100 loaves per hour? [3 marks]"),
     ("s", "(c) The owner proposes adding one more proofing unit. Will capacity increase? Explain briefly. [3 marks]")],
    [("p", "2. The chart below shows the cumulative arrivals and departures of customers at a passport office "
           "between 9:00 and 13:00."),
     ("chart", None),
     ("s", "(a) What is the maximum number of customers waiting, and at what time does it occur? [2 marks]"),
     ("s", "(b) Using Little's Law, estimate the average waiting time if the average queue is 18 customers and "
           "customers arrive at 50 per hour. [3 marks]"),
     ("h", "Section II · Multiple-choice questions"),
     ("p", "3. Which of the following increases the capacity of a process whose bottleneck is a single machine?"),
     ("s", "(A) Adding a buffer before the machine"), ("s", "(B) Reducing the setup time of the machine"),
     ("s", "(C) Speeding up a non-bottleneck step"), ("s", "(D) Increasing the batch size of a non-bottleneck step"),
     ("p", "4. A process has a flow rate of 30 units per hour and an average inventory of 15 units. The average flow time is"),
     ("s", "(A) 0.5 hours"), ("s", "(B) 2 hours"), ("s", "(C) 15 minutes"), ("s", "(D) 45 minutes"),
     ("p", "5. Which statement about Little's Law is <i>incorrect</i>?"),
     ("s", "(A) It applies to any stable system"), ("s", "(B) It requires exponential inter-arrival times"),
     ("s", "(C) It relates inventory, flow rate and flow time"), ("s", "(D) It holds for long-run averages")],
]

A_SOL = [
    ("t", "Practice Set A — Solutions"),
    ("p", "1. (a) Capacity of each stage: Mixing 40/12 × 60 = 200 loaves per hour; Proofing 3 × 40/45 × 60 = 160 "
          "loaves per hour; Baking 2 × 40/20 × 60 = 240 loaves per hour. The bottleneck is proofing and the line "
          "capacity is 160 loaves per hour."),
    ("s", "(b) Utilisation of baking = 100/240 = 41.7%."),
    ("s", "(c) Yes. With four proofing units proofing capacity becomes 213.3 loaves per hour, so mixing becomes the "
          "bottleneck and line capacity rises to 200 loaves per hour."),
    ("p", "Other questions are straightforward."),
    ("p", "3. (B)"), ("p", "4. (A)"), ("p", "5. (B)"),
]


def flow(items):
    out, text = [], []
    for kind, x in items:
        if kind == "table": out += [table(x), Spacer(1, 6)]; text += ["\t".join(r) for r in x]
        elif kind == "chart": out += [chart(), Spacer(1, 4)]
        elif kind == "pb": out.append(PageBreak())
        else:
            out.append(Paragraph(x, {"t": TITLE, "h": BOLD, "s": SUB}.get(kind, BODY)))
            text.append(x.replace("<i>", "").replace("</i>", ""))
    return out, text


def typed_pdf(path, pages, label):
    story, text = [], []
    for i, pg in enumerate(pages):
        f, t = flow(pg); story += f; text += t
        if i < len(pages) - 1: story.append(PageBreak())
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=22 * mm, bottomMargin=20 * mm)
    furn = page_furniture(label) if label else (lambda c, d: None)
    doc.build(story, onFirstPage=furn, onLaterPages=furn)
    return text


tA = typed_pdf(OUT / "setA.pdf", A_PAGES, "PGP-I · Operations · Practice Set A")
(OUT / "truth" / "setA.txt").write_text("\n".join(tA) + "\n")
tS = typed_pdf(OUT / "setA_solutions.pdf", [A_SOL], None)
(OUT / "truth" / "setA_solutions.txt").write_text("\n".join(tS) + "\n")

# ---------------------------------------------------------------- Set B (scanned)
B_PAGES = [[
    ("t", "Operations Management — Practice Set B"),
    ("p", "Answer both questions. Marks are shown in brackets."),
    ("p", "1. Annual demand for a spare part is 12,000 units. Each order costs Rs 450 to place and holding one unit "
          "for a year costs Rs 30."),
    ("s", "(a) Compute the economic order quantity. [3 marks]"),
    ("s", "(b) How many orders are placed in a year, and what is the total annual ordering cost? [2 marks]"),
    ("p", "2. A clinic sees 6 patients per hour on average and each patient spends 25 minutes in the clinic. "
          "How many patients are in the clinic on average?"),
    ("s", "(A) 1.5"), ("s", "(B) 2.5"), ("s", "(C) 4.2"), ("s", "(D) 150"),
]]


def degrade(img, angle=0.6, blur=0.6, noise=10):
    img = img.convert("L").rotate(angle, expand=False, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(blur))
    px = img.load(); w, h = img.size
    for _ in range(w * h // 60):                       # speckle
        x, y = random.randrange(w), random.randrange(h); px[x, y] = max(0, min(255, px[x, y] + random.randint(-noise * 8, noise * 8)))
    return img


def pdf_to_images(pdf, dpi=200):
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), f"{td}/p"], check=True)
        return [Image.open(p).copy() for p in sorted(Path(td).glob("p*.png"))]


def images_to_pdf(imgs, path, dpi=200):
    imgs[0].save(path, "PDF", resolution=dpi, save_all=True, append_images=imgs[1:])


tmp = OUT / "_setB_typed.pdf"
tB = typed_pdf(tmp, B_PAGES, None)
images_to_pdf([degrade(i) for i in pdf_to_images(tmp)], OUT / "setB_scan.pdf")
tmp.unlink()
(OUT / "truth" / "setB_scan.txt").write_text("\n".join(tB) + "\n")

# handwritten-style solutions: jittered glyphs, uneven baseline
HAND = ["1. (a) EOQ = sqrt(2 x 12000 x 450 / 30) = 600 units",
        "(b) Orders per year = 12000 / 600 = 20",
        "Ordering cost = 20 x 450 = Rs 9000",
        "2. (B) L = 6 x 25/60 = 2.5"]
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf", 44)
pg = Image.new("L", (1654, 2339), 255); dr = ImageDraw.Draw(pg)
y = 220
for line in HAND:
    x = 150 + random.randint(-10, 10)
    for ch in line:
        g = Image.new("L", (60, 70), 0); ImageDraw.Draw(g).text((8, 4), ch, font=font, fill=255)
        g = g.rotate(random.uniform(-9, 9), resample=Image.BICUBIC)
        pg.paste(0, (x, y + random.randint(-5, 5)), g)
        x += int(font.getlength(ch) * random.uniform(0.95, 1.12)) + 1
    y += 120 + random.randint(-8, 12)
images_to_pdf([degrade(pg, angle=-0.4, blur=0.8)], OUT / "setB_solutions_scan.pdf")
(OUT / "truth" / "setB_solutions_scan.txt").write_text("\n".join(HAND) + "\n")
print("fixtures written to", OUT)
