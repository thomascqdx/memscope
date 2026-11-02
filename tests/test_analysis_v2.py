"""Step 3：中位间隔 / gaps / valid_hours / ev_hours / level_groups / 数据 findings。"""
from datetime import datetime, timedelta

import pytest

from memscope import analysis as da
from memscope.model import Dataset, RoundData


def _mk(n=10, start=datetime(2026, 10, 26, 17, 0), iv_min=4.6, gap_after=None,
        gap_h=0.0, rss=100.0, model='3.0', chip='mtk', events=()):
    ds = Dataset(label='X', model=model, chip=chip)
    ds.detect_src = {'model': 'auto', 'chip': 'auto'}
    t = start
    for i in range(1, n + 1):
        rd = RoundData(index=i)
        rd.start = t
        rd.uptime_ms = 60000 + i * 1000
        rd.rss = {'Native': {'p': rss}}
        rd.pss = {'Native': {'total': rss, 'procs': {'p': rss}}}
        t += timedelta(minutes=iv_min)
        if gap_after == i:
            t += timedelta(hours=gap_h)
        ds.rounds.append(rd)
    ds.round_dirs = n
    ds.event_source = {'kind': 'mtk_mobilelog', 'files': 1,
                       'lines': 0, 'events': len(events)}
    ds.events = list(events)
    return ds


def _opts():
    return da.Options(**da.default_options('dynamic'))


class _E:
    def __init__(self, t, phase='test'):
        self.t = t
        self.phase = phase
        self.kind = 'lmk'
        self.proc = 'p'
        self.reason = self.comp = self.caller = None
        self.adj = '925'
        self.code = 10
        self.rss_mb = 1.0


def test_median_interval_and_gap():
    ds = _mk(n=10, gap_after=5, gap_h=2.0)
    h, iv, total, gaps, vh = da._hours(ds)
    assert iv == pytest.approx(4.6)
    assert len(gaps) == 1
    g = gaps[0]
    assert g['after_round'] == 5 and g['round'] == 6
    assert g['hours'] == pytest.approx(2.0 + 4.6 / 60)
    # valid_hours = total − (gap − median)
    assert vh == pytest.approx(total - g['hours'] + 4.6 / 60)


def test_gap_threshold_not_small():
    # 中断阈值 = max(3×中位, 30min)：20min 的空档不算中断
    ds = _mk(n=6, gap_after=3, gap_h=0.3)
    _, _, _, gaps, _ = da._hours(ds)
    assert gaps == []


def test_ev_hours_gap_without_events():
    ds = _mk(n=10, gap_after=5, gap_h=2.0)
    _, _, total, gaps, _ = da._hours(ds)
    e = da.build_events(ds, total, gaps=gaps, iv_s=4.6 * 60)
    assert e['ev_hours'] == pytest.approx(total - gaps[0]['hours'] + 4.6 / 60)


def test_ev_hours_gap_with_events_kept():
    ds = _mk(n=10, gap_after=5, gap_h=2.0)
    _, _, total, gaps, _ = da._hours(ds)
    gs = datetime.strptime(gaps[0]['start'], '%Y-%m-%d %H:%M:%S')
    ds.events = [_E(gs + timedelta(minutes=30))]
    e = da.build_events(ds, total, gaps=gaps, iv_s=4.6 * 60)
    assert e['ev_hours'] == pytest.approx(total)


def test_standards_level_groups():
    ds = _mk(n=5)
    for rd in ds.rounds:
        rd.rss = {'Persistent': {'a': 500.0},
                  'Persistent Service': {'b': 300.0}}
    from memscope.model import Thresholds
    ds.thresholds = Thresholds(levels={'Persistent': 700.0},
                             level_groups={'Persistent':
                                           ('Persistent',
                                            'Persistent Service')},
                             available=True)
    dsb = _mk(n=5)
    R = da.analyze(ds, dsb, _opts())
    lv = next(l for l in R['standards']['a']['levels']
              if l['name'] == 'Persistent')
    assert lv['members'] == ['Persistent', 'Persistent Service']
    assert lv['mean'] == pytest.approx(800.0)
    assert lv['viol'] is True


