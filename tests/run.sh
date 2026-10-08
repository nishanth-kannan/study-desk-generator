#!/usr/bin/env bash
# End-to-end test on synthetic fixtures. Run before tagging a release.
#   tests/run.sh [workdir]
# fixtures -> ingest -> scaffold -> (Claude's edits, scripted) -> build -> verify -> compare screenshot -> fault injection -> OCR eval
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T="$ROOT/study-desk/scripts"
W=${1:-$(mktemp -d)}
rm -rf "$W/fx" "$W/desk"; mkdir -p "$W/desk"
step() { printf '\n== %s\n' "$1"; }

step "environment";  python3 "$T/check_env.py" --expect "$(cat "$T/VERSION")"
step "fixtures";     python3 "$ROOT/tests/make_fixtures.py" "$W/fx" >/dev/null
cd "$W/desk"
step "ingest";       time python3 "$T/ingest.py" "$W"/fx/*.pdf
step "scaffold";     python3 "$T/scaffold.py"
step "Claude's edits (scripted)"; python3 "$ROOT/tests/fill_fixture.py" "$W/desk"
step "build";        time python3 "$T/build_desk.py" data -o "$W/desk/Fixture Ops Study Desk.html"
step "verify";       python3 "$T/verify.py" "$W/desk/Fixture Ops Study Desk.html"
step "compare";      python3 "$T/verify.py" --compare work/compare.html --top 3 --out work/review.png
step "faults";       python3 "$ROOT/tests/faults.py" "$W/desk"
step "OCR";          python3 "$ROOT/tests/ocr_eval.py" "$W/fx" "$W/desk"
printf '\nALL PASSED  (workdir %s)\n' "$W"
