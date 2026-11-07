"""load_dataset 临时目录清理重试（WinError 145 修复）。"""
import shutil
from pathlib import Path

import pytest

from memscope import loader
from memscope.loader import load_dataset


def _stub_load(monkeypatch, exc=None):
    if exc is None:
        monkeypatch.setattr(loader, '_load', lambda *a, **k: None)
    else:
        def _boom(*a, **k):
            raise exc
        monkeypatch.setattr(loader, '_load', _boom)


def _always_fail(*a, **k):
    raise OSError(145, 'not empty')


def test_rmtree_retry_succeeds(tmp_path, monkeypatch):
    _stub_load(monkeypatch)
    monkeypatch.setattr(loader.time, 'sleep', lambda s: None)
    real = shutil.rmtree
    calls = {'n': 0, 'path': None}
    def flaky(path, *a, **k):
        calls['n'] += 1
        calls['path'] = path
        if calls['n'] <= 2:
            raise OSError(145, 'not empty')
        return real(path, *a, **k)
    monkeypatch.setattr(loader.shutil, 'rmtree', flaky)
    src = tmp_path / 'pkg'
    src.mkdir()
    ds = load_dataset(src, 'A', tmpdir=str(tmp_path))
    assert calls['n'] == 3
    assert not Path(calls['path']).exists()
    assert not any('临时目录未能完全删除' in w for w in ds.warnings)


def test_rmtree_retry_gives_up(tmp_path, monkeypatch):
    _stub_load(monkeypatch)
    monkeypatch.setattr(loader.time, 'sleep', lambda s: None)
    monkeypatch.setattr(loader.shutil, 'rmtree', _always_fail)
    src = tmp_path / 'pkg'
    src.mkdir()
    ds = load_dataset(src, 'A', tmpdir=str(tmp_path))
    assert any('临时目录未能完全删除' in w for w in ds.warnings)


def test_load_error_survives_cleanup_failure(tmp_path, monkeypatch):
    _stub_load(monkeypatch, loader.LoadError('boom'))
    monkeypatch.setattr(loader.time, 'sleep', lambda s: None)
    monkeypatch.setattr(loader.shutil, 'rmtree', _always_fail)
    src = tmp_path / 'pkg'
    src.mkdir()
    with pytest.raises(loader.LoadError, match='boom'):
        load_dataset(src, 'A', tmpdir=str(tmp_path))
