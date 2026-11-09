"""测试窗口（test_window / _apply_window / build_events 窗口口径）单元测试。"""
from datetime import datetime, timedelta

import pytest

from memscope.analysis import build_events
from memscope.eventsrc import _apply_window
from memscope.model import (Dataset, Event, RoundData, round_interval_s)
from memscope.model import test_window as _window

T0 = datetime(2026, 1, 1, 10, 0, 0)


def _rounds(starts_min, ends_min=None):
    ends_min = ends_min or [None] * len(starts_min)
    return [RoundData(
        index=i + 1,
        start=T0 + timedelta(minutes=s) if s is not None else None,
        end=T0 + timedelta(minutes=e) if e is not None else None)
        for i, (s, e) in enumerate(zip(starts_min, ends_min))]


def _mk_ds(rounds, events):
    ds = Dataset(label='x')
    ds.rounds = rounds
    ds.events = events
    ds.event_source = {'outside': 0, 'window': {'start': None, 'end': None}}
    return ds


def _ev(t, kind='lmk', phase='test', **kw):
    return Event(t=t, kind=kind, phase=phase, **kw)


def test_round_interval_s():
    assert round_interval_s(_rounds([0, 10, 20])) == 600
    assert round_interval_s(_rounds([0, 20, 30])) == 900      # 两个差值取均值
    assert round_interval_s(_rounds([0, 10, 15, 45])) == 600  # 中位数非均值
    assert round_interval_s(_rounds([0])) is None
    assert round_interval_s([]) is None
    # 相邻对两侧都有 start 才计入
    assert round_interval_s(_rounds([0, None, 20, 30])) == 600


def test_window_basic():
    # ends 全缺 → 末轮 start + 间隔中位数
    s, e = _window(_rounds([0, 10, 20]))
    assert s == T0 and e == T0 + timedelta(minutes=30)
    # 末轮 end < 推断 end → 取推断 end
    s, e = _window(_rounds([0, 10, 20], [None, None, 25]))
    assert e == T0 + timedelta(minutes=30)
    # 末轮 end > 推断 end → 取末轮 end
    s, e = _window(_rounds([0, 10, 20], [None, None, 40]))
    assert e == T0 + timedelta(minutes=40)


def test_window_edge():
    assert _window([]) == (None, None)
    # 单轮无 end → end = 其 start
    s, e = _window(_rounds([0]))
    assert s == T0 and e == T0
    # 单轮有 end → 取该 end
    s, e = _window(_rounds([0], [15]))
    assert e == T0 + timedelta(minutes=15)
    # rounds[0].start 缺失 → 无窗口
    s, e = _window(_rounds([None, 10, 20]))
    assert s is None and e is None


def test_apply_window():
    ds = _mk_ds(_rounds([0, 10, 20]), [     # 窗口 end = T0+30min
        _ev(T0 - timedelta(minutes=5), 'start', 'boot'),   # boot 事件不丢弃
        _ev(T0 + timedelta(minutes=15), 'lmk'),            # 窗口内
        _ev(T0 + timedelta(minutes=30), 'lmk'),            # t == end，含入
        _ev(T0 + timedelta(minutes=31), 'lmk'),            # t > end，丢弃
    ])
    _apply_window(ds)
    assert [e.t for e in ds.events] == [
        T0 - timedelta(minutes=5), T0 + timedelta(minutes=15),
        T0 + timedelta(minutes=30)]
    assert ds.events[0].phase == 'boot'
    assert ds.event_source['outside'] == 1
    assert ds.event_source['window'] == {
        'start': '2026-01-01 10:00:00', 'end': '2026-01-01 10:30:00'}


def test_apply_window_no_end():
    ds = _mk_ds([RoundData(index=1)], [_ev(T0 + timedelta(hours=5), 'lmk')])
    _apply_window(ds)
    assert len(ds.events) == 1
    assert ds.event_source['outside'] == 0
    assert ds.event_source['window'] == {'start': None, 'end': None}


def test_build_events_window_denominator():
    # 轮次 0h/1h/2h，ends 缺 → 窗口 end = +3h，win_hours = 3
    ds = _mk_ds(_rounds([0, 60, 120]), [
        _ev(T0 + timedelta(minutes=30), 'lmk', adj='900', code=1, proc='a'),
        _ev(T0 + timedelta(minutes=70), 'lmk', adj='900', code=1, proc='a'),
        _ev(T0 + timedelta(minutes=170), 'lmk', adj='900', code=1, proc='b'),
    ])
    _apply_window(ds)
    r = build_events(ds, 2.0)       # hours_total=末轮 start−首轮 start=2h
    lmk = r['lmk']
    assert lmk['total'] == 3                 # 末轮 start 之后、窗口内事件计入
    assert lmk['hourly'] == [1, 1, 1]
    assert sum(lmk['hourly']) == lmk['total']
    assert r['ev_hours'] == pytest.approx(3.0)
    assert lmk['per_hour'] == pytest.approx(1.0)
