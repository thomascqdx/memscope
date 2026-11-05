"""解析 round_N/dumpsys_meminfo_<proc>[_<pid>].txt：明细表 Pss Total 列（MB）。"""
from __future__ import annotations

import re

_KB = 1024.0

_HDR = re.compile(r'\*\*\s*MEMINFO in pid (\d+) \[(.*?)\]\s*\*\*')
_DASH = re.compile(r'^\s*-{3,}')
# 行 = 行名（可含空格/点）+ 至少两个空格 + 第一列数字（Pss Total）
_ROW = re.compile(r'^\s*(\S.*?)\s{2,}(-?[\d,]+)(?:\s|$)')


def parse_app_meminfo(text: str) -> dict | None:
    """{'pid': int|None, 'name': str|None, 'rows': {row_name: mb}}。

    无明细表（无 TOTAL 行）→ None（无效文件，如 'No process found' 桩）。
    """
    pid = name = None
    m = _HDR.search(text)
    if m:
        pid = int(m.group(1))
        name = m.group(2).strip()

    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if _DASH.match(l):
            # 明细表分隔线：前面两行内应有 Pss 表头
            if any('Pss' in lines[j] for j in range(max(0, i - 2), i)):
                start = i + 1
                break
    if start is None:
        return None
    rows = {}
    for l in lines[start:]:
        if not l.strip():
            break
        m = _ROW.match(l)
        if not m:
            break
        rows[re.sub(r'\s+', ' ', m.group(1).strip())] = (
            int(m.group(2).replace(',', '')) / _KB)
    if 'TOTAL' not in rows:
        return None
    return {'pid': pid, 'name': name, 'rows': rows}
