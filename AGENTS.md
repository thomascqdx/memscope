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

Outputs `report.md`, `report.html`, `data.xlsx` into `out/<static|dynamic>/<labelA>_vs_<labelB>/` by default. Both subcommands share one pipeline and the same flags: `--labels -o --whitelist --prefixes --google-prefixes --gap-abs --gap-pct --resident --leak-delta --leak-pct --leak-r2 --sys-leak-delta --rate-pct --tmpdir` (per-kind argparse defaults come from `analysis.default_options(kind)`).

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

Routes: `#tab=overview|system|levels|procs|inhouse|google|events|pressure|risk|report` (same 10 tabs for both kinds), `#proc=<name>[&metric=pss|rss]`, `&metric=rss` and `&cached=1` (include Cached in rankings) append to any route; `#tab=events&q=<kw>` prefills the wake-chain filter. Default metric = `meta.primary_metric` (static=pss, dynamic=rss). Time-series x axis = round categories when `meta.axis=='round'` (max rounds ≤30), else hours since test start.

## Conventions

- One analysis pipeline: `analysis.py` `analyze()` produces a single JSON-serializable result dict with an identical key set for both kinds; scenario differences come only from data presence and per-kind defaults (`Options.kind` / `default_options(kind)`: primary_metric, resident, leak thresholds, trend min points). Renderers (`render/markdown.py`, `render/html.py`, `render/excel.py`) consume it — do not re-parse datasets in renderers.
- `report_template.html` in `src/memscope/assets/` is the single UI for both kinds (h1/metric/axis derived from DATA.kind/DATA.meta); `mockups/` is a requirements-discussion mockup kept as reference — keep them visually aligned but they evolve separately. `report_dynamic_template.html` and `dynamic_analysis.py`/`dynamic_*` renderers are legacy files kept on disk pending removal — nothing imports them.
- Terminology in UI/MD/Excel: 轮均 (=round-mean, absent rounds count 0), 值均 (=mean over present rounds), 出现率, 疑似泄漏 (趋势回归，提示非定论), 自动结论, 关键指标, 测试判定 — never print the raw token "mi".
- `datasets[i].events` is inlined only when ≤5000 events (else `None`, see `events_total`; details live in data.xlsx 启动事件). Per-proc `pra`/`prb` (per-round level) exist only when max rounds ≤30.
- `.work/` is scratch (gitignored); `samples/` and `out/` are gitignored and must not be committed.
