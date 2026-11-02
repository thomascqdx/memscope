"""Step 4 渲染器：跨模型横幅、层级合并名、事件来源行、无事件日志。"""
from datetime import datetime, timedelta

import pytest

from memscope import analysis as da
from memscope.model import Dataset, RoundData, Thresholds
from memscope.render import excel, html, markdown


def _mk(n=5, model='3.0', chip='mtk', es=None, events_total=0, lmk=None):
    ds = Dataset(label='X', model=model, chip=chip)
    ds.detect_src = {'model': 'auto', 'chip': 'auto'}
    t = datetime(2026, 10, 26, 17, 0)
    for i in range(1, n + 1):
        rd = RoundData(index=i)
        rd.start = t
        rd.uptime_ms = 60000 + i * 1000
        rd.rss = {'Native': {'p': 100.0},
                  'Persistent': {'a': 500.0},
                  'Persistent Service': {'b': 300.0}}
        rd.pss = {'Native': {'total': 100.0, 'procs': {'p': 100.0}}}
        t += timedelta(minutes=5)
        ds.rounds.append(rd)
    ds.round_dirs = n
    ds.lmk_total = lmk
    ds.event_source = es or {'kind': None, 'files': 0, 'lines': 0,
                             'events': 0, 'first': None, 'last': None}
    return ds


def _R(a, b):
    return da.analyze(a, b, da.Options(**da.default_options('dynamic')))


def _ylog_es():
    return {'kind': 'unisoc_ylog', 'files': 21, 'lines': 43676,
            'events': 43657, 'first': '2026-10-26 17:22:33',
            'last': '2026-10-30 17:28:41'}


def test_md_cross_banner():
    R = _R(_mk(model='3.0', chip='mtk'), _mk(model='2.0', chip='unisoc'))
    md = markdown.render(R)
    assert '注意：两侧体验模型不同（A 3.0 / B 2.0）' in md
    assert '注意：两侧芯片平台不同（A MTK / B 展锐）' in md
    assert '体验模型：A 3.0 / B 2.0' in md
    assert '芯片平台：A MTK' in md or 'MTK (' in md


def test_md_no_banner_same_model():
    R = _R(_mk(), _mk())
    md = markdown.render(R)
    assert '注意：' not in md


def test_md_events_source_line():
    a = _mk()
    b = _mk(es=_ylog_es(), events_total=43657, lmk=2028)
    b.event_source = _ylog_es()
    R = _R(a, b)
    md = markdown.render(R)
    assert '事件来源：展锐 ylog（21 文件，过滤后 43676 行，事件 43657 条' in md
    assert 'lmkcnt 累计 2028 次 / killinfo 解析' in md
    assert '事件来源：无事件日志' in md


def test_md_std_members_name():
    a = _mk()
    a.thresholds = Thresholds(
        levels={'Persistent': 700.0},
        level_groups={'Persistent': ('Persistent', 'Persistent Service')},
        available=True)
    R = _R(a, _mk())
    md = markdown.render(R)
    assert 'Persistent（含 Persistent Service）' in md


def test_xlsx_cross_banner_and_name(tmp_path):
    a = _mk(model='3.0', chip='mtk')
    a.thresholds = Thresholds(
        levels={'Persistent': 700.0},
        level_groups={'Persistent': ('Persistent', 'Persistent Service')},
        available=True)
    b = _mk(model='2.0', chip='unisoc', es=_ylog_es(), lmk=5)
    R = _R(a, b)
    out = excel.render(R, tmp_path / 'd.xlsx')
    import openpyxl
    wb = openpyxl.load_workbook(out)
    info = [tuple(r) for r in wb['测试信息'].iter_rows(values_only=True)]
    assert any(r[0] and '两侧体验模型不同' in str(r[0]) for r in info)
    std = [tuple(r) for r in wb['标准对照'].iter_rows(values_only=True)]
    assert any('Persistent（含 Persistent Service）' == r[1] for r in std)
    lmk = [tuple(r) for r in wb['LMK查杀'].iter_rows(values_only=True)]
    assert any(r[1] and '展锐 ylog' in str(r[1]) for r in lmk)


def test_html_embeds_banner_data(tmp_path):
    R = _R(_mk(model='3.0', chip='mtk'), _mk(model='2.0', chip='unisoc'))
    out = html.render(R, tmp_path / 'r.html')
    t = out.read_text(encoding='utf-8')
    assert 'crossBanner' in t and 'cross_model' in t
    assert '"cross_model":true' in t
