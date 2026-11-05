"""2.0 包加载、事件源优先级、detect_model/detect_chip、CLI 新参数。"""
import io
import struct
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

import pytest

from memscope import loader
from memscope.loader import (
    LoadError, _ROUND_PAT, _round_files, detect_chip, detect_model,
    load_dataset)
from memscope.parsers.events import parse_events
from tests.test_parsers_v2 import _blk, _fh, _ft, _summary_xlsx


# ---------- detect ----------

def test_detect_model():
    assert detect_model({'device_info.txt'}) == '3.0'
    assert detect_model({'x/round_info.txt'}) == '3.0'
    assert detect_model({'a/tw_rpt_x_rounds.zip'}) == '3.0'
    assert detect_model({'devinfo/deviceinfo.txt', 'log'}) == '2.0'
    assert detect_model({'devinfo/deviceinfo.txt'}) is None   # 缺顶层 log
    assert detect_model({'log'}) is None
    assert detect_model(set()) is None
    # 两模型特征并存 → 3.0 优先
    assert detect_model({'device_info.txt', 'devinfo/deviceinfo.txt',
                         'log'}) == '3.0'


def test_detect_chip():
    assert detect_chip('mt6878') == 'mtk'
    assert detect_chip('MT6893') == 'mtk'
    assert detect_chip('ums9230') == 'unisoc'
    assert detect_chip('sc9863a') == 'unisoc'
    assert detect_chip('sm8250') == 'qcom'
    assert detect_chip('sdm660') == 'qcom'
    assert detect_chip('lahaina') == 'qcom'
    assert detect_chip('bogus') is None
    assert detect_chip('') is None and detect_chip(None) is None


def test_detect_model_override(tmp_path):
    d = _mk_v2_pkg(tmp_path / 'v2')
    ds = load_dataset(d, 'A', kind='dynamic')
    assert ds.model == '2.0' and ds.detect_src['model'] == 'auto'
    ds2 = load_dataset(d, 'A', kind='dynamic', model='3.0')
    assert ds2.model == '3.0' and ds2.detect_src['model'] == 'cli'
    assert any('命令行指定为 3.0' in w and '2.0' in w for w in ds2.warnings)
    ds3 = load_dataset(d, 'A', kind='dynamic', chip='mtk')
    assert ds3.chip == 'mtk' and ds3.detect_src['chip'] == 'cli'
    assert any('芯片平台按命令行指定为 mtk' in w for w in ds3.warnings)


def test_detect_model_unknown_raises(tmp_path):
    (tmp_path / 'x.txt').write_text('x')
    with pytest.raises(LoadError, match='无法识别体验模型'):
        load_dataset(tmp_path, 'A')


# ---------- round 文件匹配 ----------

def test_round_files_regex(tmp_path):
    names = {'round_1/dumpsys_meminfo.txt',
             'round_1/procStatus_sortedByLevel.txt',
             'round_1/proc_meminfo.txt',
             'round_1/tran_mem_debug/proc_vmstat.txt',
             'round_1/dumpsys_meminfo_init.txt',       # 进程明细（目标）
             'round_1/tran_mem_debug/dumpsys_activity_a.txt',
             'x/round_2/procStatus_sortedByLevel.txt'}

    class _Src:
        def list(self): return list(names)
        def read(self, n): return b''
        def materialize(self, n, t): return None

    r = _round_files(_Src(), names, tmp_path, [])
    assert sorted(r) == [1, 2]
    assert sorted(r[1]) == ['dumpsys_meminfo.txt', 'dumpsys_meminfo_init.txt',
                            'procStatus_sortedByLevel.txt',
                            'proc_meminfo.txt', 'proc_vmstat.txt']
    assert sorted(r[2]) == ['procStatus_sortedByLevel.txt']


# ---------- 最小 2.0 包 ----------

_DUMPSYS_V2 = """\
Applications Memory Usage (in Kilobytes):
Uptime: 210324 Realtime: 210324

Total PSS by OOM adjustment:
    500,000K: Native
         5,423K: zygote64 (pid 627)

Total PSS by category:

Total RAM: 3,834,008K (status normal)
 Free RAM: 1,286,463K (  383,539K cached pss +   837,492K cached kernel +    65,432K free)
 Used RAM: 2,935,824K (2,285,052K used pss +   650,772K kernel)
     ZRAM:   336,212K physical used for 1,185,792K in swap (2,875,500K total swap)
"""

