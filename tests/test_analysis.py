"""统计与 inhouse 判定单元测试。"""
from datetime import datetime, timedelta

import pytest

from memscope.analysis import (Options, analyze, cnt, gap_info, is_significant,
                               linreg, rmean, vmean)
from memscope.inhouse import base_pkg, classify
from memscope.model import Dataset, RoundData


def _mk_dataset(rounds_pss):
    """rounds_pss: [{level: {proc: mb}}] 每轮的 PSS 进程值（level total=进程和）。"""
    ds = Dataset(label='x')
    t0 = datetime(2026, 1, 1, 10, 0, 0)
    for i, lvmap in enumerate(rounds_pss):
        rd = RoundData(index=i + 1,
                       start=t0 + timedelta(minutes=5 * i),
                       end=t0 + timedelta(minutes=5 * i, seconds=10),
                       uptime_ms=1000)
        for lv, procs in lvmap.items():
            rd.pss[lv] = {'total': sum(procs.values()), 'procs': dict(procs)}
        ds.rounds.append(rd)
    return ds


def test_stats_defs():
    assert cnt([1, None, 2]) == 2
    assert rmean([1, None, 3], 3) == pytest.approx(4 / 3)
    assert vmean([1, None, 3]) == pytest.approx(2.0)
    assert vmean([None]) is None
    g = gap_info(0, 5)
    assert g['txt'] == '新增' and g['gap'] == 5
    g = gap_info(5, 0)
    assert g['txt'] == '消失'
    g = gap_info(10, 15)
    assert g['pct'] == pytest.approx(50.0)


def test_significant():
    o = Options(gap_abs=5, gap_pct=10)
    assert not is_significant(100, 103, o)     # 绝对值不足
    assert is_significant(100, 120, o)         # 20% ≥ 10%
    assert is_significant(0, 6, o)             # 新增
    assert is_significant(6, 0, o)             # 消失
    assert not is_significant(0, 0, o)
    assert not is_significant(100, 104, o)     # 4 < 5 abs, 4% < 10%


def test_linreg():
    slope, r2 = linreg([1, 2, 3, 4, 5, 6])
    assert slope == pytest.approx(1.0) and r2 == pytest.approx(1.0)
    slope, r2 = linreg([5, 4, 3, 4, 5, 4])
    assert abs(slope) < 0.5 and r2 < 0.3


def test_level_changed_and_cached_only():
    a = _mk_dataset([{'Cached': {'p.maps': 10}, 'Foreground': {'p.fg': 5}}] * 6)
    b = _mk_dataset([{'Perceptible Low': {'p.maps': 8}, 'Foreground': {'p.fg': 5}}] * 6)
    R = analyze(a, b, Options(prefixes=()))
    p = R['metrics']['pss']['procs']['p.maps']
    assert p['level_changed'] and p['la'] == ['Cached'] and p['lb'] == ['Perceptible Low']
    # 仅在 Cached 的进程 → cached_only
    a2 = _mk_dataset([{'Cached': {'p.c': 10}}] * 6)
    b2 = _mk_dataset([{'Cached': {'p.c': 20}}] * 6)
    R2 = analyze(a2, b2, Options(prefixes=()))
    assert R2['metrics']['pss']['procs']['p.c']['cached_only']
    # A Cached + B Perceptible → 非 cached_only
    assert not R['metrics']['pss']['procs']['p.maps']['cached_only']


def test_cached_only_partial_presence():
    # 部分轮次缺席、且仅单侧出现的 Cached 进程仍是 cached_only
    a = _mk_dataset([{'Cached': {'p.part': 10}},
                     {'Cached': {}},
                     {'Cached': {'p.part': 10}, 'Foreground': {'p.fg': 5}}])
    b = _mk_dataset([{'Foreground': {'p.fg': 5}}] * 3)
    R = analyze(a, b, Options(prefixes=()))
    procs = R['metrics']['pss']['procs']
    assert procs['p.part']['cached_only']      # A 侧缺席一轮 + 仅 A
    assert procs['p.part']['ka'] == 2 and procs['p.part']['kb'] == 0
    assert not procs['p.fg']['cached_only']    # 缺席的 None 不参与判定，但层级非 Cached


def test_growth_hint():
    a = _mk_dataset([{'Foreground': {'p.grow': 10 + i}} for i in range(8)])
    b = _mk_dataset([{'Foreground': {'p.grow': 10}} for _ in range(8)])
    R = analyze(a, b, Options(prefixes=(), growth_slope=1.0, growth_r2=0.6))
    names = {(h['ds'], h['name']) for h in R['growth']}
    assert ('A', 'p.grow') in names
    assert ('B', 'p.grow') not in names


def test_inhouse():
    wl = {'com.myapp'}
    px = ('com.transsion.', 'com.rlk.')
    assert classify('com.myapp', wl, px) == 'whitelist'
    assert classify('com.myapp:remote', wl, px) == 'whitelist'  # :子进程按包名
    assert classify('com.transsion.x', wl, px) == 'prefix'
    assert classify('com.google.x', wl, px) is None
    assert base_pkg('com.a.b:c:d') == 'com.a.b'
