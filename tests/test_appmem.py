"""进程 meminfo 明细（dumpsys_meminfo_<proc>）：解析器 / dumpsys pids / 加载名解析 / 分析。"""
import pytest

from memscope import analysis as da
from memscope.loader import _load_appmem, _load_round
from memscope.model import Dataset, RoundData
from memscope.parsers.appmeminfo import parse_app_meminfo
from memscope.parsers.dumpsys import parse_dumpsys


# ---------- 解析器 ----------

_APP = """\
Applications Memory Usage (in Kilobytes):
Uptime: 52394919 Realtime: 52394919

** MEMINFO in pid 3551 [com.android.bluetooth] **
                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap    16734    15740      960     5409    17824    32508    20389     8034
 Dalvik Other     2698     2268      228      169     3340
    Other dev       30        8        8        0      624
     .so mmap    10376      384     4740      322    54468
        TOTAL    49289    23484     9872     7504   153676    39996    23813    12098

 App Summary
                       Pss(KB)                        Rss(KB)
                        ------                         ------
           Java Heap:     3380                          18212
"""

_NATIVE = """\
Applications Memory Usage (in Kilobytes):
Uptime: 1924041 Realtime: 1924041
                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap    13828    11784     2044    26564    13828        0        0        0
   EGL mtrack    73190    73190        0        0    73190
        TOTAL   139756    99250     3300    30644   145282        0        0        0
"""

_SYS3 = """\
Applications Memory Usage (in Kilobytes):
Uptime: 505114 Realtime: 505114

** MEMINFO in pid 2171 [system] **
                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap    30689    29800      868    15548    32468    68524    41296    23121
        TOTAL   100000    60000    10000     2000   200000   100000    50000    20000
"""


def test_parse_app_with_header():
    r = parse_app_meminfo(_APP)
    assert r['pid'] == 3551 and r['name'] == 'com.android.bluetooth'
    assert r['rows']['Native Heap'] == pytest.approx(16734 / 1024)
    assert r['rows']['TOTAL'] == pytest.approx(49289 / 1024)
    # 行名含点/空格
    assert r['rows']['.so mmap'] == pytest.approx(10376 / 1024)
    assert r['rows']['Other dev'] == pytest.approx(30 / 1024)
    # App Summary 区段的数字不混入
    assert 'Java Heap' not in r['rows']


def test_parse_native_no_header():
    r = parse_app_meminfo(_NATIVE)
    assert r['pid'] is None and r['name'] is None
    assert r['rows']['EGL mtrack'] == pytest.approx(73190 / 1024)


def test_parse_v3_system_server():
    r = parse_app_meminfo(_SYS3)
    assert r['name'] == 'system' and r['pid'] == 2171


def test_parse_invalid_files():
    assert parse_app_meminfo('No process found for: 13620\n') is None
    assert parse_app_meminfo('') is None
    assert parse_app_meminfo(
        'Applications Memory Usage (in Kilobytes):\nUptime: 1\n') is None


def test_dumpsys_pids_map():
    text = """\
Applications Memory Usage (in Kilobytes):
Uptime: 1 Realtime: 1

Total PSS by OOM adjustment:
    500,000K: Native
         5,423K: zygote64 (pid 627)
         1,000K: camerahalserver (pid 1846)
         2,000K: com.foo (pid 900 / activities)

Total RAM: 3,834,008K (status normal)
"""
    d = parse_dumpsys(text)
    assert d['pids'] == {627: 'zygote64', 1846: 'camerahalserver',
                         900: 'com.foo'}


# ---------- loader 名解析 ----------

def _am(name_hdr=None, total='1024', pid='1'):
    hdr = (f'\n** MEMINFO in pid {pid} [{name_hdr}] **\n' if name_hdr else '')
    return (f'Applications Memory Usage (in Kilobytes):\nUptime: 1{hdr}\n'
            '                   Pss  Private\n'
            '                 Total    Dirty\n'
            '                ------   ------\n'
            '  Native Heap     512      400\n'
            f'        TOTAL   {total}      800\n')


def _got(files):
    return {n: (lambda d=d: d.encode('utf-8')) for n, d in files.items()}


def test_appmem_v3_pid_to_name():
    out = _load_appmem(
        _got({'dumpsys_meminfo__system_bin_camerahalserver_1846.txt':
              _am()}), {1846: 'camerahalserver'}, '3.0')
    assert list(out) == ['camerahalserver']
    assert out['camerahalserver']['TOTAL'] == pytest.approx(1.0)


def test_appmem_v3_strip_pid_fallback():
    out = _load_appmem(
        _got({'dumpsys_meminfo_somedaemon_123.txt': _am()}), {}, '3.0')
    assert list(out) == ['somedaemon']