_PROCSTATUS_V2 = """\
LevelName: Native          VmRSS + Vmswap: 120.00MB
ProcName: zygote64                       Pid: 627    OomScore: -1000 LevelName: Native          VmRSS + VmSwap:  121268.00KB VmSwap:  34684KB ProcType: native
LevelName: Persistent      VmRSS + Vmswap: 10.00MB
ProcName: system                         Pid: 1419   OomScore: -900  LevelName: Persistent      VmRSS + VmSwap:   10240.00KB VmSwap:      0KB ProcType: system
ProcName: com.a.c                        Pid: 101    OomScore: -700  LevelName: Persistent      VmRSS + VmSwap:    2048.00KB VmSwap:      0KB ProcType: system
"""

_RUNLOG = """\
2026-10-26 17:00:00,000 - INFO - ============Script Parameters================
2026-10-26 17:00:00,000 - INFO - -std: dynamic
2026-10-26 17:00:00,000 - INFO - -s: 15834155B6001363
2026-10-26 17:00:00,001 - INFO - =============================================
2026-10-26 17:00:00,000 - INFO - ===================== round_1=====================
2026-10-26 17:00:01,000 - INFO - device date: Mon Oct 26 16:03:00 CST 2026
2026-10-26 17:04:00,000 - INFO - ===================== round_2=====================
2026-10-26 17:04:01,000 - INFO - device date: unknown
2026-10-26 17:04:10,000 - INFO - No memory info in this round, please check the round 2 message.
2026-10-26 17:08:00,000 - INFO - ===================== round_3=====================
2026-10-26 17:08:01,000 - INFO - device date: Mon Oct 26 16:11:00 CST 2026
"""

_LMK = ("ACTIVITY MANAGER LMK KILLS (dumpsys activity lmkcnt)\n"
        "  Total number of kills: {n}\n")


def _ylog_bytes():
    meta = b'VERSION:5.0.0\nTAGS:\x03:android.log;'
    ev = (b'E000001  10-26 16:05:00.000  1112  1366 I am_proc_start: '
          b'[0,900,10160,com.foo,bound-service,{com.foo/.S},caller=a/1]\n'
          b'E000002  10-26 16:05:01.000   583   583 I killinfo: '
          b'[900,10160,925,50,106672,10,757068]\n'
          b'E000003  10-26 16:05:02.000  1112  1366 I am_kill : '
          b'[0,900,com.foo,905,empty,106672]\n')
    return _fh() + _blk(meta, b'\x00') + _blk(ev, b'\x03') + _ft()


def _v2_files():
    files = {
        'devinfo/deviceinfo.txt':
            'ro.product.device: TECNO-KN3\n'
            'ro.build.display.id: KN3-15.1.2.195SP16(OP001PF001AZ)\n'
            'ro.build.version.release: 15\n'
            'ro.board.platform: ums9230\n'
            'ro.kernel.version: 5.15\n',
        'devinfo/getprop.txt':
            '[ro.product.model]: [TECNO KN3]\n'
            '[ro.product.brand]: [TECNO]\n'
            '[ro.serialno]: [15834155B6001363]\n'
            '[ro.product.device]: [TECNO-KN3]\n'
            '[ro.tranos.version]: [hios15.1.2]\n'
            '[ro.board.platform]: [ums9230]\n'
            '[ro.build.display.id]: [KN3-15.1.2.195SP16(OP001PF001AZ)]\n'
            '[ro.build.version.release]: [15]\n',
        'devinfo/package_list.txt': 'package:com.a\npackage:com.b\n',
        'log': _RUNLOG,
        'lmkcnt_start.txt': _LMK.format(n=1),
        'lmkcnt_end.txt': _LMK.format(n=2029),
        'report_Exception_Summary_Report.xlsx': _summary_xlsx(),
        'round_1/dumpsys_meminfo.txt': _DUMPSYS_V2,
        'round_1/procStatus_sortedByLevel.txt': _PROCSTATUS_V2,
        'round_1/proc_meminfo.txt': 'MemTotal:        3834008 kB\n',
        'round_1/tran_mem_debug/proc_vmstat.txt': 'pswpin 100\n',
        'round_2/dumpsys_meminfo.txt': b'',
        'round_2/procStatus_sortedByLevel.txt': b'',
        'round_3/dumpsys_meminfo.txt': _DUMPSYS_V2.replace('210324', '310324'),
        'round_3/procStatus_sortedByLevel.txt': _PROCSTATUS_V2,
        'round_3/proc_meminfo.txt': 'MemTotal:        3834008 kB\n',
        'round_3/tran_mem_debug/proc_vmstat.txt': 'pswpin 300\n',
        'ylog/ap/000-x.ylog': _ylog_bytes(),
    }
    return files


