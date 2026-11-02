"""解析 round_N/procStatus_sortedByLevel.txt（RSS 口径）。

LevelName 字段不可信（合并了 Persistent 与 Persistent Service），按 OomScore 映射层级。
"""
from __future__ import annotations

import re

_KB = 1024.0
# 兼容两种行格式（LevelName 可含空格，数值可带小数）：
# 3.0: ProcName:/system/bin/init Pid:1 OomScore:-1000 LevelName:Native VmRSS+VmSwap:9992KB VmSwap:3388KB ...
# 2.0: ProcName: zygote64  Pid: 627  OomScore: -1000 LevelName: Native VmRSS + VmSwap:  121268.00KB VmSwap:  34684KB ...
_LINE = re.compile(
    r'ProcName:\s*(.+?)\s+Pid:\s*(\d+)\s+OomScore:\s*(-?\d+)\s+'
    r'LevelName:\s*(.+?)\s+VmRSS\s*\+\s*VmSwap:\s*([\d.]+)\s*KB\s+'
    r'VmSwap:\s*([\d.]+)\s*KB')


def oom_to_level(score: int) -> str:
    if score < -900:
        return 'Native'
    if score <= -801:
        return 'System'
    if score <= -701:
        return 'Persistent'
    if score <= -1:
        return 'Persistent Service'
    if score <= 99:
        return 'Foreground'
    if score <= 199:
        return 'Visible'
    if score <= 224:
        return 'Perceptible'
    if score <= 249:
        return 'Perceptible Medium'
    if score <= 299:
        return 'Perceptible Low'
    if score <= 399:
        return 'Backup'
    if score <= 499:
        return 'Heavy Weight'
    if score <= 599:
        return 'A Services'
    if score <= 699:
        return 'Home'
    if score <= 799:
        return 'Previous'
    if score <= 899:
        return 'B Services'
    return 'Cached'


def parse_proc_status(text: str) -> dict:
    """返回 {level: {proc_name: mb}}。同名多 pid 相加。"""
    out: dict = {}
    for m in _LINE.finditer(text):
        name, _pid, score, _lname, rss, _sw = m.groups()
        level = oom_to_level(int(score))
        procs = out.setdefault(level, {})
        v = float(rss) / _KB
        procs[name] = procs.get(name, 0.0) + v
    return out
