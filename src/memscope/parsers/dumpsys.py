"""解析 round_N/dumpsys_meminfo.txt：PSS by OOM 区段、尾部汇总、Uptime。"""
from __future__ import annotations

import re

_KB = 1024.0
_NUM = r'(-?[\d,]+)K'


def _num(s: str) -> float:
    return int(s.replace(',', '')) / _KB


_OOM_LINE = re.compile(r'^( *)([\d,]+)K: (.+?)\s*(?:\(\s*([\d,]+)K in swap\))?\s*$')
_PID = re.compile(r'^(.*?) \(pid (\d+)(?: / [^)]*)?\)')


def parse_dumpsys(text: str) -> dict:
    lines = text.splitlines()
    uptime_ms = None
    for l in lines[:10]:
        m = re.search(r'Uptime:\s*(\d+)', l)
        if m:
            uptime_ms = int(m.group(1))
            break
    pss, pids = _parse_pss_oom(lines)
    return {
        'uptime_ms': uptime_ms,
        'pss': pss,
        'pids': pids,
        'tail': _parse_tail(lines),
    }


def _parse_pss_oom(lines: list):
    """返回 ({level: {'total': mb, 'procs': {name: mb}}}, {pid: name})。

    level 行缩进 < 8；进程行缩进 >= 8，名称后接 `(pid N)`、`(pid N / activities)`，
    且 `(in swap)` 括注可能紧贴 `)`。同名多 pid 相加；pids 记录 pid→进程名。
    """
    title = 'Total PSS by OOM adjustment:'
    try:
        s = lines.index(title)
    except ValueError:
        return {}, {}
    out = {}
    pids = {}
    cur = None
    for l in lines[s + 1:]:
        if not l.strip():
            break
        m = _OOM_LINE.match(l)
        if not m:
            continue
        ind, v, name, _sw = m.groups()
        if len(ind) < 8:
            level = name.strip()
            cur = {'total': _num(v), 'procs': {}}
            out[level] = cur
        elif cur is not None:
            pm = _PID.match(name)
            if pm:
                nm = pm.group(1).strip()
                pids[int(pm.group(2))] = nm
                cur['procs'][nm] = cur['procs'].get(nm, 0.0) + _num(v)
    return out, pids


def _parse_tail(lines: list) -> dict:
    try:
        i = lines.index('Global Mapped Bitmaps:')
    except ValueError:
        i = next((j for j, l in enumerate(lines)
                  if re.match(r'\s*Total RAM:', l)), None)
        if i is None:
            return {}
    t = '\n'.join(lines[i:])

    def g(p):
        m = re.search(p, t)
        return _num(m.group(1)) if m else None

    return {
        'Total RAM': g(r'Total RAM:\s*' + _NUM),
        'Free RAM': g(r'Free RAM:\s*' + _NUM),
        'cached pss': g(_NUM + r' cached pss'),
        'cached kernel': g(_NUM + r' cached kernel'),
        'free': g(_NUM + r' free\)'),
        'Used RAM': g(r'Used RAM:\s*' + _NUM),
        'used pss': g(_NUM + r' used pss'),
        'kernel': g(_NUM + r' kernel\)'),
        'Lost RAM': g(r'Lost RAM:\s*' + _NUM),
        'ZRAM physical': g(r'ZRAM:\s*' + _NUM),
        'in swap': g(r'for\s*' + _NUM + r' in swap'),
        'DMA-BUF': g(r'DMA-BUF:\s*' + _NUM),
        'GPU': g(r'GPU:\s*' + _NUM),
    }
