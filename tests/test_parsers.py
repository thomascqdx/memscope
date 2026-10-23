"""解析器单元测试（微型内联数据）。"""
import pytest

from memscope.parsers.dumpsys import parse_dumpsys
from memscope.parsers.procstatus import oom_to_level, parse_proc_status
from memscope.parsers.procmeminfo import parse_proc_meminfo
from memscope.parsers.events import parse_events, dedupe_lines
from memscope.parsers.device import parse_device_info, parse_round_info


DUMPSYS = """Applications Memory Usage (in Kilobytes):
Uptime: 373936 Realtime: 373936

Total PSS by OOM adjustment:
    639,523K: Native                                                      (  328,662K in swap)
         11,739K: zygote64 (pid 999)                                          (      533K in swap)
         10,390K: vendor.mediatek.hardware.mtkpower-service.mediatek (pid 1066)(    5,536K in swap)
          1,000K: init (pid 1)
          2,000K: init (pid 383)
    100,000K: Cached
          5,000K: com.transsion.launcher3 (pid 2241 / activities)

Total PSS by category:

Global Mapped Bitmaps:
  Mapped: 0 bitmaps,         0K

Total RAM: 5,585,816K (status normal)
 Free RAM: 3,078,320K (1,474,552K cached pss + 1,515,052K cached kernel +    88,716K free)
DMA-BUF:   145,124K (       12K mapped +   145,112K unmapped)
      GPU:   193,304K (  141,864K dmabuf +    51,440K private)
 Used RAM: 2,837,011K (1,776,499K used pss + 1,060,512K kernel)
 Lost RAM:   -10,240K
     ZRAM:   195,852K physical used for   736,000K in swap (4,189,356K total swap)
"""


def test_dumpsys_pss_levels_and_procs():
    r = parse_dumpsys(DUMPSYS)
    assert r['uptime_ms'] == 373936
    pss = r['pss']
    assert set(pss) == {'Native', 'Cached'}
    assert pss['Native']['total'] == pytest.approx(639523 / 1024)
    procs = pss['Native']['procs']
    # 名称紧贴 in-swap 括注的变体
    assert procs['vendor.mediatek.hardware.mtkpower-service.mediatek'] == pytest.approx(10390 / 1024)
    # 同名多 pid 相加
    assert procs['init'] == pytest.approx((1000 + 2000) / 1024)
    # pid + activities 变体
    assert pss['Cached']['procs']['com.transsion.launcher3'] == pytest.approx(5000 / 1024)


def test_dumpsys_tail_negative_lost_ram():
    tail = parse_dumpsys(DUMPSYS)['tail']
    assert tail['Total RAM'] == pytest.approx(5585816 / 1024)
    assert tail['Free RAM'] == pytest.approx(3078320 / 1024)
    assert tail['cached pss'] == pytest.approx(1474552 / 1024)
    assert tail['cached kernel'] == pytest.approx(1515052 / 1024)
    assert tail['free'] == pytest.approx(88716 / 1024)
    assert tail['Lost RAM'] == pytest.approx(-10240 / 1024)
    assert tail['ZRAM physical'] == pytest.approx(195852 / 1024)
    assert tail['in swap'] == pytest.approx(736000 / 1024)
    assert tail['DMA-BUF'] == pytest.approx(145124 / 1024)
    assert tail['GPU'] == pytest.approx(193304 / 1024)


@pytest.mark.parametrize('score,level', [
    (-1000, 'Native'), (-901, 'Native'), (-900, 'System'), (-801, 'System'),
    (-800, 'Persistent'), (-701, 'Persistent'), (-700, 'Persistent Service'),
    (-1, 'Persistent Service'), (0, 'Foreground'), (99, 'Foreground'),
    (100, 'Visible'), (200, 'Perceptible'), (224, 'Perceptible'),
    (225, 'Perceptible Medium'), (249, 'Perceptible Medium'),
    (250, 'Perceptible Low'), (300, 'Backup'), (400, 'Heavy Weight'),
    (500, 'A Services'), (600, 'Home'), (700, 'Previous'),
    (800, 'B Services'), (900, 'Cached'), (999, 'Cached'),
])
def test_oom_to_level(score, level):
    assert oom_to_level(score) == level


