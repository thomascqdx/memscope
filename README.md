# memscope

Android 静态内存测试报告 A/B 对比工具：读取两个平台测试包（zip 或解压目录），生成三份结果：

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
```

- `static`：静态内存测试包（当前实现）；`dynamic`：动态内存测试包（占位，尚未实现）。
- 输入为平台 `内存健康测试报告_*.zip` 或其解压目录；`Test Type` 字段非 `static` 时拒绝运行。
- 默认输出到 `out/static/<A标签>_vs_<B标签>/`；标签默认取 `Rom Version`，相同则追加 `#SN`。
- 自研进程判定：`--whitelist` 优先（命中记 `whitelist`），否则按 `--prefixes`（默认 `config/inhouse_prefixes.txt`）前缀匹配。

## 口径速览

- **PSS** 来自 dumpsys meminfo OOM 段（含 swap），为主口径；**RSS** 来自 procStatus 的 VmRSS+VmSwap，OomScore 映射层级，用于标准对照。
- 出现次数 = 非空轮数；轮次均值 = 非空求和 / 总轮数（缺席记 0）；数值均值 = 仅非空轮均值。
- GAP = B − A（轮次均值）；阈值：|GAP| ≥ gap-abs 且（新增/消失或 |GAP|/A ≥ gap-pct）判为差异显著。
- 详见报告第 12 节「口径说明」。

## 开发

见 [AGENTS.md](AGENTS.md)。
