"""解析 mobilelog 中的 events_log 行：am_proc_start / am_kill / am_proc_died /
boot_progress_start，并按启动时间划分阶段（boot / test，上一轮开机事件丢弃）。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..model import Event

_LINE = re.compile(
    r'(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.(\d+)\s+\d+\s+\d+ I\s+(\w+)\s*:\s*(.*)$')
_CALLER = re.compile(r'caller=([^/,\]]+)')
_COMP = re.compile(r'\{([^}]*)\}')


def _parse_line(line: str, year: int):
    """返回 (datetime, tag, body)；非目标行返回 None。"""
    m = _LINE.match(line.strip())
    if not m:
        return None
    md, hms, frac, tag, body = m.groups()
    try:
        t = datetime.strptime(f'{year}-{md} {hms}', '%Y-%m-%d %H:%M:%S')
        t = t.replace(microsecond=int(frac[:6].ljust(6, '0')))
    except ValueError:
        return None
    return t, tag, body


def _int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def parse_events(lines, year: int, round1_start, round1_uptime_ms, extra: bool = False):
    """lines: 已去重的原始日志行。返回 (events, boot_time)。

    阶段：t < boot_time → 丢弃；boot_time <= t < round1_start → 'boot'；否则 'test'。
    boot_time = 满足 <= round1_start 的最近一次 boot（event_t − boot_progress_start 毫秒）；
    无记录时回退为 round1_start − 第 1 轮 dumpsys Uptime。
    extra=True 时额外解析 killinfo/am_anr/am_crash/am_low_memory（统一开启）。
    """
    boots = []   # boot 事件的原始 uptime_ms，含事件时刻
    evs = []
    for line in lines:
        p = _parse_line(line, year)
        if not p:
            continue
        t, tag, body = p
        if tag == 'boot_progress_start':
            m = re.match(r'\s*(\d+)', body)
            if m:
                boots.append((t, int(m.group(1))))
            continue
        b = body.strip()
        if b.startswith('['):
            b = b[1:-1] if b.endswith(']') else b[1:]
        f = b.split(',')
        if tag == 'am_proc_start' and len(f) >= 5:
            cm = _CALLER.search(body)
            comp = _COMP.search(body)
            evs.append(Event(
                t=t, kind='start', proc=f[3], reason=f[4],
                comp=comp.group(1) if comp else ','.join(f[5:]),
                caller=cm.group(1) if cm else '',
                pid=_int(f[1])))
        elif tag == 'am_kill' and len(f) >= 4:
            rss = _int(f[5]) if len(f) > 5 else None
            evs.append(Event(t=t, kind='kill', proc=f[2],
                             adj=f[3], reason=f[4] if len(f) > 4 else '',
                             pid=_int(f[1]),
                             rss_mb=(rss / 1024.0) if rss is not None else None))
        elif tag == 'am_proc_died' and len(f) >= 4:
            evs.append(Event(t=t, kind='died', proc=f[2], adj=f[3],
                             pid=_int(f[1])))
        elif extra and tag == 'killinfo' and len(f) >= 6:
            # [pid,uid,oom_adj,min_oom_score,rss_kb,kill_reason,...]
            pid, rss, code = _int(f[0]), _int(f[4]), _int(f[5])
            if pid is None:
                continue
            evs.append(Event(t=t, kind='lmk', pid=pid, adj=str(f[2]),
                             rss_mb=(rss / 1024.0) if rss is not None else None,
                             code=code))
        elif extra and tag == 'am_anr' and len(f) >= 5:
            evs.append(Event(t=t, kind='anr', proc=f[2], pid=_int(f[1]),
                             reason=','.join(f[4:])[:200]))
        elif extra and tag == 'am_crash' and len(f) >= 5:
            msg = ','.join(f[5:]) if len(f) > 5 else ''
            evs.append(Event(t=t, kind='crash', proc=f[2], pid=_int(f[0]),
                             reason=(f[4] + ': ' + msg)[:200]))
        elif extra and tag == 'am_low_memory':
            evs.append(Event(t=t, kind='lowmem'))

    boot_time = None
    cands = [t - timedelta(milliseconds=ms)
             for t, ms in boots if round1_start is None or t - timedelta(milliseconds=ms) <= round1_start]
    if cands:
        boot_time = max(cands)
    elif round1_start is not None and round1_uptime_ms is not None:
        boot_time = round1_start - timedelta(milliseconds=round1_uptime_ms)

    out = []
    for e in evs:
        if boot_time is not None and e.t < boot_time:
            continue  # 上一轮开机
        if boot_time is not None and round1_start is not None and e.t < round1_start:
            e.phase = 'boot'
        else:
            e.phase = 'test'
        out.append(e)
    out.sort(key=lambda e: e.t)
    if extra:
        _resolve_lmk_names(out)
    return out, boot_time


def _resolve_lmk_names(events):
    """为 lmk 事件回填进程名：同 pid 最近一条 t<=lmk.t 的 start；
    否则同 pid 在 lmk 之后 10 秒内的第一条 died/kill；否则 pid:<pid>。"""
    import bisect
    starts, exits = {}, {}
    for e in events:
        if e.pid is None:
            continue
        if e.kind == 'start' and e.proc:
            starts.setdefault(e.pid, []).append(e)
        elif e.kind in ('died', 'kill') and e.proc:
            exits.setdefault(e.pid, []).append(e)
    for d in (starts, exits):
        for v in d.values():
            v.sort(key=lambda e: e.t)
    for e in events:
        if e.kind != 'lmk':
            continue
        proc = None
        st = starts.get(e.pid)
        if st:
            i = bisect.bisect_right([x.t for x in st], e.t) - 1
            if i >= 0:
                proc = st[i].proc
        if proc is None:
            ex = exits.get(e.pid)
            if ex:
                lim = e.t + timedelta(seconds=10)
                i = bisect.bisect_left([x.t for x in ex], e.t)
                if i < len(ex) and ex[i].t <= lim:
                    proc = ex[i].proc
        e.proc = proc if proc is not None else f'pid:{e.pid}'


def dedupe_lines(lines):
    """多份 events_log 可能含相同行，整体去重保序。"""
    seen = set()
    for l in lines:
        if l in seen:
            continue
        seen.add(l)
        yield l