def test_appmem_v2_keep_suffix():
    # 2.0 不剥 _<digits>：文件名无 pid 后缀，_64 是进程名一部分
    out = _load_appmem(
        _got({'dumpsys_meminfo_audioserver_64.txt': _am()}),
        {64: 'otherproc'}, '2.0')
    assert list(out) == ['audioserver_64']


def test_appmem_system_server_alias():
    out = _load_appmem(
        _got({'dumpsys_meminfo_system_server.txt': _am()}), {}, '2.0')
    assert list(out) == ['system']


def test_appmem_header_wins():
    # 文件名 stem（_pre-initialized_）与头名不一致 → 用头名
    out = _load_appmem(
        _got({'dumpsys_meminfo__pre-initialized__27422.txt':
              _am('tech.palm.id', pid='27422')}), {}, '3.0')
    assert list(out) == ['tech.palm.id']


def test_appmem_same_name_summed():
    files = {'dumpsys_meminfo_a_100.txt': _am('a', '1024', '100'),
             'dumpsys_meminfo_a_200.txt': _am('a', '2048', '200')}
    out = _load_appmem(_got(files), {}, '3.0')
    assert list(out) == ['a']
    assert out['a']['TOTAL'] == pytest.approx(3.0)
    assert out['a']['Native Heap'] == pytest.approx(1.0)


_DUMPSYS = """\
Applications Memory Usage (in Kilobytes):
Uptime: 210324 Realtime: 210324

Total PSS by OOM adjustment:
    500,000K: Native
         5,423K: zygote64 (pid 627)

Total RAM: 3,834,008K (status normal)
"""

_PROCSTATUS = """\
LevelName: Native          VmRSS + Vmswap: 120.00MB
ProcName: zygote64                       Pid: 627    OomScore: -1000 LevelName: Native          VmRSS + VmSwap:  121268.00KB VmSwap:  34684KB ProcType: native
"""


def test_load_round_appmem_invalid_skipped():
    got = _got({'dumpsys_meminfo.txt': _DUMPSYS,
                'procStatus_sortedByLevel.txt': _PROCSTATUS,
                'dumpsys_meminfo_zygote64_627.txt': _am(),
                'dumpsys_meminfo_dead_13620.txt':
                    'No process found for: 13620\n',
                'dumpsys_meminfo_broken_1.txt': b''})
    # b'' thunk → encode() 需要 str；直接换 bytes thunk
    got['dumpsys_meminfo_broken_1.txt'] = lambda: b''
    rd, reason = _load_round(1, got, (None, None), '3.0')
    assert reason is None
    assert list(rd.appmem) == ['zygote64']
    assert rd.appmem['zygote64']['Native Heap'] == pytest.approx(0.5)


# ---------- 分析 ----------

def _ds_am(n, appmem=None, rising=None, proc='px'):
    """n 轮；appmem: {i: {proc: {row: mb}}} 或 rising=(base,step) 简写。"""
    ds = Dataset(label='x')
    for i in range(n):
        rd = RoundData(index=i + 1, uptime_ms=(i + 1) * 1000)
        rd.rss = {'Native': {'other': 1.0}}
        rd.pss = {'Native': {'total': 1.0, 'procs': {'other': 1.0}}}
        if rising is not None:
            base, step = rising
            rd.appmem = {proc: {'Native Heap': base + step * i,
                                'Dalvik Heap': 10.0, 'TOTAL': 50.0}}
        elif appmem and i in appmem:
            rd.appmem = appmem[i]
        ds.rounds.append(rd)
    return ds


def _opts(kind):
    return da.Options(**da.default_options(kind))


def test_appmem_result_shape_both_kinds():
    for kind in ('static', 'dynamic'):
        a = _ds_am(15, rising=(100, 0.1))
        b = _ds_am(15, rising=(120, 0.1))
        R = da.analyze(a, b, _opts(kind))
        am = R['appmem']
        assert am['fields'][0] == 'TOTAL'
        assert am['fields'].index('Native Heap') < am['fields'].index(
            'Dalvik Heap')
        p = am['procs']['px']
        assert set(p) == {'fields', 'a', 'b', 'cov_a', 'cov_b', 'heap',
                          'leak_a', 'leak_b'}
        assert p['cov_a'] == p['cov_b'] == 15
        assert p['a']['k'] == list(range(15))
        assert len(p['a']['v']['Native Heap']) == 15
        assert set(p['heap']['a']) == {'Native Heap', 'Dalvik Heap'}


