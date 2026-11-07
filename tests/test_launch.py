"""应用启动（sysui_multi_action APP_TRANSITION）解析与慢启动分析测试。"""
import zipfile
from datetime import datetime, timedelta

import pytest

from memscope import analysis as da
from memscope.loader import load_dataset
from memscope.model import Dataset, Event, RoundData
from memscope.parsers.events import parse_events
from tests.test_dynamic import _mk_ds, _mk_pkg

# MTK mobilelog（微秒时间戳）：cold 冷启动，ms=425，转场 91，reason=1
LAUNCH_MTK = (
    '10-13 14:23:06.520935  1665  1725 I sysui_multi_action: '
    '[319,91,321,85,322,425,325,2047,757,761,758,7,759,1,'
    '806,com.debug.loggerui,871,com.debug.loggerui.MainActivity,'
    '904,com.mediatek.engineermode,905,0,945,67,1320,4,1321,23]')
# 展锐 ylog（毫秒）：hot 热启动，ms=0，reason=5 返回桌面
LAUNCH_UNISOC = (
    '10-26 16:19:20.364  1112  1220 I sysui_multi_action: '
    '[319,209,322,0,325,4084,757,761,758,9,759,5,'
    '806,com.transsion.hilauncher,'
    '871,com.android.launcher3.uioverrides.QuickstepLauncher,'
    '904,com.google.android.setupwizard,905,0,1320,4,1321,23]')
_LAUNCH_KILL = ('10-13 14:25:00.000000 2171 2671 I am_kill : '
                '[0,10973,com.android.vending,905,stop reason,86880]')

OPTS = da.Options(**da.default_options('dynamic'))
T0 = datetime(2026, 10, 13, 10, 0, 0)


def _mk_ev(t, ltype='cold', ms=100, code=2, proc='com.x', comp='com.x.A',
           caller='', phase='test', delay=50):
    return Event(t=t, kind='launch', proc=proc, comp=comp, caller=caller,
                 code=code, ltype=ltype, ms=ms, delay_ms=delay, phase=phase)


def _mk_launch_ds(n, launches=None, base=T0):
    """带轮次 start 的最小 Dataset + launches。"""
    ds = _mk_ds([100.0] * n, n=n)
    for i, rd in enumerate(ds.rounds):
        rd.start = base + timedelta(minutes=10 * i)
    ds.launches = launches or []
    return ds


def _build(ds, opts=OPTS, axis='round', hours_total=None, ev_hours=None):
    _ha, iv, hta, _g, _vh = da._hours(ds)
    return da.build_launch(ds, opts,
                           hours_total if hours_total is not None else hta,
                           iv,
                           ev_hours if ev_hours is not None else hta,
                           axis)


# ---------- parse_events ----------
def test_parse_launch_mtk_unisoc():
    evs, _ = parse_events([LAUNCH_MTK], 2026, None, None, extra=True)
    assert len(evs) == 1
    e = evs[0]
    assert e.kind == 'launch' and e.ltype == 'cold'        # 758=7
    assert e.ms == 425 and e.delay_ms == 91               # 322 / 319
    assert e.code == 1                                    # 759
    assert e.proc == 'com.debug.loggerui'                 # 806
    assert e.comp == 'com.debug.loggerui.MainActivity'    # 871
    assert e.caller == 'com.mediatek.engineermode'        # 904
    evs2, _ = parse_events([LAUNCH_UNISOC], 2026, None, None, extra=True)
    assert len(evs2) == 1
    e2 = evs2[0]
    assert e2.ltype == 'hot' and e2.ms == 0 and e2.delay_ms == 209
    assert e2.code == 5 and e2.proc == 'com.transsion.hilauncher'
    assert e2.caller == 'com.google.android.setupwizard'


