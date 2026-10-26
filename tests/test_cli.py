"""CLI 结构：static/dynamic 子命令与 Test Type 校验。"""
import pytest

from memscope.cli import main
from memscope.loader import check_test_type
from memscope.model import Dataset


def test_check_test_type():
    ds = Dataset(label='A')
    ds.device['Test Type'] = 'static'
    assert check_test_type(ds) is None

    ds2 = Dataset(label='A')
    ds2.device['Test Type'] = ' Static '
    assert check_test_type(ds2) is None   # 大小写/空白不敏感

    ds3 = Dataset(label='B')
    ds3.device['Test Type'] = 'dynamic'
    err = check_test_type(ds3)
    assert err and 'B' in err and 'dynamic' in err

    ds4 = Dataset(label='A')               # 缺失 → None + warning
    assert check_test_type(ds4) is None
    assert any('Test Type' in w for w in ds4.warnings)


def test_dynamic_compare_bad_input(capsys):
    rc = main(['dynamic', 'compare', 'x', 'y'])
    assert rc == 2
    assert '无法识别的输入' in capsys.readouterr().err


def test_old_top_level_compare_removed():
    with pytest.raises(SystemExit) as e:
        main(['compare', 'x', 'y'])
    assert e.value.code == 2
