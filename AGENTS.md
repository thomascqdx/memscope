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

Integration tests require `samples/static_memory_datas-model3.0/*.zip` and are skipped when absent. Dynamic integration tests additionally need `$env:MEMSCOPE_SLOW=1` (loads ~10GB packages, takes minutes):

```powershell
$env:MEMSCOPE_SLOW=1; python -m pytest -q tests/test_integration_dynamic.py   # 3.0 包
$env:MEMSCOPE_SLOW=1; python -m pytest -q tests/test_integration_v2.py       # 2.0 包（samples/dynamic_memory_datas-model2.0）
```

## Run

```powershell
memscope static compare <baseline.zip> <target.zip> [-o outdir] [--labels A B] [--tmpdir DIR]
memscope dynamic compare <baseline.(zip|rar)> <target.(zip|rar)> [-o outdir] [--labels A B] [--tmpdir DIR]
```

Outputs `report.md`, `report.html`, `data.xlsx` into `out/<static|dynamic>/<labelA>_vs_<labelB>/` by default. Both subcommands share one pipeline and the same flags: `--labels -o --whitelist --prefixes --google-prefixes --gap-abs --gap-pct --resident --leak-delta --leak-pct --leak-r2 --sys-leak-delta --rate-pct --tmpdir --model --platform` (per-kind argparse defaults come from `analysis.default_options(kind)`).

