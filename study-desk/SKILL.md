---
name: study-desk
description: "Use for every study-desk build. Past papers are digitized word for word, never paraphrased, with solutions. Tables and figures appear only where the source material has them, and each desk gets its own colour theme."
---

# Study Desk

Build one offline HTML file a student revises from. It has up to four sections: a generated
**question bank**, **past papers** digitized with solutions, **concept notes**, and a **quick
reference**. Progress is saved in the browser. Scripts do the copying, checking and rendering;
you do the reading, judging and writing. You never hand-write the HTML, CSS or JavaScript, and
you never retype a past paper.

Tools: https://github.com/nishanth-kannan/study-desk-generator (this file needs **v2.2 or later** in the v2 line: v2.2 added
answer-format detection — unlettered options, answer boxes, printed keys and multi-part questions — past-paper
examples inside concept notes, and Reveal all / Hide all toggles).

---

## THE NON-NEGOTIABLE RULES (the student set these explicitly)

1. **Never paraphrase a question from the material the student provides.** Past papers, practice
   sets, problem sets and quizzes are *digitized*, not rewritten: every word, number, typo, mark and
   printed answer hint ("Ans: …"), in the printed structure and order. A shortened or "cleaned up"
   question is a violation. The only allowed changes are layout and fixing OCR mistakes by reading
   the page image. The scripts copy the text for you; the build fails if anything differs.
   **The answer format is part of the question.** A question printed with choices (lettered or not,
   radio buttons, True / False, a drop-down) stays a choice question; a numeric entry box stays a
   numeric box; a question with several separately answered sub-parts keeps one answer per sub-part.
   Never collapse a choice or numeric question into a written answer box.
2. **The Past Papers section only digitizes and solves.** Each question shows the verbatim question,
   then the **official solution as supplied** (handwritten sheets included) when one exists, then
   your explanation (concepts, working, Excel formulae). You may add your own take in the
   explanation; you may never touch the question.
3. **Tables and images appear only when necessary and present in the given material.** Reproduce
   a table or figure the question or its official solution contains. Draw one in a *solution* only
   when the question asks the student to draw it ("draw a Gantt chart"). Never invent a summary
   table, flow diagram or chart for a past-paper or bank question, and never turn a printed list
   into a table. (Concept notes may use comparison tables freely.)
4. **Bank questions don't restate source problems.** A bank question built on a past-paper problem
   uses `"ref"`: its stem is only the ask, and the desk shows the source problem verbatim in a
   collapsible box. Never copy a past-paper MCQ into the bank with a reworded stem or options.

---

## Get the tools (first command of every build)

```bash
T="<skill base directory>/scripts"
ok() { case "$(cat "$1/VERSION" 2>/dev/null)" in v2.0|v2.1|v2.0.*|v2.1.*) false ;; v2.*) true ;; *) false ;; esac; }
if ! ok "$T"; then                                   # bundled tools missing or older than v2.2: fetch the newest v2.x release
  T=/tmp/study-desk-tools/study-desk/scripts
  if ! ok "$T"; then
    rm -rf /tmp/study-desk-tools
    REPO=https://github.com/nishanth-kannan/study-desk-generator.git
    TAG=$(git ls-remote --tags --refs "$REPO" 'v2.*' | sed 's#.*refs/tags/##' | sort -V | tail -1)
    [ -n "$TAG" ] && git -c advice.detachedHead=false clone -q --depth 1 --branch "$TAG" "$REPO" /tmp/study-desk-tools
  fi
fi
if ok "$T"; then python3 "$T/check_env.py"; else echo "STOP: study-desk tools v2.2+ not found"; false; fi
```

It prints the tools' version and absolute path: use that path in every later command (shell
variables don't carry between commands). If it prints STOP or the clone fails, **stop and tell the
student** (v2.2 must be released in the repo); never recreate the scripts from memory. Work in a
desk folder (e.g. `./om1-desk/`) and run every command from it.

---

## Workflow

### 0. Settle which sections to build

