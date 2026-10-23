"""解析 device_info.txt 与 round_info.txt。正则只依赖 ASCII 片段，对编码不敏感。"""
from __future__ import annotations

import re
from datetime import datetime

_TS = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def parse_device_info(text: str):
    """返回 (fields dict, packages list)。Package List: 之后每行 `package:<name>`。"""
    head, _, tail = text.partition('Package List')
    fields = dict(re.findall(r'^([A-Za-z][\w ]*):\s*(.+?)\s*$', head, re.M))
    packages = sorted(set(re.findall(r'package:(\S+)', tail)))
    return fields, packages


def parse_round_info(text: str):
    """每行 `[NNNN]  开始时间：..  结束时间：..` → [(index, start, end)]，按 index 排序。"""
    out = []
    for line in text.splitlines():
        m = re.match(r'\s*\[(\d+)\]', line)
        if not m:
            continue
        ts = _TS.findall(line)
        start = datetime.strptime(ts[0], '%Y-%m-%d %H:%M:%S') if len(ts) > 0 else None
        end = datetime.strptime(ts[1], '%Y-%m-%d %H:%M:%S') if len(ts) > 1 else None
        out.append((int(m.group(1)), start, end))
    out.sort(key=lambda x: x[0])
    return out
