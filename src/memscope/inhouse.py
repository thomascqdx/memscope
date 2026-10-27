"""自研应用判定：白名单优先（按基础包名），否则按包名前缀。"""
from __future__ import annotations

import importlib.resources
from pathlib import Path


def _read_lines(path: Path):
    out = []
    for l in Path(path).read_text(encoding='utf-8').splitlines():
        l = l.strip()
        if not l or l.startswith('#'):
            continue
        out.append(l)
    return out


def load_whitelist(path=None):
    """白名单文件：每行一个包名，忽略空行/#注释/前导 `package:`。文件不存在时返回空集。"""
    if path is None:
        default = Path('config/internal_dev_package.txt')
        if not default.exists():
            return set(), None
        path = default
    else:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f'白名单文件不存在: {path}')
    items = set()
    for l in _read_lines(path):
        if l.startswith('package:'):
            l = l[len('package:'):]
        items.add(l)
    return items, str(path)


def _load_prefix_file(path, local_default, asset_name, missing_msg):
    if path is not None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f'{missing_msg}: {path}')
        return _read_lines(path), str(path)
    local = Path(local_default)
    if local.exists():
        return _read_lines(local), str(local)
    ref = importlib.resources.files('memscope.assets').joinpath(asset_name)
    return [l for l in ref.read_text(encoding='utf-8').splitlines()
            if l.strip() and not l.strip().startswith('#')], '(内置)'


def load_prefixes(path=None):
    """前缀文件：--prefixes > ./config/inhouse_prefixes.txt > 包内置默认。"""
    return _load_prefix_file(path, 'config/inhouse_prefixes.txt',
                             'inhouse_prefixes.txt', '前缀文件不存在')


def load_google_prefixes(path=None):
    """Google 前缀文件：--google-prefixes > ./config/google_prefixes.txt > 包内置默认。"""
    return _load_prefix_file(path, 'config/google_prefixes.txt',
                             'google_prefixes.txt', 'Google 前缀文件不存在')


def is_google(name, prefixes) -> bool:
    return any(name.startswith(p) for p in prefixes)


def base_pkg(name: str) -> str:
    return name.split(':')[0]


def classify(name: str, whitelist: set, prefixes) -> str | None:
    """返回 'whitelist' | 'prefix' | None。"""
    if base_pkg(name) in whitelist:
        return 'whitelist'
    if any(name.startswith(p) for p in prefixes):
        return 'prefix'
    return None
