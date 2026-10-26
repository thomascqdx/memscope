"""动态内存分析单元测试（微型内联数据，不依赖样本包）。"""
import json
import pytest

from memscope import dynamic_analysis as da
from memscope.loader import check_test_type, load_dataset
from memscope.model import Dataset, RoundData
from memscope.parsers.dumpsys import parse_dumpsys
from memscope.parsers.events import dedupe_lines, parse_events
from memscope.parsers.vmstat import parse_vmstat


# ---------- pct / dist / trend ----------
def test_pct_linear_inclusive():
    s = list(range(1, 101))  # 1..100
    assert da.pct(s, 50) == pytest.approx(50.5)
    assert da.pct(s, 90) == pytest.approx(90.1)
    assert da.pct(s, 95) == pytest.approx(95.05)
    assert da.pct(s, 0) == 1 and da.pct(s, 100) == 100
    assert da.pct([], 90) is None
    assert da.pct([7.0], 90) == 7.0


def test_dist():
    s = [None, 10.0, 20.0, None, 30.0]
    d = da.dist(s)
    assert d['k'] == 3 and d['n'] == 5 and d['rate'] == pytest.approx(0.6)
    assert d['mi'] == pytest.approx(12.0)       # 缺席按 0
    assert d['mean'] == pytest.approx(20.0)
    assert d['min'] == 10.0 and d['max'] == 30.0
    assert d['p50'] == pytest.approx(20.0)
    d0 = da.dist([None, None])
    assert d0['k'] == 0 and d0['mean'] is None and d0['p90'] is None


def test_trend():
    # 线性增长：y = 100 + 2h
    hours = [float(i) for i in range(20)]
    s = [100 + 2 * h for h in hours]
    t = da.trend(hours, s)
    assert t['slope'] == pytest.approx(2.0) and t['r2'] == pytest.approx(1.0)
    # head=前10%(3点)中位=102, tail=后3点中位=136 → delta=34
    assert t['delta'] == pytest.approx(34.0)
    # 有缺省轮
    s2 = [None] * 5 + s[5:]
    t2 = da.trend(hours, s2)
    assert t2 is not None and t2['slope'] == pytest.approx(2.0)
    # 出现轮 <10 → None
    assert da.trend(hours, [None] * 15 + [1.0] * 5) is None


def _mk_ds(proc_series, level='Native', proc='procX', vmstats=None,
           reboots=None, n=None):
    """构造最小 Dataset：单进程。"""
    ds = Dataset(label='X')
    n = n or len(proc_series)
    for i, v in enumerate(proc_series):
        rd = RoundData(index=i + 1)
        rd.start = None
        rd.rss = {level: {proc: v}} if v is not None else {}
        rd.pss = {level: {'total': v or 0.0, 'procs': {proc: v or 0.0}}}
        rd.uptime_ms = (i + 1) * 1000
        if vmstats:
            rd.vmstat = vmstats[i]
        ds.rounds.append(rd)
    ds.reboots = reboots or []
    return ds


def _analyze(proc_a, proc_b, opts=None, n=20):
    dsa = _mk_ds(proc_a, n=n)
    dsb = _mk_ds(proc_b, n=n)
    return da.analyze(dsa, dsb, opts or da.DynOptions())


def test_leak_positive_negative():
    # B 线性泄漏 100→300（delta≈190 > max(10, head*20%)≈26）
    flat = [100.0] * 20
    grow = [100.0 + i * 10 for i in range(20)]
    R = _analyze(flat, grow)
    p = R['metrics']['rss']['procs']['procX']
    assert p['leak_b'] and not p['leak_a']
    assert any(l['ds'] == 'B' and l['name'] == 'procX' for l in R['leaks'])

    # 平稳 + 噪声不报
    noise = [100.0 + (i % 3) for i in range(20)]
    R2 = _analyze(flat, noise)
    assert not R2['metrics']['rss']['procs']['procX']['leak_b']

    # 边界 rate=0.9 恰好满足 resident（>=），可报
    sparse = grow[:18] + [None, None]
    R3 = _analyze(flat, sparse)
    p3 = R3['metrics']['rss']['procs']['procX']
    assert p3['db']['rate'] == pytest.approx(0.9)
    assert p3['resident_b'] and p3['leak_b']
    # rate 0.85 一定不报
    sparse2 = grow[:17] + [None] * 3
    R4 = _analyze(flat, sparse2)
    p4 = R4['metrics']['rss']['procs']['procX']
    assert not p4['resident_b'] and not p4['leak_b']


