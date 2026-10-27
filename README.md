# memscope

Android 内存健康测试包（静态/动态）A/B 对比工具：读取两个平台测试包（zip/rar/解压目录），产出三份结果：

- `report.md` — Markdown 对比报告
- `report.html` — 单文件离线交互报告（内嵌 ECharts，支持 PSS/RSS 切换、Tab/链接路由）
- `data.xlsx` — 全量明细数据

## 安装

```powershell
pip install -e .
```

依赖：`openpyxl`、`xlrd`（读取平台 xls 阈值）。

## 使用

```powershell
memscope static compare <基线A.zip> <对比B.zip> [-o 输出目录] [--labels A B]
memscope dynamic compare <基线A.(zip|rar)> <对比B.(zip|rar)> [-o 输出目录] [--labels A B]
```

两种类型共用同一条分析管线与同一套报告结构（同一组 Tab/章节/sheet），
差异仅来自数据有无与按类型的默认参数。共用参数：

```
--whitelist 白名单.txt --prefixes 前缀.txt --google-prefixes 前缀.txt
--gap-abs 5 --gap-pct 10 --resident <出现率> --leak-delta 10 --leak-pct <%>
--leak-r2 <R²> --sys-leak-delta <MB> --rate-pct 20 --tmpdir 临时目录
```

按类型默认值：

| 参数 | static | dynamic |
|---|---|---|
| resident（常驻出现率） | 1.0 | 0.9 |
| leak-pct（%） | 0 | 20 |
| leak-r2 | 0.6 | 0.3 |
| sys-leak-delta（MB） | 50 | 100 |
| 主口径 | PSS | RSS |
| 趋势最少出现轮 | 5 | 10 |

- `static`：静态内存测试包；`dynamic`：动态内存测试包（monkey 长衰）。默认小时时间轴；轮次 ≤30 时按轮类目轴。统一含 LMK/ANR/crash/重启/vmstat 内存压力/疑似泄漏/自动结论。
- 输入为平台 `内存健康测试对比_*`（zip/rar/解压目录）。rar 需系统 `tar`（Win10 1803+ 自带 bsdtar），解包时剔除 debuglogger。
- 嵌套 rounds/mobilelog zip 物化到临时目录（峰值约一包 ~5GB），`--tmpdir` 可改临时目录位置。
- 默认输出 `out/<static|dynamic>/<A标签>_vs_<B标签>/`；标签默认取 `Rom Version`，相同则追加 `#SN`。
- 自研进程判定：`--whitelist` 优先（白名单），其次 `--prefixes`（默认 `config/inhouse_prefixes.txt`）前缀匹配。
- Google 进程判定：`--google-prefixes`（默认 `config/google_prefixes.txt` 或内置）前缀匹配；与自研同时命中时自研优先。
- 测试包事件数 >5000 时 HTML 不内嵌明细（明细在 data.xlsx 的「启动事件」sheet）。

## 打包单文件 exe

```bat
build_exe.bat
```

在隔离环境 `.venv-build\` 中安装 PyInstaller，产出 `dist\memscope.exe`，用法同 CLI：

```powershell
memscope.exe static compare A.zip B.zip -o outdir
```

## 口径说明

- **PSS** 数据：dumpsys meminfo OOM 层（含 swap，层为总值）→ 默认静态主口径；**RSS** 数据：procStatus 的 VmRSS+VmSwap，OomScore 映射层级（用于标准对照，默认动态主口径）。
- 轮均 = 非空轮合计 ÷ 总轮数（缺席按 0）；值均 = 仅出现轮均值；出现率 = 出现轮数 ÷ 总轮数。
- GAP = B − A（轮均）；显著差异：|GAP| ≥ gap-abs 且（新增/消失或 |GAP|/A ≥ gap-pct）视为显著变化。
- 报告共 12 节（口径说明见末节）：测试信息 / 结论摘要 / 健康标准对照 / 整机内存 / 层级内存（RSS+PSS）/ 进程 GAP Top20 / 独有进程与层级变化 / 自研与 Google / 疑似泄漏 / 进程事件 / 内存压力 / 口径说明。

## 开发

见 [AGENTS.md](AGENTS.md)。
