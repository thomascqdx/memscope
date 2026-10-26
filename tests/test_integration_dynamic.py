"""动态包端到端：真实样本（A=rar, B=zip）。样本缺失或未设 MEMSCOPE_SLOW=1 时跳过。"""
import glob
import json
import os

import pytest

from memscope import loader
from memscope.dynamic_analysis import DynOptions, analyze, dist
from memscope.inhouse import load_prefixes, load_whitelist

SAMPLES = os.path.join(os.path.dirname(__file__), '..', 'samples',
                       'dynamic_memory_datas')


def _find(sn):
    hits = glob.glob(os.path.join(SAMPLES, f'*{sn}*'))
    return hits[0] if hits else None


PA = _find('1824615693000290')   # rar
PB = _find('18246156AB000432')   # zip
pytestmark = pytest.mark.skipif(
    not (PA and PB) or os.environ.get('MEMSCOPE_SLOW') != '1',
    reason='样本包不存在或未设 MEMSCOPE_SLOW=1（加载两包需数分钟）')


@pytest.fixture(scope='module')
def datasets():
    a = loader.load_dataset(PA, 'A', kind='dynamic')
    b = loader.load_dataset(PB, 'B', kind='dynamic')
    return a, b


@pytest.fixture(scope='module')
def result(datasets):
    a, b = datasets
    wl, ws = load_whitelist()
    px, ps = load_prefixes()
    return analyze(a, b, DynOptions(whitelist=wl, whitelist_src=ws,
                                  prefixes=tuple(px), prefixes_src=ps))


def test_dataset_shape(datasets):
    a, b = datasets
    assert a.n == 1275 and b.n == 1279
    assert any(d['index'] == 39 for d in a.failed_rounds)
    assert a.device['Test Type'] == 'dynamic'
    assert a.platform.get('dynamic_result') == 'Fail'


def _level_mean(ds, lv):
    vals = [sum(rd.rss.get(lv, {}).values()) for rd in ds.rounds]
    return sum(vals) / len(vals)


def test_platform_aligned_means(datasets):
    a, b = datasets
    assert _level_mean(a, 'Native') == pytest.approx(1766.76, abs=0.01)
    assert _level_mean(b, 'Native') == pytest.approx(1745.28, abs=0.02)
    assert _level_mean(a, 'System') == pytest.approx(501.51, abs=0.01)
    assert _level_mean(b, 'System') == pytest.approx(504.66, abs=0.01)
    assert _level_mean(a, 'Persistent') == pytest.approx(889.98, abs=0.01)
    assert _level_mean(b, 'Persistent') == pytest.approx(713.24, abs=0.01)
    # 注意：all_memory_data.xls 的 Visible（A 1158.2 / B 1355.15）与平台自身
    # level_memory 汇总文件（B 1455.77）不一致；memscope 口径（procStatus
    # OomScore 映射）与后者完全一致，此处按后者断言。
    assert _level_mean(a, 'Visible') == pytest.approx(1277.27, abs=0.01)
    assert _level_mean(b, 'Visible') == pytest.approx(1455.77, abs=0.01)


def test_free_ram_mean(datasets):
    a, b = datasets
    fa = sum(r.tail['Free RAM'] for r in a.rounds) / a.n
    fb = sum(r.tail['Free RAM'] for r in b.rounds) / b.n
    assert fa == pytest.approx(1536.95, abs=0.01)
    assert fb == pytest.approx(1433.73, abs=0.01)


def test_native_percentiles(result):
    """插值法=inclusive 线性：与平台 level_memory/Exception_Summary_Level 完全一致；
    与 all_memory_data.xls 有 ~0.01-0.02 差（平台内部口径差异），按后者断言放宽到 0.02。"""
    mrss = result['metrics']['rss']
    r = next(x for x in mrss['level_rows'] if x['level'] == 'Native')
    assert r['da']['p90'] == pytest.approx(1846.098, abs=0.02)
    assert r['da']['p95'] == pytest.approx(1871.742, abs=0.02)
    assert r['db']['p90'] == pytest.approx(1810.87, abs=0.02)
    assert r['db']['p95'] == pytest.approx(1835.451, abs=0.03)
    assert r['da']['mean'] == pytest.approx(1766.76, abs=0.01)
    assert r['db']['mean'] == pytest.approx(1745.28, abs=0.02)
    assert r['da']['max'] == pytest.approx(1997.28, abs=0.1)
    assert r['db']['max'] == pytest.approx(2395.30, abs=0.1)


def test_thresholds(datasets):
    a, _ = datasets
    assert a.thresholds.levels['Native'] == 1762
    assert a.thresholds.free_ram == 1450
    assert 'zygote64' in a.thresholds.procs


def test_standards_verdict(result):
    sa, sb = result['standards']['a'], result['standards']['b']
    assert sa['verdict'] == 'Fail' and 'Native' in sa['fail_items']
    assert sb['verdict'] == 'Fail' and 'Free RAM' in sb['fail_items']


def test_lmk_events(datasets):
    _, b = datasets
    lmk = [e for e in b.events if e.kind == 'lmk' and e.phase == 'test']
    assert len(lmk) > 0
    named = sum(1 for e in lmk if e.proc and not e.proc.startswith('pid:'))
    assert named / len(lmk) >= 0.9


def test_result_json_serializable(result):
    result2 = dict(result)
    result2['report_md'] = ''
    json.dumps(result2, ensure_ascii=False)


def test_cli_end_to_end(datasets, tmp_path):
    from memscope.cli import main
    out = tmp_path / 'o'
    rc = main(['dynamic', 'compare', PA, PB, '-o', str(out)])
    assert rc == 0
    for f in ('report.md', 'report.html', 'data.xlsx'):
        assert (out / f).exists() and (out / f).stat().st_size > 1000
    md = (out / 'report.md').read_text(encoding='utf-8')
    assert '# 动态内存对比报告' in md
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert 'src="http' not in html and 'href="http' not in html
    import openpyxl
    wb = openpyxl.load_workbook(out / 'data.xlsx', read_only=True)
    for s in ('标准对照', '层级对比_RSS', '进程对比_RSS', '内存压力',
              'LMK查杀', '每轮_整机_A', '每轮_进程RSS_B'):
        assert s in wb.sheetnames
