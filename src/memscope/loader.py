"""输入加载：平台 zip 或已解压目录 → Dataset。

zip 内文件名是 GBK 编码且未置 UTF-8 标志位，需 cp437→GBK 解码。
嵌套 zip（tw_rpt_*_rounds.zip / mobilelog.zip）在内存中读取，不落盘。
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from .model import Dataset, RoundData
from .parsers import device as dev
from .parsers import dumpsys, procmeminfo, procstatus, thresholds as thr
from .parsers.events import dedupe_lines, parse_events


class LoadError(Exception):
    pass


def zip_name(zi: zipfile.ZipInfo) -> str:
    if zi.flag_bits & 0x800:
        return zi.filename
    try:
        return zi.filename.encode('cp437').decode('gbk')
    except UnicodeDecodeError:
        return zi.filename


class _Source:
    """统一的只读文件视图：list() 返回相对路径（posix 风格），read() 读字节。"""
    def list(self):
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError


class ZipSource(_Source):
    def __init__(self, data: bytes):
        self.zf = zipfile.ZipFile(io.BytesIO(data))
        raw = {zip_name(zi): zi for zi in self.zf.infolist() if not zi.is_dir()}
        tops = {n.split('/')[0] for n in raw}
        prefix = tops.pop() + '/' if len(tops) == 1 else ''
        self.map = {}
        for n, zi in raw.items():
            rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
            if rel:
                self.map[rel] = zi

    def list(self):
        return list(self.map)

    def read(self, name):
        return self.zf.read(self.map[name])


class DirSource(_Source):
    def __init__(self, root: Path):
        self.root = root
        self.map = {p.relative_to(root).as_posix(): p
                    for p in root.rglob('*') if p.is_file()}

    def list(self):
        return list(self.map)

    def read(self, name):
        return self.map[name].read_bytes()


def open_source(path) -> _Source:
    path = Path(path)
    if path.is_dir():
        return DirSource(path)
    if path.is_file() and zipfile.is_zipfile(path):
        return ZipSource(path.read_bytes())
    raise LoadError(f'无法识别的输入（应为平台 zip 或已解压目录）: {path}')


def _decode(b: bytes) -> str:
    return b.decode('utf-8', errors='replace')


def load_dataset(path, label: str) -> Dataset:
    ds = Dataset(label=label, path=str(path))
    src = open_source(path)
    names = set(src.list())

    def find(suffix):
        hits = sorted(n for n in names if n == suffix or n.endswith('/' + suffix))
        return hits[0] if hits else None

    # device_info / round_info
    f = find('device_info.txt')
    if f:
        ds.device, ds.packages = dev.parse_device_info(_decode(src.read(f)))
    else:
        ds.warnings.append('缺少 device_info.txt')
    f = find('round_info.txt')
    round_times = dev.parse_round_info(_decode(src.read(f))) if f else []
    if not f:
        ds.warnings.append('缺少 round_info.txt')
    tmap = {i: (s, e) for i, s, e in round_times}

    # 轮次数据：已解压的 round_N/ 目录，或嵌套 tw_rpt_*_rounds.zip（懒加载三个目标文件）
    wanted = ('dumpsys_meminfo.txt', 'procStatus_sortedByLevel.txt', 'proc_meminfo.txt')
    rfiles = _round_files(src, names, ds)  # {round_idx: {fname: reader()}}
    for idx in sorted(rfiles):
        rd = RoundData(index=idx)
        st, en = tmap.get(idx, (None, None))
        rd.start, rd.end = st, en
        got = rfiles[idx]
        if 'dumpsys_meminfo.txt' in got:
            d = dumpsys.parse_dumpsys(_decode(got['dumpsys_meminfo.txt']()))
            rd.uptime_ms = d['uptime_ms']
            rd.pss = d['pss']
            rd.tail = d['tail']
        if 'procStatus_sortedByLevel.txt' in got:
            rd.rss = procstatus.parse_proc_status(_decode(got['procStatus_sortedByLevel.txt']()))
        if 'proc_meminfo.txt' in got:
            rd.meminfo = procmeminfo.parse_proc_meminfo(_decode(got['proc_meminfo.txt']()))
        ds.rounds.append(rd)
    if not ds.rounds:
        ds.warnings.append('未找到任何 round_N 轮次数据')

    # 阈值
    f = find('all_memory_data.xls')
    if f:
        try:
            ds.thresholds = thr.parse_thresholds(src.read(f))
        except Exception as e:  # noqa: BLE001 - 解析失败降级为空阈值
            ds.warnings.append(f'all_memory_data.xls 解析失败: {e}')
    else:
        ds.warnings.append('缺少 all_memory_data.xls（无健康标准阈值）')

    # 事件日志
    year = None
    for i, s, _e in round_times:
        if s:
            year = s.year
            break
    if year is None and ds.rounds and ds.rounds[0].start:
        year = ds.rounds[0].start.year
    f = find('mobilelog.zip')
    if f:
        if year is None and not (ds.rounds and ds.rounds[0].start):
            ds.warnings.append('无法确定事件日志年份，已跳过 mobilelog.zip')
        else:
            try:
                lines = _event_lines(src.read(f), ds)
                r1 = ds.rounds[0] if ds.rounds else None
                ds.events, ds.boot_time = parse_events(
                    dedupe_lines(lines),
                    year if year is not None else r1.start.year,
                    r1.start if r1 else None,
                    r1.uptime_ms if r1 else None)
            except Exception as e:  # noqa: BLE001
                ds.warnings.append(f'mobilelog.zip 解析失败: {e}')
    else:
        ds.warnings.append('缺少 mobilelog.zip（无启动/唤醒事件）')
    return ds


def _round_files(src: _Source, names, ds: Dataset):
    """{round_idx: {fname: thunk()->bytes}}，仅收集需要的三个文件。"""
    wanted = {'dumpsys_meminfo.txt', 'procStatus_sortedByLevel.txt', 'proc_meminfo.txt'}
    pat = re.compile(r'(?:^|/)round_(\d+)/([^/]+)$')
    out = {}
    for n in names:
        m = pat.search(n)
        if m and m.group(2) in wanted:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = (
                lambda n=n: src.read(n))
    if out:
        return out
    # 嵌套 tw_rpt_*_rounds.zip
    zips = [n for n in names if re.search(r'tw_rpt_.*rounds\.zip$', n)]
    if not zips:
        return out
    blob = src.read(zips[0])
    nz = zipfile.ZipFile(io.BytesIO(blob))
    for zi in nz.infolist():
        if zi.is_dir():
            continue
        n = zip_name(zi)
        m = pat.search(n)
        if m and m.group(2) in wanted:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = (
                lambda zi=zi: nz.read(zi))
    return out


def _event_lines(mobilelog_bytes: bytes, ds: Dataset):
    mz = zipfile.ZipFile(io.BytesIO(mobilelog_bytes))
    out = []
    for zi in mz.infolist():
        if zi.is_dir():
            continue
        if 'events_log' not in zip_name(zi):
            continue
        out.extend(_decode(mz.read(zi)).splitlines())
    if not out:
        ds.warnings.append('mobilelog.zip 中无 events_log')
    return out
