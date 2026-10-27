"""版本 bump：唯一来源 src/memscope/__init__.py 的 __version__ 行。

用法：python scripts/bump_version.py {major|minor|patch|X.Y.Z}
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / 'src' / 'memscope' / '__init__.py'
_RE = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def bump(current: str, part: str) -> str:
    """返回新版本号；非法输入抛 ValueError。"""
    try:
        major, minor, patch = (int(x) for x in current.split('.'))
    except ValueError:
        raise ValueError(f'当前版本非法: {current}')
    if part == 'major':
        new = (major + 1, 0, 0)
    elif part == 'minor':
        new = (major, minor + 1, 0)
    elif part == 'patch':
        new = (major, minor, patch + 1)
    else:
        try:
            v = tuple(int(x) for x in part.split('.'))
        except ValueError:
            raise ValueError(f'非法目标版本: {part}')
        if len(v) != 3:
            raise ValueError(f'非法目标版本: {part}')
        new = v
        if part == current:
            raise ValueError(f'目标版本与当前相同: {part}')
    return '.'.join(str(x) for x in new)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print('用法: bump_version.py {major|minor|patch|X.Y.Z}', file=sys.stderr)
        return 2
    text = INIT.read_text(encoding='utf-8')
    m = _RE.search(text)
    if not m:
        print(f'未找到 __version__: {INIT}', file=sys.stderr)
        return 2
    old = m.group(1)
    try:
        new = bump(old, argv[0])
    except ValueError as e:
        print(f'错误: {e}', file=sys.stderr)
        return 2
    INIT.write_text(text[:m.start(1)] + new + text[m.end(1):],
                    encoding='utf-8')
    print(f'{old} -> {new}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
