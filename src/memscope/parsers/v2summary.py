"""解析 2.0 包的 *_Exception_Summary_Report.xlsx（openpyxl read_only）。

产出与 3.0 all_memory_data.xls 相同的 Thresholds（RSS 口径，MB），
以及平台判定字段 dict（static_result/dynamic_result/tool_version/
std_version/rom/std_tier/valid_count）。"""
from __future__ import annotations

import io
import re

import openpyxl

from ..model import Thresholds

_RESULT = {
    'static_result': re.compile(r'(Rss|RSS|Pss|PSS)?内存标准静态测试结果[：:]'),
    'dynamic_result': re.compile(r'(Rss|RSS|Pss|PSS)?内存标准动态测试结果[：:]'),
    'tool_version': re.compile(r'检测工具版本'),
    'std_version': re.compile(r'内存标准版本'),
    'rom': re.compile(r'ROM版本'),
}
_TIER = re.compile(r'^(\d+GB)内存标准[：:]')
_TH_STATIC = 'RSS静态内存占用标准(MB)'
_TH_DYNAMIC = 'RSS动态内存占用标准(MB)'
_VALID = '有效测试次数'


def parse_summary(data: bytes, kind: str) -> tuple:
    """返回 (Thresholds, platform dict)。kind ∈ {'static','dynamic'}。"""
    th = Thresholds()
    platform = {}
    th_col = None   # 标准表中当前 kind 对应列
    valid_col = None
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                c0 = str(row[0]).strip() if row and row[0] is not None else ''
                if c0:
                    for key, pat in _RESULT.items():
                        if key not in platform and pat.match(c0):
                            platform[key] = row[1] if len(row) > 1 else None
                    tm = _TIER.match(c0)
                    if tm:
                        platform['std_tier'] = tm.group(1)
                cells = [str(c).strip() if c is not None else '' for c in row]
                if _TH_STATIC in cells and _TH_DYNAMIC in cells:
                    th_col = cells.index(
                        _TH_STATIC if kind == 'static' else _TH_DYNAMIC)
                    continue
                if 'valid_count' not in platform and _VALID in cells:
                    valid_col = cells.index(_VALID)
                    continue
                if th_col is not None:
                    if not c0:
                        th_col = None
                        continue
                    v = row[th_col] if th_col < len(row) else None
                    if isinstance(v, (int, float)) and v > 0:
                        if c0 == 'Free RAM':
                            th.free_ram = v
                        else:
                            th.levels[c0] = v
                    continue
                if (valid_col is not None and 'valid_count' not in platform
                        and c0 and valid_col < len(row)):
                    v = row[valid_col]
                    if isinstance(v, (int, float)):
                        platform['valid_count'] = int(v)
                    else:
                        try:
                            platform['valid_count'] = int(str(v).strip())
                        except (TypeError, ValueError):
                            pass
                    valid_col = None
    finally:
        wb.close()
    th.available = bool(th.levels) or th.free_ram is not None
    return th, platform