def _mk_v2_pkg(root):
    root = Path(root)
    for name, data in _v2_files().items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data, encoding='utf-8')
    return root


def _check_v2_ds(ds):
    assert ds.model == '2.0' and ds.chip == 'unisoc'
    assert ds.n == 2 and ds.round_dirs == 3
    assert ds.failed_rounds == [
        {'index': 2, 'reason': '设备离线/无内存数据（获取设备日期失败）'}]
    assert ds.device['Device Model'] == 'TECNO KN3'
    assert ds.device['Market'] == 'OP'
    assert ds.device['Test Type'] == 'dynamic'
    assert ds.device['Platform'] == 'ums9230'
    assert ds.packages == ['com.a', 'com.b']
    th = ds.thresholds
    assert th.available and th.levels['Native'] == 930
    assert th.free_ram == 1171
    assert th.level_groups == {'Persistent': ('Persistent', 'Persistent Service')}
    assert ds.platform['dynamic_result'] == 'Fail'
    assert ds.platform['valid_count'] == 880
    assert ds.lmk_total == 2028
    assert ds.clock_offset_s == pytest.approx(3420, abs=2)
    r1 = ds.rounds[0]
    assert r1.index == 1
    assert r1.start == datetime(2026, 10, 26, 17, 0, 0)
    assert r1.end == datetime(2026, 10, 26, 17, 4, 0)   # 下一轮 start
    assert r1.tail['Free RAM'] == pytest.approx(1286463 / 1024)
    assert r1.rss['Native']['zygote64'] == pytest.approx(121268 / 1024)
    assert r1.rss['Persistent Service']['com.a.c'] == pytest.approx(2.0)
    assert r1.vmstat['pswpin'] == 100
    assert ds.rounds[1].end is None                    # 末轮
    es = ds.event_source
    assert es['kind'] == 'unisoc_ylog' and es['files'] == 1
    assert es['events'] == 3
    # 设备时间 +57min → 主机时间；均落在 round_1 之后 → test
    ks = {e.kind for e in ds.events}
    assert ks == {'start', 'lmk', 'kill'}
    kill = next(e for e in ds.events if e.kind == 'kill')
    assert kill.t == datetime(2026, 10, 26, 17, 2, 2)
    assert kill.phase == 'test'


def test_v2_pkg_dir(tmp_path):
    ds = load_dataset(_mk_v2_pkg(tmp_path / 'v2'), 'A', kind='dynamic')
    _check_v2_ds(ds)


def test_v2_pkg_zip(tmp_path):
    zp = tmp_path / 'pkg.zip'
    with zipfile.ZipFile(zp, 'w') as z:
        for name, data in _v2_files().items():
            z.writestr('pkg/' + name, data)
    ds = load_dataset(zp, 'A', kind='dynamic')
    _check_v2_ds(ds)


# ---------- 事件源优先级 ----------

