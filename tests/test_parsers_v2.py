"""2.0 包解析器单元测试（微型内联数据 + 合成 ylog/xlsx）。"""
import io
import struct
import zlib
from datetime import datetime, timedelta

import pytest

from memscope.parsers.dumpsys import parse_dumpsys
from memscope.parsers.procstatus import parse_proc_status
from memscope.parsers.events import parse_events
from memscope.parsers.v2info import (
    clock_offset, device_fields, parse_getprop, parse_kv, parse_lmkcnt,
    parse_packages, parse_runlog)
from memscope.parsers.v2summary import parse_summary
from memscope.parsers.ylog import iter_event_lines


# ---------- procstatus ----------

V2_PROCSTATUS = """\
LevelName: Native          VmRSS + Vmswap: 1358.20MB
ProcName: com.android.commands.monkey.transsion Pid: 7938   OomScore: -1000 LevelName: Native          VmRSS + VmSwap:  137604.00KB VmSwap:  32228KB ProcType: native RssAnon: 18616KB RssFile: 85612KB
ProcName: zygote64                       Pid: 627    OomScore: -1000 LevelName: Native          VmRSS + VmSwap:  121268.00KB VmSwap:  34684KB ProcType: native
ProcName: /system/bin/init               Pid: 1      OomScore: -1000 LevelName: Native          VmRSS + VmSwap:    8108.00KB VmSwap:   2868KB ProcType: native
ProcName: /system/bin/init               Pid: 383    OomScore: -1000 LevelName: Native          VmRSS + VmSwap:    1000.00KB VmSwap:     10KB ProcType: native
LevelName: A Services      VmRSS + Vmswap: 242.47MB
ProcName: com.transsion.plat.appupdate   Pid: 7836   OomScore: 500   LevelName: A Services      VmRSS + VmSwap:   11384.00KB VmSwap:   5186KB ProcType: custom
ProcName: com.a.c                        Pid: 101    OomScore: -700  LevelName: Persistent      VmRSS + VmSwap:    2048.00KB VmSwap:      0KB ProcType: system
"""


def test_proc_status_v2():
    r = parse_proc_status(V2_PROCSTATUS)
    assert set(r) == {'Native', 'A Services', 'Persistent Service'}
    assert r['Native']['zygote64'] == pytest.approx(121268.0 / 1024)
    assert r['Native']['com.android.commands.monkey.transsion'] == \
        pytest.approx(137604.0 / 1024)
    # 路径名 + 同名多 pid 相加 + 小数值
    assert r['Native']['/system/bin/init'] == pytest.approx(9108.0 / 1024)
    # 含空格的 LevelName
    assert r['A Services']['com.transsion.plat.appupdate'] == \
        pytest.approx(11384.0 / 1024)
    # OomScore -700 → Persistent Service（不用 LevelName 字段）
    assert r['Persistent Service']['com.a.c'] == pytest.approx(2.0)


def test_proc_status_v3_still_works():
    text = (
        "ProcName:/system/bin/init Pid:1 OomScore:-1000 LevelName:Native "
        "VmRSS+VmSwap:10696KB VmSwap:3744KB ProcType:system\n"
        "ProcName:com.a.b Pid:100 OomScore:905 LevelName:Cached "
        "VmRSS+VmSwap:1024KB VmSwap:0KB ProcType:custom\n")
    r = parse_proc_status(text)
    assert r['Native']['/system/bin/init'] == pytest.approx(10696 / 1024)
    assert r['Cached']['com.a.b'] == pytest.approx(1.0)


# ---------- dumpsys tail（无 Global Mapped Bitmaps） ----------

V2_DUMPSYS_TAIL = """\
Applications Memory Usage (in Kilobytes):
Uptime: 210324 Realtime: 210324

Total PSS by category:
    428,372K: .apk mmap                                                   (    9,844K in swap)

Total RAM: 3,834,008K (status normal)
 Free RAM: 1,286,463K (  383,539K cached pss +   837,492K cached kernel +    65,432K free)
DMA-BUF:   107,104K (   16,448K mapped +    90,656K unmapped)
DMA-BUF Heaps:   107,104K
DMA-BUF Heaps pool:    18,752K
      GPU:   139,660K (  139,660K dmabuf +         0K private)
 Used RAM: 2,935,824K (2,285,052K used pss +   650,772K kernel)
 Lost RAM:   332,831K
     ZRAM:   336,212K physical used for 1,185,792K in swap (2,875,500K total swap)
"""


