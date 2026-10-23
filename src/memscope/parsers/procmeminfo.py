"""解析 round_N/proc_meminfo.txt（/proc/meminfo 快照，单位 kB → MB）。"""
from __future__ import annotations

import re

_KB = 1024.0
_LINE = re.compile(r'^(\w+(?:\(\w+\))?):\s+(\d+)\s*kB', re.M)


def parse_proc_meminfo(text: str) -> dict:
    out = {k: int(v) / _KB for k, v in _LINE.findall(text)}
    if 'SwapTotal' in out and 'SwapFree' in out:
        out['SwapUsed'] = out['SwapTotal'] - out['SwapFree']
    return out
