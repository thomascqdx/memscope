# memscope

Android 内存健康测试包（静态/动态）A/B 对比工具：读取两个平台测试包（zip/rar/解压目录），生成三份结果：

- `report.md` — 中文对比报告
- `report.html` — 单文件离线交互报告（内嵌 ECharts，支持 PSS/RSS 切换、Tab/进程路由）
- `data.xlsx` — 全部明细数据

## 安装

```powershell
pip install -e .
```

依赖：`openpyxl`、`xlrd`（读取平台 xls 阈值表）。

## 使用

```powershell
memscope static compare <基线A.zip> <对比B.zip> [-o 输出目录] [--labels A B] \
    [--whitelist 白名单.txt] [--prefixes 前缀.txt] \
    [--gap-abs 5] [--gap-pct 10] [--growth-slope 1] [--sys-growth-slope 5] [--growth-r2 0.6]

memscope dynamic compare <基线A.(zip|rar)> <对比B.(zip|rar)> [-o 输出目录] [--labels A B] \
    [--tmpdir 临时目录] [--leak-pct 20] [--leak-r2 0.3] [--sys-leak-delta 100] [--rate-pct 20]
```

- `static`：静态内存测试包；`dynamic`：动态内存测试包（monkey 长衰，默认 RSS 口径，小时时间轴，含 LMK/ANR/crash/重启/vmstat 内存压力/疑似泄漏分析）。
- 输入为平台 `内存健康测试报告_*`（zip/rar/解压目录）；rar 依赖系统 `tar`（Win10 1803+ 自带 bsdtar），解包时剔除 debuglogger。
- 嵌套 rounds/mobilelog zip 物化到临时目录（峰值约单包 5GB），`--tmpdir` 可改临时目录位置。
- 动态阈值见 `--gap-abs/--gap-pct/--resident/--leak-delta/--leak-pct/--leak-r2/--sys-leak-delta/--rate-pct`。
- 默认输出到 `out/<static|dynamic>/<A标签>_vs_<B标签>/`；标签默认取 `Rom Version`，相同则追加 `#SN`。
- 自研进程判定：`--whitelist` 优先（命中记 `whitelist`），否则按 `--prefixes`（默认 `config/inhouse_prefixes.txt`）前缀匹配。

## 构建单文件 exe

```bat
build_exe.bat
```

在隔离环境 `.venv-build\` 中安装 PyInstaller 并打包，产物为 `dist\memscope.exe`。用法与 CLI 相同：

```powershell
memscope.exe static compare A.zip B.zip -o outdir
```

## 口径速览

- **PSS** 来自 dumpsys meminfo OOM 段（含 swap），为主口径；**RSS** 来自 procStatus 的 VmRSS+VmSwap，OomScore 映射层级，用于标准对照。
- 出现次数 = 非空轮数；轮次均值 = 非空求和 / 总轮数（缺席记 0）；数值均值 = 仅非空轮均值。
- GAP = B − A（轮次均值）；阈值：|GAP| ≥ gap-abs 且（新增/消失或 |GAP|/A ≥ gap-pct）判为差异显著。
- 详见报告第 12 节「口径说明」。

## 开发

见 [AGENTS.md](AGENTS.md)。