def test_dumpsys_tail_v2_no_bitmaps():
    tail = parse_dumpsys(V2_DUMPSYS_TAIL)['tail']
    assert tail['Total RAM'] == pytest.approx(3834008 / 1024)
    assert tail['Free RAM'] == pytest.approx(1286463 / 1024)
    assert tail['cached pss'] == pytest.approx(383539 / 1024)
    assert tail['cached kernel'] == pytest.approx(837492 / 1024)
    assert tail['free'] == pytest.approx(65432 / 1024)
    assert tail['Used RAM'] == pytest.approx(2935824 / 1024)
    assert tail['used pss'] == pytest.approx(2285052 / 1024)
    assert tail['kernel'] == pytest.approx(650772 / 1024)
    assert tail['Lost RAM'] == pytest.approx(332831 / 1024)
    assert tail['ZRAM physical'] == pytest.approx(336212 / 1024)
    assert tail['in swap'] == pytest.approx(1185792 / 1024)
    assert tail['DMA-BUF'] == pytest.approx(107104 / 1024)
    assert tail['GPU'] == pytest.approx(139660 / 1024)


# ---------- v2info ----------

def test_parse_kv_and_getprop():
    kv = parse_kv(
        "ro.product.device: TECNO-KN3\n"
        "ro.kernel.version: 5.15\n"
        "ro.os_product.version: \n"
        "Physical size: 720x1600\n")
    assert kv['ro.product.device'] == 'TECNO-KN3'
    assert kv['ro.os_product.version'] == ''
    assert kv['Physical size'] == '720x1600'
    props = parse_getprop(
        "[ro.product.model]: [TECNO KN3]\n"
        "[ro.transsion.backlight.level]: []\n"
        "[ro.os_sc_install_range]: [50_20900]\n")
    assert props['ro.product.model'] == 'TECNO KN3'
    assert props['ro.transsion.backlight.level'] == ''
    assert props['ro.os_sc_install_range'] == '50_20900'


def test_device_fields_kn3():
    devinfo = parse_kv(
        "ro.product.device: TECNO-KN3\n"
        "ro.build.display.id: KN3-15.1.2.195SP16(OP001PF001AZ)\n"
        "ro.build.version.release: 15\n"
        "ro.board.platform: ums9230\n"
        "ro.kernel.version: 5.15\n"
        "Physical size: 720x1600\n"
        "MemTotal:        3834008 kB\n")
    props = parse_getprop(
        "[ro.product.model]: [TECNO KN3]\n"
        "[ro.product.brand]: [TECNO]\n"
        "[ro.build.display.id]: [KN3-15.1.2.195SP16(OP001PF001AZ)]\n"
        "[ro.build.version.release]: [15]\n"
        "[ro.serialno]: [15834155B6001363]\n"
        "[ro.product.device]: [TECNO-KN3]\n"
        "[ro.tranos.version]: [hios15.1.2]\n"
        "[ro.board.platform]: [ums9230]\n")
    fields = device_fields(devinfo, props, 'dynamic')
    assert fields == {
        'Device Model': 'TECNO KN3',
        'Brand': 'TECNO',
        'Market': 'OP',
        'SN Number': '15834155B6001363',
        'Test Type': 'dynamic',
        'Android Version': '15',
        'Device': 'TECNO-KN3',
        'Rom Version': 'KN3-15.1.2.195SP16(OP001PF001AZ)',
        'TOS Version': 'hios15.1.2',
        'Platform': 'ums9230',
        'Kernel Version': '5.15',
    }
    assert list(fields) == [
        'Device Model', 'Brand', 'Market', 'SN Number', 'Test Type',
        'Android Version', 'Device', 'Rom Version', 'TOS Version',
        'Platform', 'Kernel Version']


