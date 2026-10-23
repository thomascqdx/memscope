"""PyInstaller 入口脚本：memscope/__main__.py 用相对导入，不能直接作入口。"""
import sys

from memscope.cli import main

if __name__ == '__main__':
    sys.exit(main())