| Section | Builds | They might say |
|---|---|---|
| `concepts` | Chapter-by-chapter revision notes | notes, theory, summarise, revision |
| `questions` | The generated practice-question bank | quiz me, practice questions, MCQs, drills |
| `papers` | Past papers digitized with solutions | past papers, PYQs, previous exams, practice sets |
| `reference` | Traps, frameworks and the coverage table | quick reference, cheat sheet |

If the student named sections, build exactly those. If not, ask with AskUserQuestion (one
multi-select question; offer `papers` only if papers were supplied). If nobody is there to answer,
build `concepts` and `questions` and say so. State your scope ("units 1–8, ~25 questions each")
rather than asking again.

### 1. Ingest every upload

```bash
python3 <tools>/ingest.py <uploads...>          # PDFs, scans, photos, pptx/docx, xlsx/csv, txt
cat work/summary.txt
```

It writes `sources/<id>.txt` (the verbatim master text), finds bold, tables, figures, running
headers and two-column pages, OCRs scans (Tesseract, per-word confidence), sorts files into
paper / solution / reading, and pairs solution sheets with papers. **`summary.txt` is what you
read instead of the pages.** View only the images it lists, in one batch per paper:

- **table / figure crops** (typed pages): confirm the table reads right and the figure is complete.
- **OCR review strips** (scans): each crop of a flagged line sits above its OCR reading. Every
  number and every low-confidence word is flagged. Fix only the lines that differ, with small edits
  to `sources/<id>.txt` at the line numbers shown. Never edit `*.ocr.txt`.
- **layout thumbnails** (scans): if a scanned page has a table, write it into the source at its
  place as `[[table <id>]]`, TAB-separated rows, `[[/table]]`. If it has a figure, crop it
  (`pdftoppm -r 150 -f N -l N -x -y -W -H`) to `work/view/` and add `[[figure <id> <path>]]`.
- **full pages marked handwritten**: transcribe the page word for word into the source.

**Answer formats.** Ingest reads them off the page and writes them into the source:

| On the page | In the source | Desk shows |
|---|---|---|
| radio buttons / check boxes next to choices (lettered or not; side-by-side rows are split) | `[[options]]` … `[[/options]]`, one option per line | clickable MCQ |
| an answer box (typed entry, drop-down) | `[[blank]]` (a typed entry is kept as `#~ [entry] …`) | numeric box, choices or written answer (step 3) |
| a printed key, e.g. "The correct answer is: …" | `[[key]] The correct answer is: …` | official solution; sets the MCQ answer |

**Attempt reviews** (Moodle and similar printouts) are recognised: the left status column becomes
`[[item N]]` plus an ignored line (`#~ Question 4 · Partially correct · Mark 12.60 out of 15.00`);
"Mark x out of y", the typed entries, the selected-option dots and the course navigation are
ignored lines; the attempt summary on page 1 (status, times, grade) is evidence, so mark it `#~`.
For lettered (A)–(D) options on typed papers nothing changes: scaffold splits them as before. When
ingest misses choices (a scan, a list printed without buttons), wrap them yourself in
`[[options]]` … `[[/options]]`, one per line, words untouched.

Pen marks on a scan are evidence (a crossed-out answer means the key differs), not part of the
paper: don't transcribe them; mention them in the paper's `note`. A referenced but missing exhibit
("see Exhibit 15") is noted in `note` and in the answer; never invent its data.

Wrong role or pairing guess? Re-run with `--role ID=paper|solution|reading` or `--pair SOL=PAPER`.
Papers set the register of the bank: skim the sources of the newest paper before writing questions.

### 2. Scaffold the papers

```bash
python3 <tools>/scaffold.py
```

Writes `data/papers/<id>.json`: every printed question as one item with its text, tables and
figures copied from the source; headings and instructions as `head` items; MCQ options split
out; official solutions copied into `e_official` (MCQ keys set `a`); `note` drafted. Its report
lists what needs you:

- **`_check`** on an item or paper is a guess to confirm. Look, fix if needed, delete the `_check`.
  The build refuses unconfirmed guesses.