# ---------- vmstat ----------
def test_vmstat_parse():
    t = "nr_free_pages 16783\npswpin 246610\npswpout 670022\nallocstall_movable 4908\n" \
        "allocstall_normal 2268\npgmajfault 269108\nworkingset_refault_file 456656\n" \
        "oom_kill 0\nbadline\nfoo bar\ncompact_stall 44\n"
    v = parse_vmstat(t)
    assert v['pswpin'] == 246610 and v['pswpout'] == 670022
    assert v['allocstall_movable'] == 4908 and v['allocstall_normal'] == 2268
    assert v['pgmajfault'] == 269108 and v['workingset_refault_file'] == 456656
    assert 'nr_free_pages' not in v and 'foo' not in v and 'badline' not in v


def test_pressure_deltas_reboot_negative():
    vm = [{'pswpout': 0}, {'pswpout': 1024}, {'pswpout': 512}, {'pswpout': 2048}]
    ds = _mk_ds([1.0] * 4, vmstats=vm, n=4)
    # round 3→4 之间有一次重启（deltas 跨 idx 3 不计）
    ds.reboots = [{'after_round': 3, 'round': 4}]
    pr = da.build_pressure(ds, ds, [0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 2.0, 3.0])
    row = next(c for c in pr['counters'] if c['name'] == 'pswpout')
    # deltas: [None, 1024页→4MB, 512-1024<0→None, 重启→None]
    assert row['a'][0] is None and row['a'][1] == pytest.approx(4.0)
    assert row['a'][2] is None and row['a'][3] is None
    assert row['sa']['total'] == pytest.approx(4.0)


# ---------- events extra ----------
LINES = [
    '10-22 14:00:00.000000    1    1 I boot_progress_start: 24928',
    '10-22 14:05:00.000000 2171 2212 I am_proc_start: [0,7530,10160,com.foo.app,bound-service,{com.foo.app/.S},caller=android/1000]',
    '10-22 14:10:00.000000  583  583 I killinfo: [7530,10160,925,50,106672,10,757068,815956]',
    '10-22 14:11:00.000000  583  583 I killinfo: [9999,10161,900,50,2048,8,1]',
    '10-22 14:11:05.000000 2171 2212 I am_proc_died: [0,9999,com.bar.app,905,19]',
    '10-22 14:12:00.000000  583  583 I killinfo: [8888,10162,950,50,1024,3,1]',
    '10-22 14:13:00.000000 2171 2862 I am_anr  : [999,21823,com.king.candycrushsaga,549994052,Input dispatching timed out, extra detail]',
    '10-22 14:14:00.000000 2171 2862 I am_crash: [10797,0,com.google.android.photopicker,549994053,java.lang.IllegalArgumentException,bad key]',
    '10-22 14:15:00.000000 2171 2671 I am_low_memory: 2',
    '10-22 14:16:00.000000 2171 3102 I am_kill : [0,10973,com.android.vending,905,stop reason,86880]',
]
from datetime import datetime
R1 = datetime(2026, 10, 22, 14, 4, 0)


def test_events_extra_kinds():
    evs, boot = parse_events(dedupe_lines(LINES), 2026, R1, 60000, extra=True)
    kinds = [e.kind for e in evs]
    assert kinds.count('lmk') == 3 and 'anr' in kinds and 'crash' in kinds
    assert 'lowmem' in kinds
    lmk = [e for e in evs if e.kind == 'lmk']
    assert lmk[0].pid == 7530 and lmk[0].proc == 'com.foo.app'  # start 回填
    assert lmk[0].rss_mb == pytest.approx(106672 / 1024)
    assert lmk[0].code == 10 and lmk[0].adj == '925'
    assert lmk[1].proc == 'com.bar.app'   # died 10s 内回退
    assert lmk[2].proc == 'pid:8888'      # 无映射
    anr = next(e for e in evs if e.kind == 'anr')
    assert anr.proc == 'com.king.candycrushsaga'
    assert 'Input dispatching timed out' in anr.reason and 'extra detail' in anr.reason
    cr = next(e for e in evs if e.kind == 'crash')
    assert cr.proc == 'com.google.android.photopicker'
    assert cr.reason.startswith('java.lang.IllegalArgumentException: ')
    k = next(e for e in evs if e.kind == 'kill')
    assert k.pid == 10973 and k.rss_mb == pytest.approx(86880 / 1024)


def test_events_static_no_extra():
    evs, _ = parse_events(dedupe_lines(LINES), 2026, R1, 60000, extra=False)
    assert not any(e.kind in ('lmk', 'anr', 'crash', 'lowmem') for e in evs)


def test_lmk_reason_names():
    assert da._lmk_reason(8) == 'LOW_MEM'
    assert da._lmk_reason(10) == '厂商扩展(10)'
    assert da._lmk_reason(None) == '未知'