def test_parse_proc_status():
    text = """Uptime:383 Realtime:383
ProcName:/system/bin/init Pid:1 OomScore:-1000 LevelName:Native VmRSS+VmSwap:10696KB VmSwap:3744KB ProcType:system
ProcName:/system/bin/init Pid:383 OomScore:-1000 LevelName:Native VmRSS+VmSwap:5728KB VmSwap:2708KB ProcType:system
ProcName:com.a.b Pid:100 OomScore:905 LevelName:Cached VmRSS+VmSwap:1024KB VmSwap:0KB ProcType:custom
ProcName:com.a.c Pid:101 OomScore:-500 LevelName:Persistent VmRSS+VmSwap:2048KB VmSwap:0KB ProcType:system
"""
    r = parse_proc_status(text)
    # /system/bin/init 两个 pid 相加；OomScore -500 → Persistent Service（LevelName 误标为 Persistent）
    assert r['Native']['/system/bin/init'] == pytest.approx((10696 + 5728) / 1024)
    assert r['Cached']['com.a.b'] == pytest.approx(1.0)
    assert r['Persistent Service']['com.a.c'] == pytest.approx(2.0)
    assert 'Persistent' not in r


def test_parse_proc_meminfo():
    text = "MemTotal:        5585816 kB\nMemFree:           85328 kB\nSwapTotal:       4194304 kB\nSwapFree:        3458304 kB\n"
    r = parse_proc_meminfo(text)
    assert r['MemTotal'] == pytest.approx(5585816 / 1024)
    assert r['SwapUsed'] == pytest.approx((4194304 - 3458304) / 1024)


def test_parse_events_phases_and_fields():
    lines = [
        # 上一轮开机的事件（应被丢弃）
        '10-22 13:52:26.445720  1667  1719 I am_proc_start: [0,23488,10181,com.rlk.weathers,content provider,{com.rlk.weathers/.P},caller=com.transsion.atomicbrain/10197]',
        '10-22 13:59:02.312734  1001  1001 I boot_progress_start: 5666',
        # boot 期
        '10-22 13:59:13.341791  1643  1727 I am_proc_start: [0,2974,10197,com.transsion.atomicbrain,content provider,{com.transsion.atomicbrain/.SP},caller=com.transsion.launcher3/10198]',
        # 测试期：reason 为空、无 caller
        '10-22 14:10:00.000000  1643  1727 I am_proc_start: [0,3000,10200,com.x.y,,]',
        '10-22 14:11:00.000000  1643  1727 I am_kill: [0,3000,com.x.y,905,empty #22]',
        '10-22 14:12:00.000000  1643  1727 I am_proc_died: [0,3000,com.x.y,905]',
        # 重复行（去重）
        '10-22 14:12:00.000000  1643  1727 I am_proc_died: [0,3000,com.x.y,905]',
    ]
    from datetime import datetime
    r1 = datetime(2026, 10, 22, 14, 5, 4)
    evs, boot = parse_events(dedupe_lines(lines), 2026, r1, 300000)
    # boot_time = 13:59:02.312734 - 5.666s
    assert abs((boot - datetime(2026, 10, 22, 13, 58, 56)).total_seconds()) < 1.0
    kinds = {(e.proc, e.kind, e.phase): e for e in evs}
    # weathers 事件早于本次开机 → 丢弃
    assert not any(e.proc == 'com.rlk.weathers' for e in evs)
    ab = [e for e in evs if e.proc == 'com.transsion.atomicbrain'][0]
    assert ab.phase == 'boot' and ab.caller == 'com.transsion.launcher3'
    assert ab.reason == 'content provider' and 'atomicbrain' in ab.comp
    xy_start = [e for e in evs if e.proc == 'com.x.y' and e.kind == 'start'][0]
    assert xy_start.phase == 'test' and xy_start.reason == '' and xy_start.caller == ''
    assert any(e.kind == 'kill' and e.adj == '905' for e in evs)
    assert sum(1 for e in evs if e.kind == 'died') == 1  # 去重生效


def test_boot_time_fallback_uptime():
    from datetime import datetime
    lines = [
        '10-22 14:00:00.000000  1  1 I am_proc_start: [0,100,1000,com.a,broadcast]',
    ]
    r1 = datetime(2026, 10, 22, 14, 5, 4)
    evs, boot = parse_events(lines, 2026, r1, 60000)
    assert boot == r1.replace() - __import__('datetime').timedelta(milliseconds=60000)


def test_device_info():
    text = """Device Model: Infinix X1202

Brand: Infinix

SN Number: 1824615693000171

Package List:

 package:com.a
package:com.b
"""
    fields, pkgs = parse_device_info(text)
    assert fields['Device Model'] == 'Infinix X1202'
    assert fields['SN Number'] == '1824615693000171'
    assert pkgs == ['com.a', 'com.b']


def test_round_info():
    text = ("[0001]     开始时间：2026-10-13 14:33:54     结束时间：2026-10-13 14:34:04\n"
            "[0002]     开始时间：2026-10-13 14:38:57     结束时间：2026-10-13 14:39:06\n")
    rt = parse_round_info(text)
    assert [r[0] for r in rt] == [1, 2]
    assert rt[0][1].hour == 14 and rt[0][1].minute == 33
    assert rt[1][2].minute == 39