- **Structure wrong** (a question split in two, a heading glued to a question)? Don't edit the
  JSON text; add a directive line to the source and re-run. Your fields survive re-runs.
  `[[item]]` / `[[item 7]]` next line starts a question (numbered 7) · `[[head]]` next line starts
  a heading block · `[[nobreak]]` next line is not a new question · `[[end]]` close the current item ·
  `[[part]]` next line starts a sub-part with its own answer · `[[part head]]` next line starts a
  passage between sub-parts (a new case, a second table) · `[[options]]`…`[[/options]]` the choices ·
  `[[blank]]` an answer box.
- **Item types come from the answer slots** (`[[options]]` blocks and `[[blank]]`s): one options
  block → MCQ; blanks only → an answer box; **two or more slots → `parts`**, one per answer, each
  with its label, text, options or boxes and printed key; the stem before the first labelled
  sub-part stays on the item; text between sub-parts becomes a `head` part. An automatic split is a
  `_check`: compare the cuts with the page, move them with `[[part]]` lines if needed.
- **Lower-case (a)–(d) read as options** but they are sub-parts: set `"sub": true`, re-run.
- **"answer needed"**: no key was supplied; solve it and set `a` (zero-based) — on the item, or on
  the part (`parts[k].a`).
- **Answer boxes** (`"sub": true` with `"blanks": n`) are written answers until you say otherwise.
  A numeric box gets `"num"` (a number, or a list for several boxes such as the two ends of an
  interval) and optional `"tol"` (relative, default 0.005); delete `"sub"`. A drop-down whose
  choices are named in the question text gets `"choices": [...]` and `"a"` (every choice must be in
  the source). Leave `sub` only for genuinely written answers.

### 3. Write the solutions (section `papers`)

For each question item — and for each sub-part in `parts` — add, with small Edit-tool edits to
`data/papers/<id>.json`:

- **`e_explain`** (always): the concept, the working, and why the tempting wrong answer is wrong.
  The build shows `Official solution (as supplied)` then `Explanation`, or `Worked solution (no
  official solution was supplied)`.
- **`calc`** for anything quantitative (see *calc*). The build turns it into the Excel formulae
  table, recalculates it in LibreOffice, and checks it against `expect`, the `num` answer, the
  correct option or the numbers in `e_official`. Don't write Excel formulae in prose. Excel 2010+
  names (NORM.INV, T.DIST.2T, BINOM.DIST…) are fine: the tools add the `_xlfn.` prefix the file needs.
- If the official key is wrong, say so in `<span class='warn'>` in `e_explain`, and give the calc a
  `"dispute"`. When the solution sheet skips questions, solve them; the `note` already says which.
- Rename `name` if the paper calls a question something else ("Problem 4", "Question II · 3").
- Multi-part questions: each answerable part needs its own `e_explain` (and `calc` where it
  computes); an item-level `e_explain` is optional and shows as notes on the whole question.

### 4. Concept notes (section `concepts`) — `data/concepts/<unit>.json`

One file per unit, revision notes someone who skipped the lecture could learn from. Use `defs` for
terms, `table` for real comparisons, `list` for ordered enumerations, `p` for arguments and `box`
for memory hooks. Write headings as claims ("Parallel ≠ always add — the croissant trap").
Reproduce the reading's canonical examples. End each unit with a box titled *In the exam* naming
the recurring question shapes. Figures come from the readings or official solutions.

**Past-paper examples (when papers are in the desk).** Under each section a past paper tests, add
`{"t": "ex", "paper": "<id>", "n": <n>, "part": "g)", "why": "<one line: what this shows>"}`
right after the explanation it illustrates (`part`: a sub-part label or 1-based index; omit it for
a whole question; `"with_stem": false` hides the shared stem). The build copies the question
verbatim from the built paper — stem and earlier passage in collapsible boxes, options listed —
puts the answer, explanation and Excel working behind *Show answer*, and adds a *Practise it in
Past Papers* link that jumps to that item. Never retype a question into the notes; never add an
example the paper doesn't support. Aim for every exam-tested idea to have one, and every paper
question to appear under at least one section.

### 5. The question bank (section `questions`) — `data/bank/<unit>.json`

- About 20–35 per unit, in the papers' register, roughly **10% easy / 55% medium / 35% hard**.
  Consistent topic labels within a unit. One file per unit, written once.
