"""统一管线测试：静态/动态共用 schema、按类默认参数、单一渲染器。"""
import io
import json

import pytest

from memscope import analysis as da
from memscope.loader import _detect_reboots, _load_platform
from memscope.model import Dataset, Event, RoundData
from memscope.render import excel, markdown
from tests.test_dynamic import _mk_ds, _mk_ds_levels, _mk_pkg


def _opts(kind):
    return da.Options(**da.default_options(kind))


def _pair(n, seed_b=200.0):
    """两侧各 n 轮：A Native procX=100 恒定，B 200 恒定（显著差异）。"""
    a = _mk_ds([100.0] * n)
    b = _mk_ds([seed_b] * n)
    return a, b


def _static_result(n=11):
    a, b = _pair(n)
    return da.analyze(a, b, _opts('static'))


def _dynamic_result(n=40):
    a, b = _pair(n)
    return da.analyze(a, b, _opts('dynamic'))


# ---------- 1. schema 对齐 ----------
def test_schema_parity_static_dynamic():
    rs = _static_result()
    rd = _dynamic_result()
    assert set(rs) == set(rd)
    for key in ('meta', 'device_diff', 'pkg_diff', 'metrics', 'system',
                'pressure', 'events', 'standards', 'inhouse', 'google',
                'leaks', 'findings'):
        assert key in rs
    assert set(rs['meta']) == set(rd['meta'])
    assert set(rs['datasets'][0]) == set(rd['datasets'][0])
    for metric in ('rss', 'pss'):
        assert set(rs['metrics'][metric]) == set(rd['metrics'][metric])
        pa = next(iter(rs['metrics'][metric]['procs'].values()))
        pb = next(iter(rd['metrics'][metric]['procs'].values()))
        assert set(pa) == set(pb)
        la = rs['metrics'][metric]['level_rows'][0]
        lb = rd['metrics'][metric]['level_rows'][0]
        assert set(la) == set(lb)
    assert set(rs['system'][0]) == set(rd['system'][0])
    if rs['standards']['a'] and rd['standards']['a']:
        assert set(rs['standards']['a']) == set(rd['standards']['a'])
    if rs['findings'] and rd['findings']:
        assert set(rs['findings'][0]) == set(rd['findings'][0])
    json.dumps(rs, ensure_ascii=False)
    json.dumps(rd, ensure_ascii=False)


# ---------- 2. pra/prb 与 axis ----------
def test_pra_prb_and_axis():
    rs = _static_result(11)
    assert rs['meta']['axis'] == 'round'
    p = rs['metrics']['rss']['procs']['procX']
    assert p['pra'] is not None and p['prb'] is not None
    assert p['pra'][0] == 'Native'
    rd = _dynamic_result(40)
    assert rd['meta']['axis'] == 'hour'
    p = rd['metrics']['rss']['procs']['procX']
    assert p['pra'] is None and p['prb'] is None


# ---------- 3. 事件内嵌上限 ----------
def test_events_inline_cap():
    a, b = _pair(11)
    from datetime import datetime
    t0 = datetime(2026, 1, 1)
    a.events = [Event(t=t0, kind='start', proc='p.x', phase='test')
                for _ in range(6001)]
    R = da.analyze(a, b, _opts('static'))
    assert R['datasets'][0]['events'] is None
    assert R['datasets'][0]['events_total'] == 6001
    a.events = a.events[:5000]
    R2 = da.analyze(a, b, _opts('static'))
    assert isinstance(R2['datasets'][0]['events'], list)
    assert R2['datasets'][0]['events_total'] == 5000


# ---------- 4. primary_metric 驱动 findings ----------
def test_primary_metric_in_findings():
    rs = _static_result()
    lv = [f for f in rs['findings'] if f['cat'] == '层级']
    assert any('PSS' in f['title'] for f in lv)
    pc = [f for f in rs['findings'] if f['cat'] == '进程']
    assert all('metric=pss' in f['link'] or f['link'].startswith('#tab=')
               for f in pc)
    rd = _dynamic_result()
    lv = [f for f in rd['findings'] if f['cat'] == '层级']
    assert any('RSS' in f['title'] for f in lv)