def test_parse_launch_types_and_filter():
    def _line(transition=761, ltype=7, drawn=425):
        return ('10-13 14:23:06.520935  1665  1725 I sysui_multi_action: '
                f'[319,91,757,{transition},758,{ltype},759,2,322,{drawn},'
                '806,com.x,871,com.x.Main]')
    evs, _ = parse_events([_line(ltype=8), _line(ltype=9)], 2026,
                          None, None, extra=True)
    assert [e.ltype for e in evs] == ['warm', 'hot']
    # 757≠761（如 755 NOTIFICATION_SHADE）丢弃
    evs3, _ = parse_events([_line(transition=755)], 2026, None, None,
                           extra=True)
    assert evs3 == []
    # 758 非 7/8/9 丢弃
    evs4, _ = parse_events([_line(ltype=4)], 2026, None, None, extra=True)
    assert evs4 == []
    # 缺 322 → ms None（仍计入 launch，分析时按无绘制时延排除）
    no_drawn = ('10-13 14:23:06.520935  1665  1725 I sysui_multi_action: '
                '[319,91,757,761,758,7,759,2,806,com.x,871,com.x.Main]')
    evs5, _ = parse_events([no_drawn], 2026, None, None, extra=True)
    assert len(evs5) == 1 and evs5[0].ms is None
    # extra=False 不解析
    evs6, _ = parse_events([LAUNCH_MTK], 2026, None, None, extra=False)
    assert evs6 == []


def test_load_events_split(tmp_path):
    """load_events：launch 从 ds.events 拆出到 ds.launches。"""
    root = _mk_pkg(tmp_path / 'pkg', 'R1', 'SN_A', n=2)
    other_757 = LAUNCH_MTK.replace('757,761', '757,755')
    with zipfile.ZipFile(root / 'mobilelog.zip', 'w') as z:
        z.writestr('events_log_1',
                   '\n'.join([LAUNCH_MTK, other_757, _LAUNCH_KILL]))
    ds = load_dataset(root, 'A', kind='dynamic')
    assert all(e.kind != 'launch' for e in ds.events)
    assert [e.kind for e in ds.events] == ['kill']
    assert len(ds.launches) == 1
    e = ds.launches[0]
    assert e.kind == 'launch' and e.ltype == 'cold' and e.ms == 425
    assert e.phase == 'test'
    es = ds.event_source
    assert es['events'] == 1 and es['launches'] == 1
    # first/last 只算非 launch 事件
    assert es['first'] == '2026-10-13 14:25:00'


# ---------- build_launch ----------
def test_build_launch_slow_threshold():
    ds = _mk_launch_ds(3)
    t = T0 + timedelta(minutes=5)
    ds.launches = [
        _mk_ev(t, 'cold', 3000),                        # 冷=3000 非慢
        _mk_ev(t + timedelta(minutes=1), 'cold', 3001),  # 冷 3001 慢
        _mk_ev(t + timedelta(minutes=2), 'warm', 3001),  # 温 3001 慢
        _mk_ev(t + timedelta(minutes=3), 'hot', 1000),   # 热=1000 非慢
        _mk_ev(t + timedelta(minutes=4), 'hot', 1001),   # 热 1001 慢
    ]
    s = _build(ds)
    assert s['available'] and s['total'] == 5 and s['slow'] == 3
    assert s['slow_rate'] == pytest.approx(0.6)
    assert s['by_type']['cold']['n'] == 2
    assert s['by_type']['cold']['slow'] == 1
    assert s['by_type']['warm']['slow'] == 1
    assert s['by_type']['hot']['n'] == 2 and s['by_type']['hot']['slow'] == 1
    # 5 条都在第 1 轮窗口（10:00–10:10）内
    assert s['buckets']['unit'] == 'round' and len(s['buckets']['total']) == 3
    assert s['buckets']['total'][0] == 5 and s['buckets']['slow'][0] == 3
    # 自定义门限生效
    opts2 = da.Options(**{**da.default_options('dynamic'),
                          'slow_cw': 2000, 'slow_hot': 500})
    s2 = _build(ds, opts=opts2)
    assert s2['by_type']['cold']['slow'] == 2
    assert s2['by_type']['hot']['slow'] == 2
    assert s2['slow'] == 5