# ---------- dumpsys 尾部可选 / check_test_type / failed_rounds ----------
def test_dumpsys_tail_missing_dmabuf_no_raise():
    from tests.test_parsers import DUMPSYS
    t = DUMPSYS.replace('DMA-BUF:   145,124K (       12K mapped +   145,112K unmapped)\n', '')
    t = t.replace('      GPU:   193,304K (  141,864K dmabuf +    51,440K private)\n', '')
    tail = parse_dumpsys(t)['tail']
    assert tail['DMA-BUF'] is None and tail['GPU'] is None
    assert tail['Total RAM'] == pytest.approx(5585816 / 1024)


def test_check_test_type_dynamic():
    ds = Dataset(label='A')
    ds.device['Test Type'] = 'static'
    err = check_test_type(ds, 'dynamic')
    assert err and '不是动态内存测试包' in err
    ds.device['Test Type'] = ' dynamic '
    assert check_test_type(ds, 'dynamic') is None


# ---------- 最小动态目录包 → CLI 端到端 ----------
DUMPSYS_MIN = """Applications Memory Usage (in Kilobytes):
Uptime: {up} Realtime: {up}

Total PSS by OOM adjustment:
    500,000K: Native
        10,000K: procX (pid 100)

Total PSS by category:

Global Mapped Bitmaps:
  Mapped: 0 bitmaps,         0K

Total RAM: 5,585,816K (status normal)
 Free RAM: 3,000,000K (1,000,000K cached pss + 1,900,000K cached kernel +   100,000K free)
 Used RAM: 2,500,000K (1,500,000K used pss + 1,000,000K kernel)
 Lost RAM:    5,816K
     ZRAM:   195,852K physical used for   736,000K in swap (4,189,356K total swap)
"""
PROCSTATUS_MIN = """Uptime:383 Realtime:383
ProcName:procX Pid:100 OomScore:-1000 LevelName:Native VmRSS+VmSwap:{rss}KB VmSwap:0KB ProcType:system
"""
VMSTAT_MIN = "pswpin {i}00\npswpout {i}00\npgmajfault 5\n"


def _mk_pkg(root: 'Path', rom: str, sn: str, n=6, bad=None):
    from pathlib import Path
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'device_info.txt').write_text(
        f'Device Model: Infinix X1202\nSN Number: {sn}\nTest Type: dynamic\n'
        f'Rom Version: {rom}\n\nPackage List:\npackage:com.a\npackage:com.b\n',
        encoding='utf-8')
    ri = ''.join(
        f'[{i:04d}]     开始时间：2026-10-13 {10 + i}:00:00     结束时间：2026-10-13 {10 + i}:01:00\n'
        for i in range(1, n + 1))
    (root / 'round_info.txt').write_text(ri, encoding='utf-8')
    for i in range(1, n + 1):
        d = root / f'round_{i}'
        d.mkdir()
        if i != bad:
            (d / 'dumpsys_meminfo.txt').write_text(
                DUMPSYS_MIN.format(up=i * 60000), encoding='utf-8')
        (d / 'procStatus_sortedByLevel.txt').write_text(
            PROCSTATUS_MIN.format(rss=1024 * (10 + i)), encoding='utf-8')
        (d / 'proc_meminfo.txt').write_text(
            'MemTotal:        5585816 kB\nMemFree:          100000 kB\n', encoding='utf-8')
        (d / 'proc_vmstat.txt').write_text(VMSTAT_MIN.format(i=i), encoding='utf-8')
    (root / 'failed_rounds.log').write_text(
        '[0009] 开始时间：2026-10-13 19:00:00 失败时间：2026-10-13 19:00:05 '
        '原因：dumpsys meminfo 未获取到有效结果\n', encoding='utf-8')
    return root


def test_dynamic_e2e_dir_pkg(tmp_path):
    from memscope.cli import main
    dirA = _mk_pkg(tmp_path / 'pkgA', 'R1', 'SN_A', n=6)
    dirB = _mk_pkg(tmp_path / 'pkgB', 'R2', 'SN_B', n=6, bad=3)
    out = tmp_path / 'o'
    rc = main(['dynamic', 'compare', str(dirA), str(dirB), '-o', str(out)])
    assert rc == 0
    for f in ('report.md', 'report.html', 'data.xlsx'):
        assert (out / f).exists() and (out / f).stat().st_size > 500
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert 'src="http' not in html and 'href="http' not in html
    md = (out / 'report.md').read_text(encoding='utf-8')
    assert '# 动态内存对比报告' in md

    # 无效轮剔除 + JSON 可序列化
    dsa = load_dataset(dirA, 'A', kind='dynamic')
    dsb = load_dataset(dirB, 'B', kind='dynamic')
    assert dsa.n == 6 and dsb.n == 5          # B 第 3 轮缺 dumpsys → 无效
    assert any(d['index'] == 3 for d in dsb.failed_rounds)
    assert any(d['index'] == 9 for d in dsa.failed_rounds)  # failed_rounds.log
    assert dsa.rounds[0].vmstat.get('pswpout') == 100
    R = da.analyze(dsa, dsb, da.DynOptions())
    json.dumps(R, ensure_ascii=False)
    R['report_md'] = 'x'
    json.dumps(R, ensure_ascii=False)


