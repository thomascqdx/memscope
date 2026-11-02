"""输入加载：平台 zip / rar / 已解压目录 → Dataset。

zip 内文件名是 GBK 编码且未置 UTF-8 标志位，需 cp437→GBK 解码。
rar 通过系统 tar（Windows 10 1803+ 自带 bsdtar）按白名单成员解出，
未解出的成员（ylog）经 tar -xOf 流式读取，不落盘。
嵌套 zip（tw_rpt_*_rounds.zip / mobilelog.zip / bugreport-*.zip）materialize
到临时目录后按文件打开；临时目录在加载完成后清理。

体验模型：3.0（MTK，device_info.txt/round_info.txt）与 2.0（Unisoc，
devinfo/ + 顶层 log）自动识别，亦可 --model/--platform 显式指定。
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import eventsrc
from .model import Dataset, RoundData
from .parsers import device as dev
from .parsers import dumpsys, procmeminfo, procstatus, thresholds as thr
from .parsers import v2info, v2summary
from .parsers import vmstat as vms


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


class _ConcatReader(io.RawIOBase):
    """顺序拼接多个成员的只读流（openers: () -> binary file-like）。"""

    def __init__(self, openers):
        self._openers = list(openers)
        self._i = 0
        self._cur = None

    def readable(self):
        return True

    def read(self, n=-1):
        out = bytearray()
        while n < 0 or len(out) < n:
            if self._cur is None:
                if self._i >= len(self._openers):
                    break
                self._cur = self._openers[self._i]()
                self._i += 1
            want = (1 << 20) if n < 0 else n - len(out)
            c = self._cur.read(want)
            if not c:
                self._cur.close()
                self._cur = None
                continue
            out += c
        return bytes(out)

    def close(self):
        if self._cur is not None:
            self._cur.close()
            self._cur = None
        super().close()


class _Source:
    """统一的只读文件视图：list() 返回相对路径（posix 风格），read() 读字节。"""
    def list(self):
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError

    def materialize(self, name: str, tmp: Path) -> Path:
        """把成员落盘为真实文件并返回路径（嵌套 zip 用）。"""
        raise NotImplementedError

    def open_concat(self, names: list):
        """按序拼接多个成员 → 只读二进制流。"""
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

    def open_concat(self, names):
        return _ConcatReader(
            [lambda zi=self.map[n]: self.zf.open(zi) for n in names])

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

    def open_concat(self, names):
        return _ConcatReader(
            [lambda p=self.map[n]: open(p, 'rb') for n in names])


# rar 白名单成员模式（* 在 bsdtar 中可跨 /；同时兼容有无单一顶层目录）
_RAR_WHITELIST = [
    '*device_info.txt', '*round_info.txt', '*failed_rounds.log',
    '*all_memory_data.xls', '*Exception_Summary_Report.xlsx',
    '*mobilelog.zip', '*tw_rpt_*rounds.zip',
    'log', '*/log',
    '*lmkcnt_start.txt', '*lmkcnt_end.txt',
    '*devinfo/deviceinfo.txt', '*devinfo/getprop.txt',
    '*devinfo/package_list.txt',
    '*bugreport-*.zip',
    '*round_*/dumpsys_meminfo.txt', '*round_*/procStatus_sortedByLevel.txt',
    '*round_*/proc_meminfo.txt', '*round_*/proc_vmstat.txt',
    '*round_*/tran_mem_debug/proc_vmstat.txt',
    '*debuglogger/mobilelog/*events_log*',
]


class RarSource(_Source):
    """rar：白名单成员解到 tmp/rar_x 后按目录读取；ylog 等大文件流式读。"""

    def __init__(self, path, tmp: Path):
        self.path = Path(path)
        try:
            lp = subprocess.run(['tar', '-tf', str(self.path)],
                                capture_output=True, text=True,
                                errors='replace')
        except FileNotFoundError:
            raise LoadError('解压 rar 需要系统 tar（Windows 10 1803+ 自带 '
                            'bsdtar）：未找到 tar 命令')
        if lp.returncode != 0:
            raise LoadError('读取 rar 成员列表失败: '
                            f'{(lp.stderr or "").strip()[:200]}')
        members = [l.strip() for l in lp.stdout.splitlines() if l.strip()]
        tops = {m.split('/')[0] for m in members if '/' in m}
        prefix = tops.pop() + '/' if len(tops) == 1 else ''

        dst = Path(tmp) / 'rar_x'
        dst.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(['tar', '-xf', str(self.path), '-C', str(dst)]
                           + _RAR_WHITELIST,
                           capture_output=True, text=True, errors='replace')
        if p.returncode != 0:
            bad = [l for l in (p.stderr or '').splitlines()
                   if l.strip() and 'Not found in archive' not in l
                   and 'Error exit delayed' not in l]
            if bad:
                raise LoadError('解压 rar 需要系统 tar（Windows 10 1803+ 自带 '
                                'bsdtar）：tar 返回 '
                                f'{p.returncode}: {bad[0].strip()[:200]}')
        root = dst
        subs = [x for x in dst.iterdir() if x.is_dir()]
        files = [x for x in dst.iterdir() if x.is_file()]
        if len(subs) == 1 and not files:
            root = subs[0]
        self.dir = DirSource(root)
        names = set(self.dir.list())
        for m in members:  # 未解出的成员只登记名字（ylog 为纯 ASCII 路径）
            rel = m[len(prefix):] if prefix and m.startswith(prefix) else m
            if rel and not rel.endswith('/') and rel.endswith('.ylog'):
                names.add(rel)
        self._names = sorted(names)

    def list(self):
        return self._names

    def read(self, name):
        return self.dir.read(name)

    def materialize(self, name, tmp):
        return self.dir.materialize(name, tmp)

    def open_concat(self, names):
        try:
            p = subprocess.Popen(
                ['tar', '-xOf', str(self.path)] + [f'*{n}' for n in names],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise LoadError('解压 rar 需要系统 tar（Windows 10 1803+ 自带 '
                            'bsdtar）：未找到 tar 命令')
        return p.stdout


def _is_rar(path: Path) -> bool:
    if path.suffix.lower() == '.rar':
        return True
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'Rar!'
    except OSError:
        return False


def open_source(path, tmp: Path) -> _Source:
    path = Path(path)
    if path.is_dir():
        return DirSource(path)
    if path.is_file() and zipfile.is_zipfile(path):
        return ZipSource(path)
    if path.is_file() and _is_rar(path):
        return RarSource(path, tmp)
    raise LoadError(f'无法识别的输入（应为平台 zip/rar 或已解压目录）: {path}')


def _decode(b: bytes) -> str:
    return b.decode('utf-8', errors='replace')


_STATIC_TAGS = ('am_proc_start', 'am_kill', 'am_proc_died', 'boot_progress_start')
_DYNAMIC_TAGS = ('killinfo', 'am_anr', 'am_crash', 'am_low_memory')
_ROUND_PAT = re.compile(
    r'(?:^|/)round_(\d+)/(?:tran_mem_debug/)?'
    r'(dumpsys_meminfo\.txt|procStatus_sortedByLevel\.txt|proc_meminfo\.txt'
    r'|proc_vmstat\.txt)$')


def detect_model(names) -> str | None:
    """'3.0' | '2.0' | None：按包内特征文件判定体验模型。"""
    if (any(n == 'device_info.txt' or n.endswith('/device_info.txt')
            for n in names)
            or any(n == 'round_info.txt' or n.endswith('/round_info.txt')
                   for n in names)
            or any(re.search(r'tw_rpt_.*rounds\.zip$', n) for n in names)):
        return '3.0'
    if (any(n == 'devinfo/deviceinfo.txt'
            or n.endswith('/devinfo/deviceinfo.txt') for n in names)
            and any(n == 'log' or n.endswith('/log') for n in names)):
        return '2.0'
    return None


def detect_chip(platform) -> str | None:
    """'mtk' | 'unisoc' | 'qcom' | None，按 device['Platform']。"""
    s = (platform or '').strip().lower()
    if not s:
        return None
    if s.startswith('mt'):
        return 'mtk'
    if s.startswith(('ums', 'sp', 'sc', 'ud')):
        return 'unisoc'
    if s.startswith(('sm', 'sdm', 'msm', 'qcom',
                     'lahaina', 'kona', 'taro')):
        return 'qcom'
    return None


def load_dataset(path, label: str, kind: str = 'static', tmpdir=None,
                 model: str = 'auto', chip: str = 'auto') -> Dataset:
    ds = Dataset(label=label, path=str(path))
    tmp = tempfile.TemporaryDirectory(prefix='memscope_', dir=tmpdir)
    tmp_path = Path(tmp.name)
    opened = []  # 需在清理临时目录前关闭的 ZipFile
    try:
        src = open_source(path, tmp_path)
        if isinstance(src, ZipSource):
            opened.append(src.zf)
        _load(src, ds, tmp_path, opened, kind, model, chip)
    finally:
        for zf in opened:
            try:
                zf.close()
            except Exception:
                pass
        tmp.cleanup()
    return ds


def _load(src: _Source, ds: Dataset, tmp_path: Path, opened: list,
          kind: str, model: str, chip: str):
    names = set(src.list())
    detected = detect_model(names)
    ds.detect_src = {'model': 'auto' if model == 'auto' else 'cli',
                     'chip': 'auto' if chip == 'auto' else 'cli'}
    if model != 'auto':
        ds.model = model
        if detected != model:
            ds.warnings.append(
                f'体验模型按命令行指定为 {model}'
                f'（包内特征判定为 {detected or "无法识别"}）')
    elif detected is None:
        raise LoadError('无法识别体验模型（未找到 device_info.txt 或 '
                        'devinfo/deviceinfo.txt），可用 --model 2.0|3.0 指定')
    else:
        ds.model = detected
    if ds.model == '2.0':
        _load_v2(src, ds, names, tmp_path, opened, kind, chip)
    else:
        _load_v3(src, ds, names, tmp_path, opened, kind, chip)


def _find(names, suffix):
    hits = sorted(n for n in names if n == suffix or n.endswith('/' + suffix))
    return hits[0] if hits else None


def _find_summary(names):
    hits = sorted(n for n in names if n.endswith('Exception_Summary_Report.xlsx'))
    hits = [n for n in hits if '/' not in n] or hits
    return hits[0] if hits else None


def _resolve_chip(ds: Dataset, chip: str):
    detected = detect_chip(ds.device.get('Platform'))
    if chip != 'auto':
        ds.chip = chip
        if detected and detected != chip:
            ds.warnings.append(
                f'芯片平台按命令行指定为 {chip}（检测到 {detected}）')
        return
    ds.chip = detected
    if detected is None and ds.device.get('Platform'):
        ds.warnings.append(f"无法识别芯片平台 '{ds.device['Platform']}'")


def _ranges(indices):
    """[1,2,3,5] → [[1,3],[5,5]]"""
    out = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def _rounds(src, ds, names, tmp_path, opened, tmap, reason_fn=None):
    """{idx: {fname: thunk}} → ds.rounds + failed dict。"""
    rfiles = _round_files(src, names, tmp_path, opened)
    failed = {}
    for idx in sorted(rfiles):
        rd, reason = _load_round(idx, rfiles[idx], tmap.get(idx, (None, None)))
        if reason is None:
            ds.rounds.append(rd)
        else:
            failed[idx] = reason_fn(idx, reason) if reason_fn else reason
    if not ds.rounds:
        ds.warnings.append('未找到任何 round_N 轮次数据')
    return rfiles, failed


def _finish_failed(ds, failed, rfiles):
    ds.failed_rounds = [{'index': i, 'reason': failed[i]}
                      for i in sorted(failed)]
    ds.round_dirs = len(set(rfiles) | set(failed))
    if not ds.failed_rounds:
        return
    reason_of = {d['index']: d['reason'] for d in ds.failed_rounds}
    items = _ranges(reason_of)[:10]
    more = (f' 等共 {len(ds.failed_rounds)}'
            if len(ds.failed_rounds) > 10 else '')
    parts = []
    for a, b in items:
        span = str(a) if a == b else f'{a}–{b}'
        rs = []
        for i in range(a, b + 1):
            if reason_of[i] not in rs:
                rs.append(reason_of[i])
        parts.append(f"{span}({'|'.join(rs)})")
    ds.warnings.append(
        f'{len(ds.failed_rounds)} 轮无效：' + ', '.join(parts) + more)


def _load_v3(src, ds, names, tmp_path, opened, kind, chip):
    # device_info / round_info
    f = _find(names, 'device_info.txt')
    if f:
        ds.device, ds.packages = dev.parse_device_info(_decode(src.read(f)))
    else:
        ds.warnings.append('缺少 device_info.txt')
    _resolve_chip(ds, chip)
    f = _find(names, 'round_info.txt')
    round_times = dev.parse_round_info(_decode(src.read(f))) if f else []
    if not f:
        ds.warnings.append('缺少 round_info.txt')
    tmap = {i: (s, e) for i, s, e in round_times}

    rfiles, failed = _rounds(src, ds, names, tmp_path, opened, tmap)

    # 平台 failed_rounds.log：`[NNNN] … 原因：xxx`（补充没有轮次目录的失败轮）
    f = _find(names, 'failed_rounds.log')
    if f:
        for m in re.finditer(r'\[(\d+)\].*?原因[：:]\s*(.+)',
                             _decode(src.read(f))):
            failed.setdefault(int(m.group(1)), m.group(2).strip())
    _finish_failed(ds, failed, rfiles)

    # 阈值
    f = _find(names, 'all_memory_data.xls')
    if f:
        try:
            ds.thresholds = thr.parse_thresholds(src.read(f))
        except Exception as e:  # noqa: BLE001 - 解析失败降级为空阈值
            ds.warnings.append(f'all_memory_data.xls 解析失败: {e}')
    else:
        ds.warnings.append('缺少 all_memory_data.xls（无健康标准阈值）')

    # 事件日志年份
    year = None
    for _i, s, _e in round_times:
        if s:
            year = s.year
            break
    if year is None and ds.rounds and ds.rounds[0].start:
        year = ds.rounds[0].start.year
    eventsrc.load_events(src, ds, names, tmp_path, opened, year)

    _load_platform(src, names, ds)
    _detect_reboots(ds)


def _load_v2(src, ds, names, tmp_path, opened, kind, chip):
    # 顶层 log：脚本参数 + 各轮时间
    runlog = v2info.RunLog()
    f = _find(names, 'log')
    if f:
        try:
            runlog = v2info.parse_runlog(_decode(src.read(f)))
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'log 解析失败: {e}')
    else:
        ds.warnings.append('缺少 log（无轮次时间/脚本参数）')

    # devinfo：deviceinfo + getprop → 3.0 同款字段
    devinfo, props = {}, {}
    f = _find(names, 'devinfo/deviceinfo.txt')
    if f:
        devinfo = v2info.parse_kv(_decode(src.read(f)))
    else:
        ds.warnings.append('缺少 devinfo/deviceinfo.txt')
    f = _find(names, 'devinfo/getprop.txt')
    if f:
        props = v2info.parse_getprop(_decode(src.read(f)))
    else:
        ds.warnings.append('缺少 devinfo/getprop.txt')
    ds.device = v2info.device_fields(
        devinfo, props, runlog.test_type or _test_type_from_name(ds.path))
    f = _find(names, 'devinfo/package_list.txt')
    ds.packages = v2info.parse_packages(_decode(src.read(f))) if f else []
    if not f:
        ds.warnings.append('缺少 devinfo/package_list.txt')
    _resolve_chip(ds, chip)

    # 轮次时间：start = 本轮 log 时间，end = 下一轮 start（末轮 None）
    order = sorted(runlog.rounds)
    tmap = {i: (runlog.rounds[i].start,
                runlog.rounds[order[j + 1]].start
                if j + 1 < len(order) else None)
            for j, i in enumerate(order)}

    def reason_fn(idx, reason):
        r = runlog.rounds.get(idx)
        if r is not None and r.nodata:
            if r.device is None:
                return '设备离线/无内存数据（获取设备日期失败）'
            return '无内存数据'
        return reason

    rfiles, failed = _rounds(src, ds, names, tmp_path, opened, tmap,
                             reason_fn)
    _finish_failed(ds, failed, rfiles)

    # 阈值 + 平台判定（同一份 Exception_Summary_Report.xlsx）
    f = _find_summary(names)
    if f:
        try:
            ds.thresholds, ds.platform = v2summary.parse_summary(
                src.read(f), kind)
            # 2.0 平台 Persistent = OomScore -800 + -700（Persistent Service）
            ds.thresholds.level_groups = {
                'Persistent': ('Persistent', 'Persistent Service')}
        except Exception as e:  # noqa: BLE001
            ds.warnings.append(f'Exception_Summary_Report.xlsx 解析失败: {e}')
    else:
        ds.warnings.append(
            '缺少 *Exception_Summary_Report.xlsx（无平台判定/阈值）')

    # lmkcnt：LMK 杀进程总数 = end - start
    s, e = _find(names, 'lmkcnt_start.txt'), _find(names, 'lmkcnt_end.txt')
    if s and e:
        a = v2info.parse_lmkcnt(_decode(src.read(s)))
        b = v2info.parse_lmkcnt(_decode(src.read(e)))
        if a is not None and b is not None:
            ds.lmk_total = b - a
    else:
        ds.warnings.append('缺少 lmkcnt_start.txt / lmkcnt_end.txt')
    off = v2info.clock_offset(runlog)
    ds.clock_offset_s = off.total_seconds() if off is not None else None

    year = None
    if ds.rounds and ds.rounds[0].start:
        year = ds.rounds[0].start.year
    elif order and runlog.rounds[order[0]].start:
        year = runlog.rounds[order[0]].start.year
    eventsrc.load_events(src, ds, names, tmp_path, opened, year)
    _detect_reboots(ds)


def _test_type_from_name(path) -> str | None:
    n = Path(str(path)).name
    if '动态内存' in n:
        return 'dynamic'
    if '静态内存' in n:
        return 'static'
    return None


def _load_round(idx: int, got: dict, times):
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
        if 'proc_vmstat.txt' in got:
            rd.vmstat = vms.parse_vmstat(_decode(got['proc_vmstat.txt']()))
    except Exception as e:  # noqa: BLE001 - 单轮异常 → 无效轮
        return rd, f'解析失败: {str(e)[:80]}'
    return rd, None


def _round_files(src: _Source, names, tmp_path: Path, opened: list):
    """{round_idx: {fname: thunk()->bytes}}。round_N/ 目录或嵌套 rounds.zip。"""
    out = {}
    for n in names:
        m = _ROUND_PAT.search(n)
        if m:
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
        m = _ROUND_PAT.search(n)
        if m:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = (
                lambda zi=zi: nz.read(zi))
    return out


def _load_platform(src: _Source, names, ds: Dataset):
    """顶层 *Exception_Summary_Report.xlsx → ds.platform。缺失/失败 → {} + warning。"""
    f = _find_summary(names)
    if not f:
        ds.warnings.append('缺少 *Exception_Summary_Report.xlsx（无平台判定）')
        return
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(src.read(f)), read_only=True)
        row_iter = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        for row in row_iter:
            if not row or row[0] is None:
                continue
            k = str(row[0]).strip()
            if k == '内存标准动态测试结果：':
                ds.platform['dynamic_result'] = (str(row[1]).strip()
                                               if len(row) > 1 and row[1] is not None else '')
            elif k == '内存标准静态测试结果：':
                ds.platform['static_result'] = (str(row[1]).strip()
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