def test_build_launch_excluded_and_boot():
    ds = _mk_launch_ds(2)
    t = T0 + timedelta(minutes=1)
    ds.launches = [
        _mk_ev(t, 'hot', 0, code=5),            # 返回桌面（ms=0）
        _mk_ev(t, 'cold', 0, code=2),           # 无绘制时延（ms=0）
        _mk_ev(t, 'warm', None, code=3),        # 无绘制时延（ms 缺失）
        _mk_ev(t, 'cold', 5000, phase='boot'),  # boot 阶段不计
    ]
    s = _build(ds)
    assert s['available']            # test 阶段有记录（含被排除的）
    assert s['total'] == 0 and s['slow'] == 0 and s['slow_rate'] is None
    assert s['excluded'] == {'recents': 1, 'no_delay': 2}
    assert s['apps'] == [] and len(s['list']['t']) == 0
    # 只有 boot 阶段记录 → 无数据
    ds2 = _mk_launch_ds(2)
    ds2.launches = [_mk_ev(t, 'cold', 100, phase='boot')]
    s2 = _build(ds2)
    assert not s2['available'] and s2['excluded'] == {'recents': 0,
                                                    'no_delay': 0}


def test_build_launch_round_and_hour_buckets():
    # 归轮：t 在 round1 内、round3 内、首轮之前
    ds = _mk_launch_ds(3)
    ds.launches = [
        _mk_ev(T0 + timedelta(minutes=5), 'cold', 100),    # → round 0
        _mk_ev(T0 + timedelta(minutes=25), 'cold', 100),   # → round 2
        _mk_ev(T0 - timedelta(minutes=5), 'cold', 100),    # 早于首轮 → None
    ]
    s = _build(ds)
    assert s['buckets']['total'] == [1, 0, 1]
    assert s['outside'] == 1                  # 首轮开始之前 → 窗口外
    assert s['list']['ri'] == [0, 2]          # list 只含窗口内记录
    assert s['list']['h'][0] == pytest.approx(5 / 60, abs=1e-4)
    # hour 桶（n>30 口径；直接传 axis='hour'）；轮次需覆盖到 hour 3
    ds2 = _mk_launch_ds(24)   # 10min×23 → 末轮 start≈T0+3.8h
    ds2.launches = [
        _mk_ev(T0 + timedelta(minutes=5), 'cold', 5000),   # hour 0
        _mk_ev(T0 + timedelta(hours=3), 'cold', 5000),     # hour 3
    ]
    s2 = _build(ds2, axis='hour', hours_total=4.0)
    assert s2['buckets']['unit'] == 'hour'
    assert len(s2['buckets']['total']) == 4
    assert s2['buckets']['total'][0] == 1 and s2['buckets']['total'][3] == 1
    assert s2['buckets']['slow'][3] == 1


def test_build_launch_apps_sort():
    t = T0 + timedelta(minutes=1)
    ds = _mk_launch_ds(2)
    ds.launches = [
        _mk_ev(t, 'cold', 5000, proc='com.b'),             # slow×2
        _mk_ev(t, 'cold', 5001, proc='com.b'),
        _mk_ev(t, 'hot', 2000, proc='com.z'),              # slow×1（pkg 大）
        _mk_ev(t, 'warm', 4000, proc='com.a'),             # slow×1（pkg 小）
        _mk_ev(t, 'cold', 100, proc='com.m'),              # 非慢 n=1
    ]
    s = _build(ds)
    assert [a['pkg'] for a in s['apps']] == ['com.b', 'com.a', 'com.z',
                                           'com.m']
    b = s['apps'][0]
    assert b['n'] == 2 and b['slow'] == 2 and b['cold'] == 2
    assert b['slow_cold'] == 2 and b['slow_hot'] == 0
    assert b['p50'] == 5000.5 and b['max'] == 5001
    z = s['apps'][2]
    assert z['hot'] == 1 and z['slow_hot'] == 1