- **Every distractor is the right answer to a slightly different question**: the adjacent concept,
  the inverse, a common error, or a true statement that doesn't answer. No habitual "all/none of
  the above", lopsided option lengths or absolutes.
- **The explanation is the product** (`e_explain`): name the principle, apply it, draw the boundary
  with the tempting distractor, anchor it in the formula or paper. Quantitative → `calc`.
- Negative stems: *incorrect*/*NOT* in `<em>`. Vary the correct option's position.
- **Built on a source problem → `"ref": {"paper": "<id>", "n": <n>}`** and an ask-only stem
  (`"with_head": false` skips the case text, `"open": true` starts expanded). New questions use
  your own scenario and numbers, never a paper's wording. Figure-reading questions only with
  figures from the supplied material.

### 6. Build, then look at the riskiest items

```bash
python3 <tools>/build_desk.py data -o "<Course> Study Desk.html" --sections "concepts,questions,papers,reference"
python3 <tools>/verify.py --compare work/compare.html --top 6 --out work/review.png
```

The build checks every paper item against its source — precision, **recall** (nothing left out),
**numbers** (as a multiset: catches 459 for 450), tables and figures shown, every option and printed
key, official solutions — runs the calcs, and prints errors plus a short summary (full report:
`work/build_report.txt`, with each paper's count of MCQ, numeric and written answers: a paper full
of choices that reports 0 MCQ is a red flag).
Fix the transcription, never the threshold. It also writes `work/compare.html`: each original
crop beside its digitized item, **riskiest first** (OCR-corrected, scanned, tables, imperfect
matches). View the `review-*.png` screenshots of the top rows; fix what's off and rebuild.

### 7. Verify and deliver

```bash
python3 <tools>/verify.py "<Course> Study Desk.html"
```

Checks every tab renders, Check answer works (bank and past papers, sub-parts included), Reveal
all toggles back to Hide all, example links open Past Papers, every paper renders, no JS errors,
no sideways scroll at 400 px in dark mode. (The bank's *Reveal all shown* toggles the same way.)
Deliver the HTML and
`<Course> Study Desk - Excel workings.xlsx`.
Say what's in each tab, which sections were left out, the theme, which official solutions were
missing, any disputed keys, and that `work/compare.html` lets the student check the digitization.

---

## Saving tokens and time

- Don't view a page `summary.txt` doesn't list. Don't `cat` whole sources or data files; use
  `sed -n 'A,Bp'` or `grep -n` for the lines you need.
- Edit JSON with small Edit-tool edits; never rewrite a whole file to change a field.
- Never copy question or solution text into JSON yourself; fix the source and re-run scaffold.
- Write each bank and notes file once, unit by unit. Rebuild only after a batch of fixes.
- Read `work/build_report.txt` only when the summary isn't enough.

---

## Data layout

```
<desk>/sources/<id>.txt        verbatim master text (edit only to fix OCR / add directives)
<desk>/work/                   inventory.json, summary.txt, view/, compare.html, build_report.txt
<desk>/data/meta.json          {"meta": {...}, "units": [...], "guide": {...}}
<desk>/data/papers/<id>.json   made by scaffold.py; you add e_explain, calc, a, name
<desk>/data/concepts/<unit>.json   {"unit", "title", "gist", "blocks": [...]}
<desk>/data/bank/<unit>.json   [{"id", "unit", "topic", "diff", "q", "o", "a", "e_explain", "calc"?, "ref"?, "media"?}]
```

- `meta`: `title`, `subtitle`, `unitLabel`, `storageKey` (unique per course), optional `theme`.
  `guide`: `intro`, `frameworks: [{h, x}]`, `traps: [{h, x}]`, `sources`.
- `a` is **zero-based**. Bank `id`s are unique. Every `unit` must be declared.
- Paper items: `o`/`a` = MCQ; `num` (+`tol`) = numeric box(es); `sub: true` = written answer;
  `head: true` = printed text with no answer; `parts: [...]` = sub-parts, each with `label`, `q`,
  `media`, and one of `o`/`a`, `choices`/`a`, `num`/`tol` or `sub`, plus `e_official` (printed key),
  `e_explain`, `calc`; a part with `head: true` is a passage between sub-parts. Fields starting `_`,
  and `src`/`e_src`/`blanks`, belong to the scripts: leave them alone.
- Text fields take inline HTML: `<b> <em> <code> <br> <sub> <sup> <ul>/<ol>/<li> <h4>`;
  `<span class='fx'>` for a formula, `<span class='warn'>` for a caveat or disputed key,
  `<b class='ans'>` for printed answer hints (blurred in bank refs), `<h5>` for sub-headings.

### calc

```json
"calc": {"rows": [["Annual demand D", 12000], ["Ordering cost S", 450], ["Holding cost H", 30],
                  ["EOQ", "=SQRT(2*B1*B2/B3)"], ["Orders per year", "=B1/B4"]],
         "answer": ["EOQ", "Orders per year"], "expect": [600, 20]}
```

Row *i* is cell B*i* (label in A). `answer`: labels or 1-based rows (default: last row). Checked
against `expect`, else the `num` answer, else the correct MCQ option's number, else the numbers in `e_official`
(tolerance `tol`, default 0.5%; percentages match either 0.417 or 41.7). A disagreement with the
official solution fails the build unless `"dispute": "<why the key is wrong>"`, which the desk
shows as a caveat.

### Blocks (concept `blocks`, and `media`/`emedia` on questions)

| `t` | Fields |
|---|---|
| `h`, `p`, `list`, `defs`, `box` | notes blocks: `x` (`defs`: `[[term, definition], …]`; `box` needs `title`) |
| `table` | `head`, `rows` (each row as long as `head`), optional `groups=[[label, span], …]`, `title`, `caption`, `note` |
| `flow` | process/precedence diagrams: `nodes=[{id, label ("\n" breaks), col, row, shape: task/store/decision/terminal, sub, hl}]`, `edges=[[from, to, label?]]`, `colw`, `rowh`, `boxw` |
| `cpm` | activity network: `acts=[{id, d, pred: [...]}]`; computes ES/EF/LS/LF and the critical path |
| `chart` | `kind: "line"` (`points` or `series=[{name, points}]`, `xticks`, `annotate`, `fill`) or `kind: "bar"` (`bars=[[label, y]]`); `xlabel`, `ylabel`, `title`, `caption` |
| `gantt` | `rows=[{label, bars: [[start, end, text?, alt?]]}]`, `ticks`, `xlabel` |
| `fig` | an image from the material: `src` (path relative to the desk, or data URI) or `svg` |
| `pre` | monospace listing: `x` |
| `ex` | (concept notes only) a past-paper example: `paper`, `n`, optional `part`, `why`, `with_stem` |

Everything follows the desk's theme in light and dark mode and scrolls sideways on phones.

## Colour theme

Every desk gets its own accent colour, derived from `meta.storageKey` so it survives rebuilds. Pin
one with `meta.theme` or `--theme`: `indigo, cobalt, ocean, teal, moss, plum, violet, orchid,
berry, rose, slate`, a hue `0–359`, or `random`. If it collides with a desk the student already
has, pin another.

## Common failure modes

- Retyping or "tidying" a past-paper question instead of fixing the source and re-running scaffold.
- **Turning a choice or numeric question into a written answer box** (missed `[[options]]`, a
  Moodle MCQ shown as "Subjective", sub-parts a)–o) lumped into one textarea). Check the build's
  MCQ / numeric / written counts against the paper.
- Viewing every page instead of what `summary.txt` lists; dumping whole files into the context.
- Deleting a `_check` without looking.
- Fixing an OCR line from memory instead of the strip; editing `*.ocr.txt`.
- Adding a table or diagram the material doesn't have (rule 3); restating a source problem in the
  bank instead of `ref` (rule 4).
- Excel formulae written in prose instead of a `calc`; a calc that "proves" nothing (no expect,
  key or option to check against).
- Piling questions into a few units (check the coverage line and rebalance); obvious distractors;
  explanations that just restate the right option.
- Concept notes with no past-paper examples when papers are in the desk, or examples typed into a
  `p` block instead of an `ex` block.
- Shipping an answer key you didn't verify; building sections the student didn't ask for.
