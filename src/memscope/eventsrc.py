"""事件日志源选择：按存在性取第一个可用源（与芯片平台无关）。

优先级：mtk_mobilelog（mobilelog.zip）→ mtk_debuglogger
（debuglogger/mobilelog/**/events_log*）→ unisoc_ylog（ylog/ap/*.ylog
经 open_concat 流式解码）→ bugreport（顶层 bugreport-*.zip 的
EVENT LOG 区段，只覆盖测试末段）。
"""
from __future__ import annotations

import gzip
import io
import re
import zipfile
from datetime import timedelta
from pathlib import Path

from .parsers import ylog as ylogp
from .parsers.events import dedupe_lines, parse_events

_STATIC_TAGS = ('am_proc_start', 'am_kill', 'am_proc_died', 'boot_progress_start')
_DYNAMIC_TAGS = ('killinfo', 'am_anr', 'am_crash', 'am_low_memory')
TAGS = _STATIC_TAGS + _DYNAMIC_TAGS
_BTAGS = tuple(t.encode() for t in TAGS)


def _decode(b: bytes) -> str:
    return b.decode('utf-8', errors='replace')


def _find(names, suffix):
    hits = sorted(n for n in names if n == suffix or n.endswith('/' + suffix))
    return hits[0] if hits else None


def load_events(src, ds, names, tmp_path, opened, year):
    """填充 ds.events / ds.boot_time / ds.event_source（失败只记 warning）。"""
    kind, lines, nfiles, ystats = _read_lines(src, ds, names, tmp_path, opened)
    ds.event_source = {'kind': kind, 'files': nfiles, 'lines': len(lines),
                       'events': 0, 'first': None, 'last': None,
                       'ylog': ystats}
    if kind is None:
        ds.warnings.append(
            '无事件日志（mobilelog/debuglogger/ylog/bugreport 均未找到）')
        return
    if kind == 'bugreport':
        ds.warnings.append('事件仅来自 bugreport，覆盖测试末段')
    _chip_source_check(ds, kind)
    if ystats:
        if ystats.get('resync') or ystats.get('zerr'):
            ds.warnings.append(
                'ylog 流存在重同步/解压异常'
                f"（resync={ystats.get('resync', 0)}, "
                f"zerr={ystats.get('zerr', 0)}）")
        if ystats.get('version') != '5.0.0':
            ds.warnings.append(
                f"ylog 版本 {ystats.get('version') or '未知'} 未经验证，"
                '如结果异常可用包内 ylog/ap/analyzer.py 手动解压核对')
    if year is None:
        ds.warnings.append('无法确定事件日志年份，已跳过事件解析')
        return
    try:
        uniq = list(dedupe_lines(lines))
        offset = (timedelta(seconds=ds.clock_offset_s)
                  if ds.clock_offset_s else None)
        r1 = ds.rounds[0] if ds.rounds else None
        ds.events, ds.boot_time = parse_events(
            uniq, year,
            r1.start if r1 else None,
            r1.uptime_ms if r1 else None,
            extra=True, offset=offset)
    except Exception as e:  # noqa: BLE001
        ds.warnings.append(f'{kind} 事件解析失败: {e}')
        return
    ds.event_source['lines'] = len(uniq)
    ds.event_source['events'] = len(ds.events)
    test_ts = [e.t for e in ds.events if e.phase == 'test']
    if test_ts:
        ds.event_source['first'] = min(test_ts).isoformat(sep=' ')
        ds.event_source['last'] = max(test_ts).isoformat(sep=' ')


def _read_lines(src, ds, names, tmp_path, opened):
    """返回 (kind, lines, nfiles, ystats)；无可用源 → (None, [], 0, None)。"""
    f = _find(names, 'mobilelog.zip')
    if f:
        try:
            lines, n = mobilelog_lines(src, f, tmp_path, opened)
            if not lines:
                ds.warnings.append('mobilelog.zip 中无 events_log')
            return 'mtk_mobilelog', lines, n, None
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'mobilelog.zip 解析失败: {e}')
            return 'mtk_mobilelog', [], 0, None
    dbg = sorted(n for n in names
                 if n.startswith('debuglogger/mobilelog/')
                 and 'events_log' in Path(n).name)
    if dbg:
        try:
            return 'mtk_debuglogger', debuglogger_lines(src, dbg), \
                len(dbg), None
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'debuglogger 事件日志解析失败: {e}')
            return 'mtk_debuglogger', [], len(dbg), None
    yl = sorted(n for n in names
                if re.match(r'ylog/ap/[^/]*\.ylog$', n))
    if yl:
        stats = {}
        try:
            fh = src.open_concat(yl)
            lines = list(ylogp.iter_event_lines(fh, _BTAGS, stats))
            return 'unisoc_ylog', lines, len(yl), stats
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'ylog 解析失败: {e}')
            return 'unisoc_ylog', [], len(yl), stats
    br = sorted(n for n in names
                if '/' not in n and Path(n).name.startswith('bugreport')
                and n.endswith('.zip'))
    if br:
        try:
            lines = bugreport_lines(src, br[0], tmp_path, opened)
            if not lines:
                ds.warnings.append('bugreport 中无 EVENT LOG 事件')
            return 'bugreport', lines, 1, None
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'bugreport 解析失败: {e}')
            return 'bugreport', [], 1, None
    return None, [], 0, None


def _chip_source_check(ds, kind):
    chip = ds.chip
    if chip == 'unisoc' and kind.startswith('mtk'):
        ds.warnings.append(f'芯片平台 unisoc 与事件源 {kind} 不一致')
    elif chip == 'mtk' and kind == 'unisoc_ylog':
        ds.warnings.append(f'芯片平台 mtk 与事件源 {kind} 不一致')


def mobilelog_lines(src, name, tmp_path, opened):
    """mobilelog.zip 内所有 events_log 成员 → (过滤行, 成员数)。"""
    p = src.materialize(name, tmp_path)
    mz = zipfile.ZipFile(p)
    opened.append(mz)
    out, nfiles = [], 0
    for zi in mz.infolist():
        if zi.is_dir() or 'events_log' not in zi.filename:
            continue
        nfiles += 1
        for line in _decode(mz.read(zi)).splitlines():
            if any(t in line for t in TAGS):
                out.append(line)
    return out, nfiles


def debuglogger_lines(src, names):
    """debuglogger/mobilelog/**/events_log* 纯文本（.gz 解压）→ 过滤行。"""
    out = []
    for n in names:
        data = src.read(n)
        if n.endswith('.gz'):
            data = gzip.decompress(data)
        for line in _decode(data).splitlines():
            if any(t in line for t in TAGS):
                out.append(line)
    return out


def bugreport_lines(src, name, tmp_path, opened):
    """bugreport-*.zip 内 bugreport*.txt 的 EVENT LOG 区段 → 过滤行。"""
    p = src.materialize(name, tmp_path)
    bz = zipfile.ZipFile(p)
    opened.append(bz)
    out = []
    for zi in bz.infolist():
        if zi.is_dir():
            continue
        bn = Path(zi.filename).name
        if not (bn.startswith('bugreport') and bn.endswith('.txt')):
            continue
        in_sec = False
        with bz.open(zi) as fh:
            for raw in io.TextIOWrapper(fh, encoding='utf-8',
                                        errors='replace'):
                if raw.startswith('------ '):
                    in_sec = raw.startswith('------ EVENT LOG')
                    continue
                if in_sec and any(t in raw for t in TAGS):
                    out.append(raw.rstrip('\n'))
    return out