def test_parse_packages_and_lmkcnt():
    assert parse_packages("package:com.b\npackage:com.a\npackage:com.b\n") == \
        ['com.a', 'com.b']
    lmk = ("ACTIVITY MANAGER LMK KILLS (dumpsys activity lmkcnt)\n"
           "  Total number of kills: 2029\n"
           "[Error] Get kill reason failed! sys.lmk.killreasoncnt <= 0.\n")
    assert parse_lmkcnt(lmk) == 2029
    assert parse_lmkcnt("nothing here") is None


RUNLOG = """\
2026-10-26 17:21:51,662 - INFO - ============Script Parameters================
2026-10-26 17:21:51,662 - INFO - -runtime: 345600
2026-10-26 17:21:51,662 - INFO - -s: 15834155B6001363
2026-10-26 17:21:51,662 - INFO - -std: dynamic
2026-10-26 17:21:51,663 - INFO - -T: 200
2026-10-26 17:21:51,663 - INFO - =============================================
2026-10-26 17:22:28,330 - INFO - ===================== round_1=====================
2026-10-26 17:22:28,383 - INFO - device date: Mon Oct 26 16:25:56 CST 2026
2026-10-26 17:23:20,223 - INFO - Command 'adb -s X shell dumpsys meminfo > out.txt' execution time: 0.30 seconds
2026-10-26 17:26:43,000 - INFO - ===================== round_2=====================
2026-10-26 17:26:43,100 - INFO - device date: unknown
2026-10-26 17:26:50,000 - INFO - No memory info in this round, please check the round 2 message.
2026-10-26 17:30:37,000 - INFO - ===================== round_3=====================
2026-10-26 17:30:37,100 - INFO - device date: Mon Oct 26 16:34:00 CST 2026
"""


def test_parse_runlog():
    rl = parse_runlog(RUNLOG)
    assert rl.params['-runtime'] == '345600'
    assert rl.params['-std'] == 'dynamic'
    assert rl.params['-T'] == '200'
    assert rl.test_type == 'dynamic'
    assert sorted(rl.rounds) == [1, 2, 3]
    r1, r2, r3 = rl.rounds[1], rl.rounds[2], rl.rounds[3]
    assert r1.start == datetime(2026, 10, 26, 17, 22, 28, 330000)
    assert r1.device == datetime(2026, 10, 26, 16, 25, 56)
    assert r2.device is None          # device date: unknown
    assert r2.nodata is True          # No memory info ... round 2
    assert r1.nodata is False and r3.nodata is False
    off = clock_offset(rl)
    # r1: 56m32.33s, r3: 56m37.0s → 中位 56m34.665s（r2 无 device 不计）
    assert off is not None
    assert off.total_seconds() == pytest.approx(3394.665)


def test_parse_events_offset():
    lines = [
        '10-22 14:10:00.000000  1  1 I am_proc_start: [0,100,1000,com.a,broadcast]',
    ]
    r1 = datetime(2026, 10, 22, 14, 5, 4)
    evs, _ = parse_events(lines, 2026, r1, None)
    assert evs[0].t == datetime(2026, 10, 22, 14, 10, 0)
    evs2, _ = parse_events(lines, 2026, r1, None,
                           offset=timedelta(minutes=57))
    assert evs2[0].t == datetime(2026, 10, 22, 15, 7, 0)


# ---------- v2summary ----------

