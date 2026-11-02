"""真实 2.0 包集成测试（慢；需 samples/dynamic_memory_datas-model2.0
且 MEMSCOPE_SLOW=1）。模块级 fixture 每包只加载一次。"""
import glob
import os

import pytest

from memscope import analysis as da
from memscope import loader

SAMPLES = os.path.join(os.path.dirname(__file__), '..', 'samples',
                       'dynamic_memory_datas-model2.0')

pytestmark = pytest.mark.skipif(
    not (os.environ.get('MEMSCOPE_SLOW') and os.path.isdir(SAMPLES)),
    reason='needs samples/dynamic_memory_datas-model2.0 + MEMSCOPE_SLOW=1')


def _find(pat):
    hits = glob.glob(os.path.join(SAMPLES, f'*{pat}*'))
    if not hits:
        pytest.skip(f'样本缺失: {pat}')
    return hits[0]


@pytest.fixture(scope='module')
def ds_rar(tmp_path_factory):
    return loader.load_dataset(
        _find('1376'), 'A', kind='dynamic',
        tmpdir=str(tmp_path_factory.mktemp('rar')))


@pytest.fixture(scope='module')
def ds_zip(tmp_path_factory):
    return loader.load_dataset(
        _find('1363'), 'B', kind='dynamic',
        tmpdir=str(tmp_path_factory.mktemp('zip')))


def _oracle(ds):
    groups = {'Native': ('Native',), 'System': ('System',),
              'Persistent': ('Persistent', 'Persistent Service'),
              'Visible': ('Visible',)}
    out = {}
    for lv, keys in groups.items():
        vals = [sum(sum(rd.rss[k].values()) for k in keys if k in rd.rss)
                for rd in ds.rounds if any(k in rd.rss for k in keys)]
        out[lv] = sum(vals) / len(vals) if vals else None
    return out


def test_rar_dataset(ds_rar):
    ds = ds_rar
    assert ds.model == '2.0' and ds.chip == 'unisoc'
    assert ds.n == 1393 and ds.round_dirs == 1398
    assert [d['index'] for d in ds.failed_rounds] == [621, 1010, 1093,
                                                    1255, 1393]
    th = ds.thresholds
    assert th.levels == {'Native': 930, 'System': 280,
                         'Persistent': 1175, 'Visible': 2014}
    assert th.free_ram == 1171
    assert th.level_groups == {'Persistent': ('Persistent',
                                             'Persistent Service')}
    assert ds.platform['dynamic_result'] == 'Fail'
    assert ds.platform['tool_version'] == 'v2.10'
    assert ds.platform['std_version'] == 'v3.1'
    assert ds.platform['valid_count'] == 1394
    assert ds.lmk_total == 3274
    assert ds.event_source['kind'] == 'unisoc_ylog'
    assert ds.event_source['files'] == 26
    assert ds.event_source['events'] > 60000
    assert 3300 < ds.clock_offset_s < 3500


def test_rar_oracle(ds_rar):
    o = _oracle(ds_rar)
    assert o['Native'] == pytest.approx(1220.50, abs=0.05)
    assert o['System'] == pytest.approx(547.73, abs=0.05)
    assert o['Persistent'] == pytest.approx(903.32, abs=0.05)
    assert o['Visible'] == pytest.approx(1909.14, abs=0.05)


def test_zip_dataset(ds_zip):
    ds = ds_zip
    assert ds.model == '2.0' and ds.chip == 'unisoc'
    assert ds.n == 880 and ds.round_dirs == 1542
    assert [d['index'] for d in ds.failed_rounds] == list(range(772, 1434))
    assert ds.platform['valid_count'] == 880


def test_zip_oracle(ds_zip):
    o = _oracle(ds_zip)
    assert o['Native'] == pytest.approx(1238.83, abs=0.05)
    assert o['System'] == pytest.approx(547.84, abs=0.05)
    assert o['Persistent'] == pytest.approx(906.73, abs=0.05)
    assert o['Visible'] == pytest.approx(1690.41, abs=0.05)


def test_analyze_pair(ds_zip, ds_rar):
    R = da.analyze(ds_zip, ds_rar,
                   da.Options(**da.default_options('dynamic')))
    gaps = R['datasets'][0]['gaps']
    assert len(gaps) == 1 and gaps[0]['hours'] > 30
    for side in 'ab':
        lv = next(l for l in R['standards'][side]['levels']
                  if l['name'] == 'Persistent')
        assert lv['members'] == ['Persistent', 'Persistent Service']
        assert lv['viol'] is False
    assert any(f['cat'] == '数据'
               and f['title'].startswith('A 侧有效轮仅 880/1542')
               for f in R['findings'])