- 体验模型自动识别：3.0（`device_info.txt`/`round_info.txt`/`tw_rpt_*rounds.zip`）或 2.0（`devinfo/deviceinfo.txt` + 顶层 `log`）；芯片按 `Platform` 判定（`mt*`→mtk，`ums*`/`sp*`/`sc*`/`ud*`→unisoc，`sm*`/`sdm*`/`msm*`/`qcom*`/`lahaina`/`kona`/`taro`→qcom）。`--model`（auto/2.0/3.0）与 `--platform`（auto/mtk/unisoc/qcom）：1 值两侧同用，2 值对应 A B。
- Inputs may be platform zip / rar / extracted dirs. rar needs system `tar` (Win10 1803+ ships bsdtar): whitelist members (`device_info.txt`, `round_*` core files incl. `dumpsys_meminfo_*.txt` 进程明细, `mobilelog.zip`, `devinfo/*`, `log`, `lmkcnt_*`, `*_Exception_Summary_Report.xlsx`, `bugreport-*.zip`, debuglogger events_log…) are extracted once to tmpdir (bsdtar `*` crosses `/`); ylog 等大文件经 `tar -xOf` 流式读取不落盘。
- 事件源按存在性取第一个：mobilelog.zip（MTK）→ debuglogger/mobilelog/**/events_log* → ylog/ap/*.ylog（Unisoc 容器流式解码）→ bugreport-*.zip EVENT LOG 区段（仅末段兜底）。
- 2.0 包：轮次时间取顶层 `log`（start=本轮 log 行、end=下一轮 start）；设备时钟偏移 = (主机−设备) 各轮中位数，加在事件时间上；阈值/平台判定解析 `*_Exception_Summary_Report.xlsx`；标准 Persistent 合并 `Persistent + Persistent Service`（`Thresholds.level_groups`，仅 2.0——3.0 不合并已验证）。
- Nested `tw_rpt_*_rounds.zip` / `mobilelog.zip` are materialized to a temp dir (peak temp space ≈ one package, ~5GB); use `--tmpdir` to redirect when the system temp drive is tight.

## Regression method

`python scripts/dump_result.py <static|dynamic> <A> <B> <out.json> [tmpdir]` loads both packages and dumps the full `analyze()` result dict to JSON — used to diff behavior across changes (PYTHONPATH selects the code under test, e.g. `.work/baseline_src` worktree pinned to a baseline commit). Diff result JSONs ignoring `meta.tool`.

## Build single-file exe

```powershell
cmd /c build_exe.bat
```

Produces `dist\memscope.exe` plus the versioned artifact `dist\memscope-<ver>.exe` (isolated venv `.venv-build\`, PyInstaller, entry `packaging\memscope_entry.py`; smoke-tested via `--version`).

## Versioning

Single source of truth: `__version__` in `src/memscope/__init__.py` (pyproject reads it via `[tool.setuptools.dynamic]`). `memscope -V` prints it; it also lands in `meta.tool` → HTML footer, report.md header, data.xlsx 测试信息.

RULE: every commit that changes user-visible behavior (CLI flags/output, report structure/content, analysis results, packaging) bumps the version **in the same commit** via `python scripts/bump_version.py minor|patch` — minor for new features or incompatible changes (while <1.0), patch for bug fixes. Docs/tests/pure refactors don't bump.

Release steps: bump → commit → `git tag vX.Y.Z` → `cmd /c build_exe.bat` → artifact `dist\memscope-X.Y.Z.exe`.

## Screenshot verification of report.html

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
& $edge --headless=new --disable-gpu --window-size=1440,2400 --virtual-time-budget=20000 --screenshot=shot.png "file:///abs/path/report.html#tab=overview"
```

`--screenshot` 必须使用**绝对路径**（Edge headless 会切换工作目录，相对路径报「系统找不到指定的路径」并静默退出）。

Routes: `#tab=overview|system|levels|procs|inhouse|google|events|pressure|risk|report` (same 10 tabs for both kinds), `#proc=<name>[&metric=pss|rss]` (proc page extras: `&mm=<fields>&mr=<round ranges>&ms=<a|b|ab>` for the meminfo 明细 card), `&metric=rss` and `&cached=1` (include Cached in rankings) append to any route; `#tab=events&q=<kw>` prefills the wake-chain filter. Default metric = `meta.primary_metric` (static=pss, dynamic=rss). Time-series x axis = round categories when `meta.axis=='round'` (max rounds ≤30), else hours since test start.

## Conventions

- One analysis pipeline: `analysis.py` `analyze()` produces a single JSON-serializable result dict with an identical key set for both kinds; scenario differences come only from data presence and per-kind defaults (`Options.kind` / `default_options(kind)`: primary_metric, resident, leak thresholds, trend min points). Renderers (`render/markdown.py`, `render/html.py`, `render/excel.py`) consume it — do not re-parse datasets in renderers.
- `report_template.html` in `src/memscope/assets/` is the single UI for both kinds (h1/metric/axis derived from DATA.kind/DATA.meta); `mockups/` is a requirements-discussion mockup kept as reference — keep them visually aligned but they evolve separately. `report_dynamic_template.html` and `dynamic_analysis.py`/`dynamic_*` renderers are legacy files kept on disk pending removal — nothing imports them.
- Terminology in UI/MD/Excel: 轮均 (=round-mean, absent rounds count 0), 值均 (=mean over present rounds), 出现率, 疑似泄漏 (趋势回归，提示非定论), 自动结论, 关键指标, 测试判定 — never print the raw token "mi".
- `datasets[i].events` is inlined only when ≤5000 events (else `None`, see `events_total`; details live in data.xlsx 启动事件). Per-proc `pra`/`prb` (per-round level) exist only when max rounds ≤30; `lvr_a`/`lvr_b` (level change points `[[round_idx, level|None]]`) exist for all sizes, `swa`/`swb` (per-round VmSwap MB) only under `metrics.rss`. `datasets[i].platform.basis`/`basis_note` = platform verdict basis text (from Exception_Summary_Report 超标 Summary blocks), computed in analysis and rendered by both MD and HTML.
- `appmem` key: 进程 meminfo 明细（`round_N/dumpsys_meminfo_<proc>[_<pid>].txt` 明细表 Pss Total 列, MB；`parsers/appmeminfo.py`，无 TOTAL 行的桩文件跳过）。`appmem.fields` 全局排序 TOTAL→AOSP 打印序→未知字母序；`procs[name]` 给 `fields/a/b`（`{k: ds.rounds 0-based 下标, v: {field: [mb]}}`，有效文件缺行按 0）、`cov_*`、`heap`（Native/Dalvik Heap trend）、`leak_*`。进程名解析：头 `[name]` → 文件名 `_<pid>` 查本轮 OOM pid 映射（3.0 native）→ stem（3.0 剥 `_<pid>`，2.0 不剥）→ `system_server→system`；同名多 pid 相加。堆泄漏判定同进程泄漏门限（覆盖率 cov/n ≥ resident），追加 `leaks` kind='heap'（findings `进程堆`，各渲染器该 kind 的 metric 原样打印不 upper）。
- Process detail page (`#proc=`): x axis = round number for both kinds; click selects nearest point (multi-select, distinct colors, click again to remove) and lists PSS/RSS/Swap/level in 已选数据点. Interaction regression: `.work/ui_click_test.py` (Playwright, `channel='msedge'`) against reports rendered from result JSONs via `.work/render_from_json.py <result.json> <outdir>`.
- `.work/` is scratch (gitignored); `samples/` and `out/` are gitignored and must not be committed.
