# AGENTS

## Environment

- Windows, Python 3.13. Use PowerShell syntax (`;` not `&&`, no `2>$null` on `&`-invocations — use `cmd /c '... > file 2>nul'` when you need to capture stdout of a binary like `msedge --dump-dom`).
- Package uses `src/` layout; install editable before running tests or CLI.

## Setup

```powershell
pip install -e .
```

## Test

```powershell
python -m pytest -q
```

Integration tests require `samples/static_memory_datas/*.zip` and are skipped when absent.

## Run

```powershell
memscope compare <baseline.zip> <target.zip> [-o outdir] [--labels A B]
```

Outputs `report.md`, `report.html`, `data.xlsx` into `out/<labelA>_vs_<labelB>/` by default.

## Screenshot verification of report.html

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
& $edge --headless=new --disable-gpu --window-size=1440,2400 --virtual-time-budget=20000 --screenshot=shot.png "file:///abs/path/report.html#tab=overview"
```

Routes: `#tab=overview|system|levels|procs|inhouse|events|risk|report`, `#proc=<name>[&metric=pss|rss]`, `&metric=rss` appends to any route.

## Conventions

- `analysis.py` produces a single JSON-serializable result dict; all renderers (`render/markdown.py`, `render/html.py`, `render/excel.py`) consume it — do not re-parse datasets in renderers.
- `report_template.html` in `src/memscope/assets/` is the UI; `mockups/` is a requirements-discussion mockup kept as reference — keep them visually aligned but they evolve separately.
- `.work/` is scratch (gitignored); `samples/` and `out/` are gitignored and must not be committed.
