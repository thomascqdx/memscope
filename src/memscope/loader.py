"""输入加载：平台 zip / rar / 已解压目录 → Dataset。

zip 内文件名是 GBK 编码且未置 UTF-8 标志位，需 cp437→GBK 解码。
rar 通过系统 tar（Windows 10 1803+ 自带 bsdtar）先解出（剔除 debuglogger）。
嵌套 zip（tw_rpt_*_rounds.zip / mobilelog.zip）materialize 到临时目录后按文件打开，
全程流式，不把数 GB 的包读进内存；临时目录在加载完成后清理。
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .model import Dataset, RoundData
from .parsers import device as dev
from .parsers import dumpsys, procmeminfo, procstatus, thresholds as thr
from .parsers import vmstat as vms
from .parsers.events import dedupe_lines, parse_events


class LoadError(Exception):
    pass


_KIND_CN = {'static': '静态', 'dynamic': '动态'}


def check_test_type(ds: Dataset, expected: str = 'static'):
    """校验 Test Type（大小写/首尾空白不敏感）。

    返回错误消息字符串；匹配或缺失时返回 None。缺失时附加一条 warning。
    """
    v = ds.device.get('Test Type')
    if v is None or not str(v).strip():
        ds.warnings.append('device_info.txt 中无 Test Type 字段，无法校验测试类型')
        return None
    if str(v).strip().lower() != expected.lower():
        return (f'{ds.label} 的 Test Type 为 {str(v).strip()}，'
                f'不是{_KIND_CN.get(expected, expected)}内存测试包，请使用对应子命令')
    return None


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

    def materialize(self, name: str, tmp: Path) -> Path:
        """把成员落盘为真实文件并返回路径（嵌套 zip 用）。"""
        raise NotImplementedError

    def close(self):
        pass


class ZipSource(_Source):
    """直接打开 zip 文件（流式，不整块读入内存）。"""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)
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

    def materialize(self, name, tmp):
        zi = self.map[name]
        out = Path(tmp) / Path(zi.filename).name
        with self.zf.open(zi) as fin, open(out, 'wb') as fout:
            shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
        return out

    def close(self):
        self.zf.close()


class DirSource(_Source):
    def __init__(self, root: Path):
        self.root = root
        self.map = {p.relative_to(root).as_posix(): p
                    for p in root.rglob('*') if p.is_file()}

    def list(self):
        return list(self.map)

    def read(self, name):
        return self.map[name].read_bytes()

    def materialize(self, name, tmp):
        return self.map[name]  # 已在磁盘上，直接返回原路径


def _is_rar(path: Path) -> bool:
    if path.suffix.lower() == '.rar':
        return True
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'Rar!'
    except OSError:
        return False


def _extract_rar(path: Path, dst: Path) -> Path:
    """系统 tar 解 rar（剔除 debuglogger），返回解出的根目录。"""
    dst.mkdir(parents=True, exist_ok=True)
    cmd = ['tar', '-xf', str(path), '-C', str(dst), '--exclude', '*/debuglogger/*']
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    except FileNotFoundError:
        raise LoadError('解压 rar 需要系统 tar（Windows 10 1803+ 自带 bsdtar）：未找到 tar 命令')
    if p.returncode != 0:
        raise LoadError('解压 rar 需要系统 tar（Windows 10 1803+ 自带 bsdtar）：'
                        f'tar 返回 {p.returncode}: {(p.stderr or "").strip()[:200]}')
    subs = [x for x in dst.iterdir() if x.is_dir()]
    files = [x for x in dst.iterdir() if x.is_file()]
    if len(subs) == 1 and not files:
        return subs[0]  # 单顶层目录，与 zip 前缀剥离一致
    return dst


def open_source(path, tmp: Path) -> _Source:
    path = Path(path)
    if path.is_dir():
        return DirSource(path)
    if path.is_file() and zipfile.is_zipfile(path):
        return ZipSource(path)
    if path.is_file() and _is_rar(path):
        return DirSource(_extract_rar(path, Path(tmp) / 'rar_x'))
    raise LoadError(f'无法识别的输入（应为平台 zip/rar 或已解压目录）: {path}')


def _decode(b: bytes) -> str:
    return b.decode('utf-8', errors='replace')


_STATIC_TAGS = ('am_proc_start', 'am_kill', 'am_proc_died', 'boot_progress_start')
_DYNAMIC_TAGS = ('killinfo', 'am_anr', 'am_crash', 'am_low_memory')
_ROUND_FILES = ('dumpsys_meminfo.txt', 'procStatus_sortedByLevel.txt',
                'proc_meminfo.txt', 'proc_vmstat.txt')


def load_dataset(path, label: str, kind: str = 'static', tmpdir=None) -> Dataset:
    ds = Dataset(label=label, path=str(path))
    tmp = tempfile.TemporaryDirectory(prefix='memscope_', dir=tmpdir)
    tmp_path = Path(tmp.name)
    opened = []  # 需在清理临时目录前关闭的 ZipFile
    try:
        src = open_source(path, tmp_path)
        if isinstance(src, ZipSource):
            opened.append(src.zf)
        _load(src, ds, kind, tmp_path, opened)
    finally:
        for zf in opened:
            try:
                zf.close()
            except Exception:
                pass
        tmp.cleanup()
    return ds


def _load(src: _Source, ds: Dataset, kind: str, tmp_path: Path, opened: list):
    dynamic = kind == 'dynamic'
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

    # 轮次数据：已解压的 round_N/ 目录，或嵌套 tw_rpt_*_rounds.zip
    rfiles = _round_files(src, names, tmp_path, opened)
    failed = {}
    for idx in sorted(rfiles):
        rd, reason = _load_round(idx, rfiles[idx], tmap.get(idx, (None, None)),
                                 dynamic)
        if reason is None:
            ds.rounds.append(rd)
        else:
            failed[idx] = reason
    if not ds.rounds:
        ds.warnings.append('未找到任何 round_N 轮次数据')

    # 平台 failed_rounds.log：`[NNNN] … 原因：xxx`（补充没有轮次目录的失败轮）
    f = find('failed_rounds.log')
    if f:
        for m in re.finditer(r'\[(\d+)\].*?原因[：:]\s*(.+)', _decode(src.read(f))):
            failed.setdefault(int(m.group(1)), m.group(2).strip())
    ds.failed_rounds = [{'index': i, 'reason': failed[i]} for i in sorted(failed)]
    if ds.failed_rounds:
        items = ds.failed_rounds[:10]
        more = f' 等共 {len(ds.failed_rounds)}' if len(ds.failed_rounds) > 10 else ''
        ds.warnings.append(
            f'{len(ds.failed_rounds)} 轮无效：'
            + ', '.join(f"{d['index']}({d['reason']})" for d in items) + more)

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
                lines = _event_lines(src, f, tmp_path, opened, dynamic)
                if not lines:
                    ds.warnings.append('mobilelog.zip 中无 events_log')
                r1 = ds.rounds[0] if ds.rounds else None
                ds.events, ds.boot_time = parse_events(
                    dedupe_lines(lines),
                    year if year is not None else r1.start.year,
                    r1.start if r1 else None,
                    r1.uptime_ms if r1 else None,
                    extra=dynamic)
            except Exception as e:  # noqa: BLE001
                ds.warnings.append(f'mobilelog.zip 解析失败: {e}')
    else:
        ds.warnings.append('缺少 mobilelog.zip（无启动/唤醒事件）')

    if dynamic:
        _load_platform(src, names, ds)
        _detect_reboots(ds)


def _load_round(idx: int, got: dict, times, dynamic: bool):
    """解析单轮；返回 (RoundData, reason)。reason 非 None 表示无效轮。"""
    rd = RoundData(index=idx)
    rd.start, rd.end = times
    try:
        if 'dumpsys_meminfo.txt' not in got:
            return rd, '缺少 dumpsys_meminfo.txt'
        if 'procStatus_sortedByLevel.txt' not in got:
            return rd, '缺少 procStatus_sortedByLevel.txt'
        d = dumpsys.parse_dumpsys(_decode(got['dumpsys_meminfo.txt']()))
        if not d['pss']:
            return rd, 'dumpsys meminfo 未获取到有效结果'
        rd.uptime_ms = d['uptime_ms']
        rd.pss = d['pss']
        rd.tail = d['tail']
        rd.rss = procstatus.parse_proc_status(
            _decode(got['procStatus_sortedByLevel.txt']()))
        if 'proc_meminfo.txt' in got:
            rd.meminfo = procmeminfo.parse_proc_meminfo(
                _decode(got['proc_meminfo.txt']()))
        if dynamic and 'proc_vmstat.txt' in got:
            rd.vmstat = vms.parse_vmstat(_decode(got['proc_vmstat.txt']()))
    except Exception as e:  # noqa: BLE001 - 单轮异常 → 无效轮
        return rd, f'解析失败: {str(e)[:80]}'
    return rd, None


def _round_files(src: _Source, names, tmp_path: Path, opened: list):
    """{round_idx: {fname: thunk()->bytes}}。round_N/ 目录或嵌套 rounds.zip。"""
    wanted = set(_ROUND_FILES)
    pat = re.compile(r'(?:^|/)round_(\d+)/([^/]+)$')
    out = {}
    for n in names:
        m = pat.search(n)
        if m and m.group(2) in wanted:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = (
                lambda n=n: src.read(n))
    if out:
        return out
    # 嵌套 tw_rpt_*_rounds.zip：materialize 后按文件打开
    zips = [n for n in names if re.search(r'tw_rpt_.*rounds\.zip$', n)]
    if not zips:
        return out
    p = src.materialize(zips[0], tmp_path)
    nz = zipfile.ZipFile(p)
    opened.append(nz)
    for zi in nz.infolist():
        if zi.is_dir():
            continue
        n = zip_name(zi)
        m = pat.search(n)
        if m and m.group(2) in wanted:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = (
                lambda zi=zi: nz.read(zi))
    return out


def _event_lines(src: _Source, name: str, tmp_path: Path, opened: list,
                 dynamic: bool):
    """materialize mobilelog.zip → 只保留目标 tag 的行（去重前预过滤）。"""
    p = src.materialize(name, tmp_path)
    mz = zipfile.ZipFile(p)
    opened.append(mz)
    tags = _STATIC_TAGS + _DYNAMIC_TAGS if dynamic else _STATIC_TAGS
    out = []
    for zi in mz.infolist():
        if zi.is_dir() or 'events_log' not in zip_name(zi):
            continue
        for line in _decode(mz.read(zi)).splitlines():
            if any(t in line for t in tags):
                out.append(line)
    return out


def _load_platform(src: _Source, names, ds: Dataset):
    """顶层 *Exception_Summary_Report.xlsx → ds.platform。缺失/失败 → {} + warning。"""
    hits = sorted(n for n in names if n.endswith('Exception_Summary_Report.xlsx'))
    hits = [n for n in hits if '/' not in n] or hits  # 优先顶层
    if not hits:
        ds.warnings.append('缺少 *Exception_Summary_Report.xlsx（无平台判定）')
        return
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(src.read(hits[0])), read_only=True)
        row_iter = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        for row in row_iter:
            if not row or row[0] is None:
                continue
            k = str(row[0]).strip()
            if k == '内存标准动态测试结果：':
                ds.platform['dynamic_result'] = (str(row[1]).strip()
                                               if len(row) > 1 and row[1] is not None else '')
            elif k == '检测工具版本：':
                ds.platform['tool_version'] = (str(row[1]).strip()
                                             if len(row) > 1 and row[1] is not None else '')
        wb.close()
    except Exception as e:  # noqa: BLE001
        ds.warnings.append(f'Exception_Summary_Report.xlsx 解析失败: {e}')


def _detect_reboots(ds: Dataset):
    """相邻有效轮 uptime_ms 变小 → 判定期间发生重启。"""
    prev = None
    for rd in ds.rounds:
        if rd.uptime_ms is None:
            continue
        if prev is not None and rd.uptime_ms < prev.uptime_ms:
            ds.reboots.append({'after_round': prev.index, 'round': rd.index})
        prev = rd