# ---------- 5. 新增/消失/归类 findings ----------
def test_new_gone_and_category_findings():
    n = 11
    # B 侧新增 procNew 轮均 50 ≥ gap_abs；自研进程 100→300 → 归类上升
    a = _mk_ds_levels({'Native': {'procX': [100.0] * n},
                       'Foreground': {'com.transsion.x': [100.0] * n}}, n=n)
    b = _mk_ds_levels({'Native': {'procX': [100.0] * n,
                                  'procNew': [50.0] * n},
                       'Foreground': {'com.transsion.x': [300.0] * n}}, n=n)
    opts = _opts('static')
    opts.prefixes = ('com.transsion.',)
    R = da.analyze(a, b, opts)
    proc_f = [f for f in R['findings'] if f['cat'] == '进程']
    assert any('新增进程' in f['title'] and f['sev'] == 'mid'
               and 'procNew' in f['detail'] for f in proc_f)
    cat_f = [f for f in R['findings'] if f['cat'] == '归类']
    assert any('自研' in f['title'] and f['sev'] == 'mid' for f in cat_f)


# ---------- 6. loader：platform 双结果 + 静态重启 ----------
def test_load_platform_both_results():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['内存标准静态测试结果：', 'Fail'])
    ws.append(['内存标准动态测试结果：', 'NA'])
    ws.append(['检测工具版本：', 'v1.2.3'])
    bio = io.BytesIO()
    wb.save(bio)

    class _Src:
        def read(self, name):
            return bio.getvalue()

    ds = Dataset(label='x')
    _load_platform(_Src(), {'Exception_Summary_Report.xlsx'}, ds)
    assert ds.platform['static_result'] == 'Fail'
    assert ds.platform['dynamic_result'] == 'NA'
    assert ds.platform['tool_version'] == 'v1.2.3'


def test_reboots_detected_static(tmp_path):
    root = _mk_pkg(tmp_path / 'pkgs', 'R1', 'SN', n=4)
    (root / 'device_info.txt').write_text(
        (root / 'device_info.txt').read_text(encoding='utf-8')
        .replace('Test Type: dynamic', 'Test Type: static'), encoding='utf-8')
    ds = Dataset(label='x')
    for i, up in enumerate((60000, 120000, 5000, 65000)):
        ds.rounds.append(RoundData(index=i + 1, uptime_ms=up))
    _detect_reboots(ds)
    assert ds.reboots == [{'after_round': 2, 'round': 3}]


# ---------- 7. Markdown 章节一致 ----------
def test_markdown_headings_identical():
    hs = [l for l in markdown.render(_static_result()).splitlines()
          if l.startswith('## ')]
    hd = [l for l in markdown.render(_dynamic_result()).splitlines()
          if l.startswith('## ')]
    assert [l.split('.', 1)[0] for l in hs] == [l.split('.', 1)[0] for l in hd]
    nums = [l.split()[1].rstrip('.') for l in hs]
    assert nums == [str(i) for i in range(1, 13)]
    md = markdown.render(_static_result())
    assert md.startswith('# 静态内存对比报告')
    assert '# 动态内存对比报告' in markdown.render(_dynamic_result())


# ---------- 8. Excel sheet 一致 ----------
def test_excel_sheets_identical(tmp_path):
    excel.render(_static_result(), tmp_path / 's.xlsx')
    excel.render(_dynamic_result(), tmp_path / 'd.xlsx')
    import openpyxl
    s = openpyxl.load_workbook(tmp_path / 's.xlsx', read_only=True).sheetnames
    d = openpyxl.load_workbook(tmp_path / 'd.xlsx', read_only=True).sheetnames
    assert s == d
    assert '内存压力' in s and '每轮_进程RSS_A' in s


# ---------- 9. 单一模板渲染 ----------
def test_html_single_template(tmp_path):
    from memscope.render import html
    for R, cn in ((_static_result(), '静态'), (_dynamic_result(), '动态')):
        R['report_md'] = ''
        out = html.render(R, tmp_path / f'r_{cn}.html')
        txt = out.read_text(encoding='utf-8')
        assert '__TITLE__' not in txt and '__RESULT_JSON__' not in txt
        title = txt.split('<title>')[1].split('</title>')[0]
        assert R['labels'][0] in title and R['labels'][1] in title
        assert cn in title