# ---------- 泄漏规则细化 ----------
def test_level_leak_resident_only():
    """非常驻层级（Visible）增长不报泄漏；常驻层级（Native）报。"""
    flat = [100.0] * 20
    grow = [100.0 + i * 10 for i in range(20)]
    R = _analyze(flat, grow) if False else None
    r_vis = da.analyze(_mk_ds(flat, level='Visible'),
                       _mk_ds(grow, level='Visible'), da.DynOptions())
    lv = r_vis['metrics']['rss']['level_rows']
    assert lv[0]['level'] == 'Visible' and not lv[0]['leak_b']
    # 层级不报，但进程 procX 是常驻进程，仍应报进程泄漏
    assert r_vis['leaks'] and all(l['kind'] == 'proc' for l in r_vis['leaks'])
    r_nat = da.analyze(_mk_ds(flat, level='Native'),
                       _mk_ds(grow, level='Native'), da.DynOptions())
    lv2 = r_nat['metrics']['rss']['level_rows']
    assert lv2[0]['level'] == 'Native' and lv2[0]['leak_b']


def test_test_tool_no_leak_no_proc_finding():
    """monkey 前缀进程不报泄漏、不进进程类 findings（数据保留）。"""
    flat = [100.0] * 20
    grow = [100.0 + i * 10 for i in range(20)]
    r = da.analyze(_mk_ds(flat, proc='com.android.commands.monkey.transsion'),
                   _mk_ds(grow, proc='com.android.commands.monkey.transsion'),
                   da.DynOptions())
    p = r['metrics']['rss']['procs']['com.android.commands.monkey.transsion']
    assert p['db']['k'] == 20            # 数据保留
    assert not p['leak_b']
    assert not any('monkey' in f['title'] or 'monkey' in f['link']
                   for f in r['findings'])


def _leak_findings(r, name):
    return [f for f in r['findings'] if f['cat'] == '泄漏'
            and name in f['title']]


def test_leak_findings_merged_and_sev():
    grow = [100.0 + i * 10 for i in range(20)]
    flat = [100.0] * 20
    # 仅 B 泄漏（RSS+PSS 同对象）→ 一条 high finding
    r = da.analyze(_mk_ds(flat), _mk_ds(grow), da.DynOptions())
    fs = _leak_findings(r, 'procX')
    assert len(fs) == 1
    f = fs[0]
    assert f['sev'] == 'high' and 'B 侧疑似泄漏' in f['title']
    assert 'B/RSS' in f['detail'] and 'B/PSS' in f['detail']  # 双口径合并
    assert f['score'] > 0
    # result['leaks'] 仍逐条（rss+pss 各一条）
    assert sum(1 for l in r['leaks'] if l['name'] == 'procX') == 2
    # 两侧都泄漏 → mid，且不再单独出 A 侧条目
    r2 = da.analyze(_mk_ds(grow), _mk_ds(grow), da.DynOptions())
    fs2 = _leak_findings(r2, 'procX')
    assert len(fs2) == 1
    assert fs2[0]['sev'] == 'mid' and '两侧均疑似泄漏' in fs2[0]['title']
    assert 'A/RSS' in fs2[0]['detail'] and 'B/RSS' in fs2[0]['detail']
    # 仅 A → low
    r3 = da.analyze(_mk_ds(grow), _mk_ds(flat), da.DynOptions())
    fs3 = _leak_findings(r3, 'procX')
    assert len(fs3) == 1 and fs3[0]['sev'] == 'low'


def test_findings_score_sort():
    """排序键 (sev, 类别序, -score)：high 内 标准 先于 泄漏/层级。"""
    flat = [100.0] * 20
    grow = [100.0 + i * 10 for i in range(20)]
    r = da.analyze(_mk_ds(flat), _mk_ds(grow), da.DynOptions())
    keys = [(da._SEV_ORDER[f['sev']], da._CAT_ORDER[f['cat']], -f['score'])
            for f in r['findings']]
    assert keys == sorted(keys)
