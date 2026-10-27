"""端到端：真实样本包（samples/ 缺失时跳过）。期望值已与平台自身输出核对。"""
import glob
import json
import os
from datetime import datetime

import pytest

from memscope import loader
from memscope.analysis import Options, analyze, rmean
from memscope.inhouse import (load_google_prefixes, load_prefixes,
                              load_whitelist)
from memscope.model import Dataset

SAMPLES = os.path.join(os.path.dirname(__file__), '..', 'samples',
                       'static_memory_datas')


def _find(sn):
    hits = glob.glob(os.path.join(SAMPLES, f'*{sn}*'))
    return hits[0] if hits else None


PA = _find('1824615693000171')
PB = _find('18246156AB000312')
pytestmark = pytest.mark.skipif(not (PA and PB), reason='样本包不存在')


@pytest.fixture(scope='module')
def datasets():
    a = loader.load_dataset(PA, 'A')
    b = loader.load_dataset(PB, 'B')
    return a, b


@pytest.fixture(scope='module')
def result(datasets):
    a, b = datasets
    wl, ws = load_whitelist()
    px, ps = load_prefixes()
    gp, gs = load_google_prefixes()
    return analyze(a, b, Options(whitelist=wl, whitelist_src=ws,
                               prefixes=tuple(px), prefixes_src=ps,
                               google_prefixes=tuple(gp),
                               google_prefixes_src=gs))


def test_dataset_shape(datasets):
    a, b = datasets
    assert a.n == 11 and b.n == 11
    assert len(a.packages) == 334 and len(b.packages) == 336
    assert a.device['Rom Version'] == 'X1202-17.0.0.039(OP001PF001AZ)'


def test_free_ram_mean(datasets):
    a, _ = datasets
    assert rmean([r.tail['Free RAM'] for r in a.rounds], a.n) == pytest.approx(3179.01, abs=0.01)


def test_pss_native_round_mean(datasets):
    a, _ = datasets
    assert rmean([r.pss['Native']['total'] for r in a.rounds], a.n) == pytest.approx(616.41, abs=0.01)


def test_rss_levels(datasets):
    a, _ = datasets
    mean = {}
    for lv in ('Native', 'System', 'Persistent', 'Visible'):
        mean[lv] = rmean([sum(rd.rss.get(lv, {}).values()) for rd in a.rounds], a.n)
    assert mean['Native'] == pytest.approx(1590.2, abs=0.1)
    assert mean['System'] == pytest.approx(469.31, abs=0.01)
    assert mean['Persistent'] == pytest.approx(808.84, abs=0.01)
    assert mean['Visible'] == pytest.approx(441.91, abs=0.01)


def test_rss_zygote(result):
    p = result['metrics']['rss']['procs']['zygote64']
    assert p['da']['mi'] == pytest.approx(169.11, abs=0.01)


def test_thresholds(datasets):
    a, _ = datasets
    assert a.thresholds.levels['Native'] == 1603
    assert a.thresholds.free_ram == 2900
    assert a.thresholds.procs['zygote64']['threshold'] == 154


def test_maps_pss(result):
    p = result['metrics']['pss']['procs']['com.google.android.apps.maps']
    assert p['da']['mi'] == pytest.approx(124.06, abs=0.01) and p['da']['k'] == 10
    assert p['la'] == ['Cached']
    assert p['db']['mi'] == pytest.approx(98.96, abs=0.01) and p['db']['k'] == 9
    assert p['lb'] == ['Perceptible Low']
    assert p['gap'] == pytest.approx(-25.10, abs=0.01)
    assert p['level_changed']
    assert not p['cached_only']  # B 侧为 Perceptible Low


def test_cached_only_integration(result):
    procs = result['metrics']['pss']['procs']
    # youtube：A Cached 9/11 + B Cached 2/11（部分轮缺席仍是 cached_only）
    assert procs['com.google.android.youtube']['cached_only']
    # 仅单侧出现的 Cached 进程
    assert procs['com.transsnet.store']['cached_only']
    assert procs['com.transsion.healthlife']['cached_only']


def test_google_category(result):
    procs = result['metrics']['pss']['procs']
    assert procs['com.google.android.youtube']['google']
    assert not procs['com.google.android.youtube']['inhouse']
    assert procs['com.transsnet.store']['inhouse']
    assert not procs['com.transsnet.store']['google']
    assert result['google']['prefixes']


def test_atomicbrain(result):
    mp = result['metrics']['pss']
    p = mp['procs'].get('com.transsion.atomicbrain')
    assert p is not None and p['da']['k'] == 0 and p['db']['k'] == 11
    assert 'com.transsion.atomicbrain' in mp['only_b']
    assert 'com.transsion.atomicbrain' in result['pkg_diff']['b']
    # 事件：boot 阶段、caller=launcher3；callee 含 healthlife；上一次开机的 weathers 事件被丢弃
    evs = result['datasets'][1]['events']
    starts = [e for e in evs if e['kind'] == 'start' and e['proc'] == 'com.transsion.atomicbrain']
    assert starts and all(e['phase'] == 'boot' for e in starts)
    assert any(e['caller'] == 'com.transsion.launcher3' for e in starts)
    callees = {e['proc'] for e in evs if e['kind'] == 'start' and e['caller'] == 'com.transsion.atomicbrain'}
    assert 'com.transsion.healthlife' in callees
    assert not any(e['proc'] == 'com.rlk.weathers'
                   and e['t'].startswith('2026-10-22 13:52') for e in evs)
    bt = result['datasets'][1]['boot_time']
    assert bt and bt.startswith('2026-10-22 13:58:5')


def test_result_json_serializable(result):
    json.dumps(result, ensure_ascii=False)


def test_cli_end_to_end(tmp_path):
    from memscope.cli import main
    out = tmp_path / 'o'
    rc = main(['static', 'compare', PA, PB, '-o', str(out)])
    assert rc == 0
    for f in ('report.md', 'report.html', 'data.xlsx'):
        assert (out / f).exists() and (out / f).stat().st_size > 1000
    md = (out / 'report.md').read_text(encoding='utf-8')
    assert '# 静态内存对比报告' in md and 'com.google.android.apps.maps' in md
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert 'src="http' not in html and 'href="http' not in html
    import openpyxl
    wb = openpyxl.load_workbook(out / 'data.xlsx', read_only=True)
    for s in ('进程对比_PSS', '进程对比_RSS', '每轮_整机_A', '每轮_进程PSS_B',
              '标准对照', '疑似泄漏', '启动事件', '内存压力'):
        assert s in wb.sheetnames
