# AGENTS

## Environment

- Do not pipe scripts containing non-ASCII (Chinese) into `python -` from Windows PowerShell 5.1: `$OutputEncoding` is US-ASCII, so every CJK char becomes `?` (this once produced `<title>????????</title>` in re-rendered reports). Write the script to `.work/*.py` (UTF-8) and run it instead.
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

Integration tests require `samples/static_memory_datas/*.zip` and are skipped when absent. Dynamic integration tests additionally need `$env:MEMSCOPE_SLOW=1` (loads two ~5GB packages, takes minutes):

```powershell
$env:MEMSCOPE_SLOW=1; python -m pytest -q tests/test_integration_dynamic.py
```

## Run

```powershell
memscope static compare <baseline.zip> <target.zip> [-o outdir] [--labels A B] [--tmpdir DIR]
memscope dynamic compare <baseline.(zip|rar)> <target.(zip|rar)> [-o outdir] [--labels A B] [--tmpdir DIR]
```

Outputs `report.md`, `report.html`, `data.xlsx` into `out/<static|dynamic>/<labelA>_vs_<labelB>/` by default.

- Inputs may be platform zip / rar / extracted dirs. rar support needs system `tar` (Windows 10 1803+ ships bsdtar); `debuglogger/` is excluded during extraction.
- Nested `tw_rpt_*_rounds.zip` / `mobilelog.zip` are materialized to a temp dir (peak temp space ≈ one package, ~5GB); use `--tmpdir` to redirect when the system temp drive is tight.

## Build single-file exe

```powershell
cmd /c build_exe.bat
```

Produces `dist\memscope.exe` (isolated venv `.venv-build\`, PyInstaller, entry `packaging\memscope_entry.py`).

## Screenshot verification of report.html

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
& $edge --headless=new --disable-gpu --window-size=1440,2400 --virtual-time-budget=20000 --screenshot=shot.png "file:///abs/path/report.html#tab=overview"
```

`--screenshot` 必须使用**绝对路径**（Edge headless 会切换工作目录，相对路径报「系统找不到指定的路径」并静默退出）。

Routes: `#tab=overview|system|levels|procs|inhouse|google|events|risk|report` (+ `pressure` for dynamic reports), `#proc=<name>[&metric=pss|rss]`, `&metric=rss` and `&cached=1` (include Cached in rankings) append to any route. Dynamic reports default to `metric=rss`; time-series x axes are hours since test start.

## Conventions

- `analysis.py` / `dynamic_analysis.py` each produce a single JSON-serializable result dict; renderers (`render/markdown.py`|`render/dynamic_markdown.py`, `render/html.py`, `render/excel.py`|`render/dynamic_excel.py`) consume it — do not re-parse datasets in renderers.
- `report_template.html` / `report_dynamic_template.html` in `src/memscope/assets/` are the UIs; `mockups/` is a requirements-discussion mockup kept as reference — keep them visually aligned but they evolve separately.
- `.work/` is scratch (gitignored); `samples/` and `out/` are gitignored and must not be committed.