def test_build_launch_window():
    """窗口外 test 记录不计入 total/slow/excluded，计 outside。"""
    ds = _mk_launch_ds(3)   # start T0,+10,+20；iv=10 → win_end=T0+30
    t_in, t_out = T0 + timedelta(minutes=5), T0 + timedelta(minutes=40)
    ds.launches = [
        _mk_ev(t_in, 'cold', 5000),            # 窗口内，慢
        _mk_ev(t_in, 'cold', 0, code=5),       # 窗口内，excluded
        _mk_ev(t_out, 'cold', 5000),           # 窗口外
        _mk_ev(t_out, 'cold', 0, code=2),      # 窗口外（excluded 也不算）
    ]
    s = _build(ds)
    assert s['available']                      # test 阶段有记录（含窗口外）
    assert s['total'] == 1 and s['slow'] == 1
    assert s['excluded'] == {'recents': 1, 'no_delay': 0}
    assert s['outside'] == 2
    assert s['window'] == {'start': '2026-10-13 10:00:00',
                          'end': '2026-10-13 10:30:00'}
    # 只有窗口外记录 → available True 但全不计入
    ds.launches = [_mk_ev(t_out, 'cold', 5000)]
    s2 = _build(ds)
    assert s2['available'] and s2['total'] == 0 and s2['outside'] == 1
    # 无轮次 → window None，无窗口限制全计入
    ds0 = Dataset(label='x')
    ds0.launches = [_mk_ev(T0, 'cold', 100)]
    s0 = _build(ds0)
    assert s0['window'] is None and s0['total'] == 1


def test_build_launch_window_end_rule():
    """win_end = max(各轮 end) 与 rounds[-1].start+iv 的较大者。"""
    ds = _mk_launch_ds(3)
    for rd in ds.rounds:
        rd.end = rd.start + timedelta(minutes=5)
    # max end = T0+25 < last_start+iv = T0+30 → 取 T0+30
    ds.launches = [_mk_ev(T0 + timedelta(minutes=28), 'cold', 100),
                   _mk_ev(T0 + timedelta(minutes=35), 'cold', 100)]
    s = _build(ds)
    assert s['window']['end'] == '2026-10-13 10:30:00'
    assert s['total'] == 1 and s['outside'] == 1
    # end 更大 → 取 end
    ds.rounds[-1].end = T0 + timedelta(minutes=45)
    s2 = _build(ds)
    assert s2['window']['end'] == '2026-10-13 10:45:00'
    assert s2['total'] == 2 and s2['outside'] == 0


# ---------- analyze 集成 ----------
def test_analyze_launch_key_and_axis():
    """n≤30 → 轮分桶；n>30 → 小时分桶；launch 键结构完整。"""
    a = _mk_launch_ds(11, launches=[_mk_ev(T0 + timedelta(minutes=1),
                                         'cold', 5000)])
    b = _mk_launch_ds(11)
    R = da.analyze(a, b, OPTS)
    lau = R['launch']
    assert set(lau) == {'thr', 'a', 'b'}
    assert lau['thr'] == {'cold': 3000, 'warm': 3000, 'hot': 1000}
    assert R['meta']['slow_cw'] == 3000 and R['meta']['slow_hot'] == 1000
    assert lau['a']['available'] and not lau['b']['available']
    assert lau['a']['buckets']['unit'] == 'round'
    a40 = _mk_launch_ds(40)
    b40 = _mk_launch_ds(40)
    R2 = da.analyze(a40, b40, OPTS)
    assert R2['launch']['a']['buckets']['unit'] == 'hour'


def test_findings_slow_launch_up_down():
    # ra=0 → 只要求 B slow ≥ 3
    a = _mk_launch_ds(11)
    b = _mk_launch_ds(11)
    t = T0 + timedelta(minutes=1)
    a.launches = [_mk_ev(t, 'cold', 100, proc='com.x') for _ in range(10)]
    b.launches = ([_mk_ev(t, 'cold', 5000, proc='com.y')
                   for _ in range(4)]
                  + [_mk_ev(t, 'cold', 100, proc='com.y')
                     for _ in range(10)])
    R = da.analyze(a, b, OPTS)
    f = [x for x in R['findings'] if x['cat'] == '启动']
    assert len(f) == 1
    assert f[0]['sev'] == 'mid' and f[0]['link'] == '#tab=launch'
    assert '慢启动率升高' in f[0]['title']
    assert 'B 侧最多：com.y 4 次' in f[0]['detail']
    # 对称下降 → info
    R2 = da.analyze(b, a, OPTS)
    f2 = [x for x in R2['findings'] if x['cat'] == '启动']
    assert len(f2) == 1 and f2[0]['sev'] == 'info'
    assert '慢启动率下降' in f2[0]['title']
    # B slow < 3 → 不报
    b.launches = b.launches[:2] + b.launches[6:]
    R3 = da.analyze(a, b, OPTS)
    assert not any(x['cat'] == '启动' for x in R3['findings'])


