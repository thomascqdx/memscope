"""统计与 inhouse 判定单元测试。"""
from datetime import datetime, timedelta

import pytest

from memscope.analysis import (Options, analyze, cnt, gap_info, is_significant,
                               rmean, vmean)
from memscope.inhouse import (base_pkg, classify, is_google,
                              load_google_prefixes)
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
    assert procs['p.part']['da']['k'] == 2 and procs['p.part']['db']['k'] == 0
    assert not procs['p.fg']['cached_only']    # 缺席的 None 不参与判定，但层级非 Cached


def test_leak_hint_static():
    # 静态默认：常驻（全出现）+ head/tail delta ≥ 10 MB 且 R² ≥ 0.6 → 疑似泄漏
    a = _mk_dataset([{'Foreground': {'p.grow': 10 + i * 5}} for i in range(8)])
    b = _mk_dataset([{'Foreground': {'p.grow': 10}} for _ in range(8)])
    R = analyze(a, b, Options(prefixes=()))
    p = R['metrics']['pss']['procs']['p.grow']
    assert p['leak_a'] and not p['leak_b']
    names = {(h['ds'], h['name']) for h in R['leaks']}
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


def test_is_google():
    px = ('com.google.', 'com.android.vending', 'com.android.chrome')
    assert is_google('com.google.android.gms', px)
    assert is_google('com.android.vending:background', px)
    assert is_google('com.android.chrome', px)
    assert not is_google('com.transsion.x', px)


def test_google_vs_inhouse_priority():
    rounds = [{'Foreground': {'com.google.android.gms': 10,
                              'com.transsion.x': 5}}] * 6
    a, b = _mk_dataset(rounds), _mk_dataset(rounds)
    R = analyze(a, b, Options(prefixes=('com.transsion.',),
                            google_prefixes=('com.google.', 'com.transsion.')))
    procs = R['metrics']['pss']['procs']
    # 同时命中自研前缀与 google 前缀 → 自研优先
    assert procs['com.transsion.x']['inhouse']
    assert not procs['com.transsion.x']['google']
    # 仅命中 google 前缀
    assert procs['com.google.android.gms']['google']
    assert not procs['com.google.android.gms']['inhouse']
    assert R['google']['prefixes'] == ['com.google.', 'com.transsion.']


def test_load_google_prefixes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    px, src = load_google_prefixes()          # cwd 无 config → 内置
    assert 'com.google.' in px and src == '(内置)'
    cfg = tmp_path / 'config'
    cfg.mkdir()
    (cfg / 'google_prefixes.txt').write_text('com.foo.\n', encoding='utf-8')
    px2, src2 = load_google_prefixes()        # 本地 config 覆盖
    assert px2 == ['com.foo.'] and 'google_prefixes.txt' in src2
    with pytest.raises(FileNotFoundError):
        load_google_prefixes(tmp_path / 'nope.txt')
    f = tmp_path / 'g.txt'
    f.write_text('com.bar.\n', encoding='utf-8')
    px3, _ = load_google_prefixes(f)          # 显式路径
    assert px3 == ['com.bar.']