def test_standards_no_groups_unchanged():
    ds = _mk(n=5)
    from memscope.model import Thresholds
    ds.thresholds = Thresholds(levels={'Native': 50.0}, available=True)
    dsb = _mk(n=5)
    R = da.analyze(ds, dsb, _opts())
    lv = next(l for l in R['standards']['a']['levels']
              if l['name'] == 'Native')
    assert lv['members'] == ['Native']
    assert lv['mean'] == pytest.approx(100.0)


def test_device_diff_new_rows():
    a = _mk(n=5, model='3.0', chip='mtk')
    b = _mk(n=5, model='2.0', chip='unisoc')
    b.event_source = {'kind': 'unisoc_ylog', 'files': 21, 'events': 100}
    b.lmk_total = 2028
    R = da.analyze(a, b, _opts())
    dd = {r['field']: r for r in R['device_diff']}
    assert list(dd)[:2] == ['体验模型', '芯片平台']
    assert dd['体验模型'] == {'field': '体验模型', 'a': '3.0', 'b': '2.0',
                            'diff': True}
    assert dd['芯片平台']['a'] == 'MTK (—)' or 'mt' not in dd['芯片平台']['a']
    assert dd['轮次目录数']['a'] == '5'
    assert '平台有效轮次' in dd and '有效时长(小时)' in dd
    assert dd['中断区间']['a'] == '无'
    assert dd['事件来源']['b'].startswith('展锐 ylog 21 文件')
    assert dd['LMK 累计(lmkcnt)']['b'] == '2028'
    assert '采样间隔(分钟，中位数)' in dd
    assert '平均采样间隔(分钟)' not in dd


def test_findings_data_category():
    a = _mk(n=5, model='3.0', chip='mtk', gap_after=3, gap_h=2.0)
    b = _mk(n=5, model='2.0', chip='unisoc')
    b.round_dirs = 100  # 5/100 < 0.8 → 低覆盖
    b.failed_rounds = [{'index': i, 'reason': 'r'} for i in range(6, 101)]
    b.platform['valid_count'] = 99
    b.event_source = {'kind': 'bugreport', 'files': 1, 'events': 3}
    b.events = [_E(datetime(2026, 10, 26, 17, 30))]
    a.event_source = {'kind': None, 'files': 0, 'events': 0}
    R = da.analyze(a, b, _opts())
    f = R['findings']
    t = {x['title'] for x in f if x['cat'] == '数据'}
    assert any('两侧体验模型不同（A 3.0 / B 2.0）' in s for s in t)
    assert any('两侧芯片平台不同' in s for s in t)
    assert any('B 侧有效轮仅 5/100' in s for s in t)
    assert any('A 侧数据中断 1 段' in s for s in t)
    assert any('B 侧工具有效轮 5 与平台有效测试次数 99 不一致' in s for s in t)
    assert any('A 侧无事件日志' in s for s in t)
    assert any('B 侧事件仅来自 bugreport' in s for s in t)
    assert R['meta']['cross_model'] is True
    assert R['meta']['cross_chip'] is True


def test_findings_data_not_fired():
    a = _mk(n=10)
    b = _mk(n=10)
    R = da.analyze(a, b, _opts())
    assert not [x for x in R['findings'] if x['cat'] == '数据']
    assert R['meta']['cross_model'] is False
    assert R['meta']['cross_chip'] is False


def test_dataset_json_new_keys():
    a = _mk(n=5, model='2.0', chip='unisoc')
    b = _mk(n=5)
    a.lmk_total = 9
    a.clock_offset_s = 3400.0
    R = da.analyze(a, b, _opts())
    d = R['datasets'][0]
    for k in ('model', 'chip', 'detect_src', 'round_dirs', 'valid_hours',
              'ev_hours', 'gaps', 'lmk_total', 'clock_offset_s',
              'event_source'):
        assert k in d
    assert d['model'] == '2.0' and d['chip'] == 'unisoc'
    assert d['lmk_total'] == 9 and d['round_dirs'] == 5
    assert 'std_version' in d['platform'] and 'std_tier' in d['platform']
    assert 'valid_count' in d['platform']