def test_findings_launch_one_side():
    a = _mk_launch_ds(11, launches=[_mk_ev(T0 + timedelta(minutes=1))])
    b = _mk_launch_ds(11)
    R = da.analyze(a, b, OPTS)
    f = [x for x in R['findings']
         if x['cat'] == '数据' and 'APP_TRANSITION' in x['title']]
    assert len(f) == 1 and f[0]['sev'] == 'low'
    assert 'B 侧无应用启动记录' in f[0]['title']
    # 两侧都无 → 无该 finding
    R2 = da.analyze(_mk_launch_ds(11), _mk_launch_ds(11), OPTS)
    assert not any('APP_TRANSITION' in x['title']
                   for x in R2['findings'])


# ---------- 渲染 ----------
def test_markdown_excel_launch(tmp_path):
    from memscope.render import excel, markdown
    t = T0 + timedelta(minutes=1)
    a = _mk_launch_ds(11, launches=[
        _mk_ev(t, 'cold', 5000, proc='com.slow.app',
               comp='com.slow.app.Main', caller='com.caller'),
        _mk_ev(t, 'hot', 0, code=5, proc='com.home')])
    b = _mk_launch_ds(11, launches=[_mk_ev(t, 'cold', 100,
                                           proc='com.fast.app')])
    R = da.analyze(a, b, OPTS)
    md = markdown.render(R)
    assert '## 12. 应用启动（慢启动：冷/温 > 3000ms，热 > 1000ms）' in md
    assert '## 13. 内存压力' in md and '## 14. 口径说明' in md
    assert 'com.slow.app' in md
    xp = tmp_path / 'd.xlsx'
    excel.render(R, xp)
    import openpyxl
    wb = openpyxl.load_workbook(xp, read_only=True)
    assert '应用启动' in wb.sheetnames and '启动汇总' in wb.sheetnames
    ws = wb['应用启动']
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == '数据集'
    a_rows = [r for r in rows if r[0] == 'A']
    assert len(a_rows) == 1                       # 只有效记录进明细
    assert a_rows[0][3] == 'com.slow.app' and a_rows[0][5] == '冷'
    assert a_rows[0][8] == '2 窗口绘制'
    assert a_rows[0][10] == '是' and a_rows[0][2] == 1
    ws2 = wb['启动汇总']
    s_rows = [r for r in ws2.iter_rows(values_only=True) if r[0] == 'A']
    assert any(r[1] == 'com.slow.app' and r[2] == 1 and r[6] == 1
               for r in s_rows)


def test_markdown_no_launch():
    from memscope.render import markdown
    R = da.analyze(_mk_launch_ds(11), _mk_launch_ds(11), OPTS)
    md = markdown.render(R)
    assert '无应用启动记录（events 日志中无 APP_TRANSITION）' in md


# ---------- CLI ----------
def test_cli_slow_launch(tmp_path, monkeypatch):
    from memscope.cli import build_parser, main
    a = build_parser().parse_args(['static', 'compare', 'x', 'y',
                                   '--slow-launch', '2500', '800'])
    assert a.slow_launch == [2500, 800]
    assert build_parser().parse_args(
        ['static', 'compare', 'x', 'y']).slow_launch == [3000, 1000]
    # 参数进入 Options
    dirA = _mk_pkg(tmp_path / 'pA', 'R1', 'SN_A', n=3)
    dirB = _mk_pkg(tmp_path / 'pB', 'R2', 'SN_B', n=3)
    for d in (dirA, dirB):
        f = d / 'device_info.txt'
        f.write_text(f.read_text(encoding='utf-8')
                     .replace('Test Type: dynamic', 'Test Type: static'),
                     encoding='utf-8')
    seen = {}
    orig = da.analyze

    def _spy(dsa, dsb, opts):
        seen['opts'] = opts
        return orig(dsa, dsb, opts)
    monkeypatch.setattr(da, 'analyze', _spy)
    rc = main(['static', 'compare', str(dirA), str(dirB),
               '-o', str(tmp_path / 'o'), '--slow-launch', '2500', '800'])
    assert rc == 0
    assert seen['opts'].slow_cw == 2500 and seen['opts'].slow_hot == 800
