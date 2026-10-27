"""版本：单一来源 __version__、CLI -V、包元数据、bump 脚本、报告内版本。"""
import importlib.util
from pathlib import Path

import pytest

from memscope import __version__


def test_cli_version(capsys):
    from memscope.cli import main
    with pytest.raises(SystemExit) as e:
        main(['--version'])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f'memscope {__version__}'


def test_package_metadata_version():
    import importlib.metadata
    assert importlib.metadata.version('memscope') == __version__


_BUMP = Path(__file__).resolve().parent.parent / 'scripts' / 'bump_version.py'
spec = importlib.util.spec_from_file_location('bump_version', _BUMP)
bv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bv)


def test_bump_parts():
    assert bv.bump('0.3.0', 'major') == '1.0.0'
    assert bv.bump('0.3.0', 'minor') == '0.4.0'
    assert bv.bump('0.3.0', 'patch') == '0.3.1'
    assert bv.bump('0.3.0', '0.5.0') == '0.5.0'


def test_bump_invalid():
    for part in ('x.y', '0.3.0'):
        with pytest.raises(ValueError):
            bv.bump('0.3.0', part)


def test_meta_tool_version():
    from memscope import analysis as da
    assert da.Options(**da.default_options('static'))
    # 用任意已分析的合成结果验证 meta.tool
    from tests.test_dynamic import _mk_ds
    R = da.analyze(_mk_ds([1.0] * 3), _mk_ds([1.0] * 3),
                   da.Options(**da.default_options('static')))
    assert R['meta']['tool'] == f'memscope {__version__}'