def _mini_v3(root):
    """最小 3.0 目录包（仅需 device_info + round_info 使 detect/year 生效）。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'device_info.txt').write_text(
        'Device Model: X\nTest Type: dynamic\nPlatform: mt6878\n',
        encoding='utf-8')
    (root / 'round_info.txt').write_text(
        '[0001]     开始时间：2026-10-13 10:00:00     结束时间：2026-10-13 10:01:00\n',
        encoding='utf-8')
    return root


_EV = '10-22 14:10:00.000000  1112  1366 I am_kill : [0,9,com.x,905,empty]'


def _add_mobilelog(root):
    with zipfile.ZipFile(Path(root) / 'mobilelog.zip', 'w') as z:
        z.writestr('APLog/events_log_1', _EV)


def _add_debuglogger(root):
    p = Path(root) / 'debuglogger/mobilelog/APLog'
    p.mkdir(parents=True, exist_ok=True)
    (p / 'events_log_1').write_text(_EV)


def _add_ylog(root):
    meta = b'VERSION:5.0.0\nTAGS:\x03:android.log;'
    ev = ('E000001  ' + _EV + '\n').encode()
    p = Path(root) / 'ylog/ap'
    p.mkdir(parents=True, exist_ok=True)
    (p / '000-x.ylog').write_bytes(
        _fh() + _blk(meta, b'\x00') + _blk(ev, b'\x03') + _ft())


def _add_bugreport(root):
    txt = ('------ EVENT LOG (logcat -b events) ------\n'
           '10-30 16:11:18.388  1000  1419  1735 I am_kill : [0,9,com.x,905,empty]\n'
           '--------- 0.1s was the duration of EVENT LOG ------\n'
           '------ MAIN LOG (x) ------\n'
           '10-30 16:11:18.388  1000  1  1 I am_kill : [9,9,drop]\n')
    with zipfile.ZipFile(Path(root) / 'bugreport-X.zip', 'w') as z:
        z.writestr('bugreport-X.txt', txt)


def test_event_source_mobilelog_beats_others(tmp_path):
    root = _mini_v3(tmp_path / 'p')
    _add_mobilelog(root)
    _add_debuglogger(root)
    _add_ylog(root)
    ds = load_dataset(root, 'A', kind='dynamic')
    assert ds.event_source['kind'] == 'mtk_mobilelog'
    assert ds.event_source['files'] == 1
    assert [e.kind for e in ds.events] == ['kill']


def test_event_source_debuglogger_beats_ylog(tmp_path):
    root = _mini_v3(tmp_path / 'p')
    _add_debuglogger(root)
    _add_ylog(root)
    ds = load_dataset(root, 'A', kind='dynamic')
    assert ds.event_source['kind'] == 'mtk_debuglogger'
    assert [e.kind for e in ds.events] == ['kill']


def test_event_source_ylog(tmp_path):
    root = _mini_v3(tmp_path / 'p')
    _add_ylog(root)
    ds = load_dataset(root, 'A', kind='dynamic')
    assert ds.event_source['kind'] == 'unisoc_ylog'
    assert [e.kind for e in ds.events] == ['kill']
    # chip=mtk + unisoc_ylog → 不一致警告
    assert any('不一致' in w for w in ds.warnings)


def test_event_source_bugreport_fallback(tmp_path):
    root = _mini_v3(tmp_path / 'p')
    _add_bugreport(root)
    ds = load_dataset(root, 'A', kind='dynamic')
    assert ds.event_source['kind'] == 'bugreport'
    assert [e.kind for e in ds.events] == ['kill']   # MAIN LOG 区段不计
    assert any('bugreport' in w for w in ds.warnings)


def test_event_source_none(tmp_path):
    root = _mini_v3(tmp_path / 'p')
    ds = load_dataset(root, 'A', kind='dynamic')
    assert ds.event_source['kind'] is None
    assert any('无事件日志' in w for w in ds.warnings)


def test_events_line_uid_column():
    evs, _ = parse_events(
        ['10-30 16:11:18.388  1000  1419  1735 I am_kill : '
         '[0,32009,x,955,empty]'], 2026, None, None)
    assert len(evs) == 1 and evs[0].pid == 32009
    evs2, _ = parse_events([_EV], 2026, None, None)   # 两列写法不变
    assert len(evs2) == 1 and evs2[0].pid == 9


# ---------- CLI 参数 ----------

def test_cli_model_platform_args():
    from memscope.cli import build_parser
    p = build_parser()
    a = p.parse_args(['dynamic', 'compare', 'x', 'y'])
    assert a.model == ['auto'] and a.platform == ['auto']
    a = p.parse_args(['dynamic', 'compare', 'x', 'y', '--model', '2.0'])
    assert a.model == ['2.0']
    a = p.parse_args(['dynamic', 'compare', 'x', 'y',
                      '--model', '2.0', '3.0'])
    assert a.model == ['2.0', '3.0']
    a = p.parse_args(['static', 'compare', 'x', 'y',
                      '--platform', 'unisoc', 'mtk'])
    assert a.platform == ['unisoc', 'mtk']
    with pytest.raises(SystemExit):
        p.parse_args(['dynamic', 'compare', 'x', 'y',
                      '--model', '2.0', '3.0', '2.0'])
    with pytest.raises(SystemExit):
        p.parse_args(['dynamic', 'compare', 'x', 'y', '--model', '4.0'])
    with pytest.raises(SystemExit):
        p.parse_args(['dynamic', 'compare', 'x', 'y', '--platform', 'foo'])