def test_appmem_field_order_and_missing_zero():
    # 未知字段字母序追加在已知字段之后；有效文件中缺行 → 0
    a = _ds_am(3, appmem={0: {'px': {'TOTAL': 10.0, 'Native Heap': 2.0,
                                     'Zz extra': 1.0}},
                          1: {'px': {'TOTAL': 10.0}},
                          2: {'px': {'Native Heap': 4.0}}})
    b = _ds_am(3)
    R = da.analyze(a, b, _opts('static'))
    am = R['appmem']
    assert am['fields'] == ['TOTAL', 'Native Heap', 'Zz extra']
    p = am['procs']['px']
    assert p['fields'] == ['TOTAL', 'Native Heap', 'Zz extra']
    assert p['a']['k'] == [0, 1, 2]
    # 第 2 轮无 Native Heap 行 → 0；第 3 轮无 TOTAL 行 → 0
    assert p['a']['v']['Native Heap'] == [2.0, 0.0, 4.0]
    assert p['a']['v']['TOTAL'] == [10.0, 10.0, 0.0]
    assert p['cov_a'] == 3 and p['cov_b'] == 0 and p['b'] is None


def test_appmem_heap_leak_flagged():
    n = 20
    a = _ds_am(n, rising=(100, 5))          # 明显上升 → 疑似泄漏
    b = _ds_am(n, rising=(200, 0.01))       # 平 → 不判
    R = da.analyze(a, b, _opts('dynamic'))
    p = R['appmem']['procs']['px']
    assert p['leak_a'] == ['Native Heap'] and p['leak_b'] == []
    hl = [lk for lk in R['leaks']
          if lk['kind'] == 'heap' and lk['name'] == 'px']
    assert len(hl) == 1 and hl[0]['ds'] == 'A'
    assert hl[0]['metric'] == 'Native Heap' and hl[0]['slope'] > 0
    fs = [f for f in R['findings']
          if f['cat'] == '泄漏' and 'px' in f['title']]
    assert any('进程堆' in f['title'] for f in fs)
    assert any('#proc=px&mm=' in f['link'] for f in fs)


def test_appmem_heap_leak_needs_coverage():
    n = 20
    # 10/20 轮有数据且上升：trend 有 ≥10 点，但覆盖率 0.5 < resident 0.9
    a = _ds_am(n, appmem={i: {'px': {'Native Heap': 100.0 + 10 * i,
                                     'TOTAL': 200.0}}
                          for i in range(0, n, 2)})
    b = _ds_am(n)
    R = da.analyze(a, b, _opts('dynamic'))
    p = R['appmem']['procs']['px']
    assert p['heap']['a']['Native Heap'] is not None
    assert p['leak_a'] == []
    assert not any(lk['kind'] == 'heap' for lk in R['leaks'])


def test_appmem_heap_leak_skips_test_tools():
    n = 20
    a = _ds_am(n, rising=(100, 5), proc='com.android.commands.monkey')
    b = _ds_am(n)
    R = da.analyze(a, b, _opts('dynamic'))
    assert R['appmem']['procs']['com.android.commands.monkey'][
        'leak_a'] == []
    assert not any(lk['kind'] == 'heap' for lk in R['leaks'])


def test_appmem_empty_when_no_data():
    a, b = _ds_am(5), _ds_am(5)
    R = da.analyze(a, b, _opts('static'))
    assert R['appmem'] == {'fields': [], 'procs': {}}


def test_appmem_allzero_heap_trend_none_and_excel_skip(tmp_path):
    # native 进程 Dalvik Heap 恒 0 → trend None；Excel 该行不出现
    from memscope.render import excel
    n = 15
    ds = Dataset(label='x')
    for i in range(n):
        rd = RoundData(index=i + 1, uptime_ms=(i + 1) * 1000)
        rd.rss = {'Native': {'other': 1.0}}
        rd.pss = {'Native': {'total': 1.0, 'procs': {'other': 1.0}}}
        rd.appmem = {'nat': {'Native Heap': 50.0 + i,
                             'Dalvik Heap': 0.0, 'TOTAL': 60.0}}
        ds.rounds.append(rd)
    a, b = ds, _ds_am(n)
    R = da.analyze(a, b, _opts('dynamic'))
    p = R['appmem']['procs']['nat']
    assert p['heap']['a']['Dalvik Heap'] is None
    assert p['heap']['a']['Native Heap'] is not None
    out = tmp_path / 'd.xlsx'
    excel.render(R, out)
    import openpyxl
    wb = openpyxl.load_workbook(out, read_only=True)
    rows = [r for r in wb['进程堆趋势'].iter_rows(values_only=True)]
    nat_rows = [r for r in rows if r[1] == 'nat']
    # Dalvik Heap 整字段跳过；Native Heap 保留 A/B 两侧行
    assert [r[2] for r in nat_rows] == ['Native Heap', 'Native Heap']
