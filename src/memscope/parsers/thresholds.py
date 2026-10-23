"""解析 all_memory_data.xls（健康标准阈值，RSS 口径，单位 MB）。仅 Threshold>0 计入。"""
from __future__ import annotations

import xlrd

from ..model import Thresholds


def parse_thresholds(data: bytes) -> Thresholds:
    wb = xlrd.open_workbook(file_contents=data)
    th = Thresholds(available=True)
    for sh in wb.sheets():
        if sh.nrows < 2:
            continue
        header = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
        try:
            ci_th = header.index('Threshold')
        except ValueError:
            continue
        if sh.name == '层级内存详情':
            for r in range(1, sh.nrows):
                name = str(sh.cell_value(r, 0)).strip()
                v = sh.cell_value(r, ci_th)
                if not name or not isinstance(v, (int, float)) or v <= 0:
                    continue
                if name == 'Free RAM':
                    th.free_ram = v
                else:
                    th.levels[name] = v
        elif sh.name == '进程内存详情':
            for r in range(1, sh.nrows):
                name = str(sh.cell_value(r, 0)).strip()
                level = str(sh.cell_value(r, 1)).strip()
                v = sh.cell_value(r, ci_th)
                if not name or not isinstance(v, (int, float)) or v <= 0:
                    continue
                th.procs[name] = {'level': level, 'threshold': v}
    return th