def _summary_xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '内存标准检测结果'
    for row in [
        ['Rss内存标准静态测试结果：', 'NA'],
        ['Rss内存标准动态测试结果：', 'Fail'],
        ['ROM版本：', 'KN3-15.1.2.195SP16(OP001PF001AZ)'],
        ['检测工具版本：', 'v2.10'],
        ['内存标准版本：', 'v3.1'],
        ['4GB内存标准：'],
        ['进程层级', '自研进程数量标准', 'RSS静态内存占用标准(MB)',
         'RSS动态内存占用标准(MB)'],
        ['Free RAM', None, 1911, 1171],
        ['Native', None, 762, 930],
        ['System', None, 259, 280],
        ['Persistent', None, 1185, 1175],
        ['Visible', None, 731, 2014],
        [None, None, None, None],
        ['层级内存占用超标 Summary：'],
        ['进程层级', '有效测试次数', '异常次数', '内存占用标准(MB)',
         '异常占用均值(MB)', '异常占用最大值/最小值(MB)',
         '异常最大值/最小值的log路径'],
        ['Free RAM', 880, 267, 1171, 952.29, 407.07, 'r/round_501'],
        ['Native', 880, 876, 930, 1241.64, 1495.68, 'r/round_390'],
        ['System', 880, 879, 280, 547.87, 641.53, 'r/round_1440'],
        ['Visible', 880, 161, 2014, 2189.48, 3095.2, 'r/round_640'],
    ]:
        ws.append(row)
    ws2 = wb.create_sheet('内存标准检测整体统计')
    for row in [
        ['所有测试均值(包含异常和非异常)'],
        ['进程层级', '有效测试次数', 'RSS内存占用标准值(MB)'],
        ['Native', 880, 930, 1238.8],
    ]:
        ws2.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_summary_dynamic():
    th, platform = parse_summary(_summary_xlsx(), 'dynamic')
    assert th.available
    assert th.levels == {'Native': 930, 'System': 280,
                       'Persistent': 1175, 'Visible': 2014}
    assert th.free_ram == 1171
    assert th.procs == {}
    assert platform['static_result'] == 'NA'
    assert platform['dynamic_result'] == 'Fail'
    assert platform['tool_version'] == 'v2.10'
    assert platform['std_version'] == 'v3.1'
    assert platform['rom'] == 'KN3-15.1.2.195SP16(OP001PF001AZ)'
    assert platform['std_tier'] == '4GB'
    assert platform['valid_count'] == 880


def test_parse_summary_static():
    th, _ = parse_summary(_summary_xlsx(), 'static')
    assert th.available
    assert th.levels == {'Native': 762, 'System': 259,
                       'Persistent': 1185, 'Visible': 731}
    assert th.free_ram == 1911


# ---------- ylog ----------

def _fh():
    return struct.pack('<i', 0x2E2E) + b'YLOG-ZIP test'.ljust(92, b'\x00')


def _ft():
    return struct.pack('<i', 0xB5B5) + b'\x00' * 100


def _blk(payload: bytes, tag: bytes, z: int = 1, seq: int = 0) -> bytes:
    data = zlib.compress(payload) if z == 1 else payload
    return struct.pack('<iIiic3x', 0x5A5A, seq, len(data), z, tag) + data


def _ylog_stream() -> bytes:
    meta1 = b'VERSION:5.0.0\nTAGS:\x01:lastlog.log;\x03:android.log;\x04:kernel.log;'
    meta2 = b'VERSION:5.0.0\nTAGS:\x02:android.log;\x09:kernel.log;'
    return b''.join([
        # ---- file 1 ----
        _fh(),
        _blk(meta1, b'\x00'),
        # android.log 行被拆到两个块中
        _blk(b'E000001  10-26 15:47:09.537  1112  1366 I am_ki', b'\x03'),
        _blk(b'll : [0,32009,x,955,empty]\n'
             b'E000002  10-26 15:47:10.000     1     1 I am_proc_died: [0,1,x]\r\n',
             b'\x03'),
        # 非 android 块中的 am_kill 行必须被忽略
        _blk(b'E000099  10-26 15:48:00.000     2     2 I am_kill : [9,9,drop]\n',
             b'\x01'),
        b'JUNKXYZ',  # 7 字节垃圾 → resync
        _ft(),
        # ---- file 2（android tag 不同）----
        _fh(),
        _blk(meta2, b'\x00'),
        _blk(b'E000010  10-27 08:00:00.001     2     2 I am_low_memory: 1\n',
             b'\x02'),
        _ft(),
    ])


def test_iter_event_lines_synthetic():
    tags = (b'am_kill', b'am_proc_died', b'am_low_memory')
    stats = {}
    lines = list(iter_event_lines(io.BytesIO(_ylog_stream()), tags, stats))
    assert lines == [
        '10-26 15:47:09.537  1112  1366 I am_kill : [0,32009,x,955,empty]',
        '10-26 15:47:10.000     1     1 I am_proc_died: [0,1,x]',
        '10-27 08:00:00.001     2     2 I am_low_memory: 1',
    ]
    assert stats['files'] == 2
    assert stats['resync'] >= 7
    assert stats['android_blocks'] >= 3
    assert stats['version'] == '5.0.0'
