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

# 超标明细小节（首列 strip 后按冒号前文本前缀匹配，兼容全/半角冒号与尾部空白）
_FAIL_SECTIONS = {
    'level': '层级内存占用超标 Summary',
    'inhouse': '层级自研进程个数超标 Summary',
    'resident': '常驻进程内存占用超标 Summary',
}


def _jcell(v):
    """单元格 → JSON 安全值（日期等转为 str）。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def parse_fail_sections(rows):
    """sheet 行迭代 → {key: {'header': [...], 'rows': [[...]]}}。

    小节 = 标题行之后、下一个小节标题（含 'Summary'）/键值行（短行且
    首列以冒号结尾）/sheet 结束之前的非空行；首行非空行作表头，其余为
    数据行，尾端 None 单元格裁掉。小节不存在或无数据 → {'header': [], 'rows': []}。
    """
    out = {k: {'header': [], 'rows': []} for k in _FAIL_SECTIONS}
    cur = None
    for row in rows:
        cells = [_jcell(c) for c in row]
        while cells and cells[-1] is None:
            cells.pop()
        c0 = str(cells[0]).strip() if cells and cells[0] is not None else ''
        if c0:
            hit = next((k for k, p in _FAIL_SECTIONS.items()
                        if c0.startswith(p)), None)
            if hit is not None:
                cur = hit
                continue
        if cur is None or not cells:
            continue
        # 其它小节标题（含 'Summary'）或键值行（短行且首列以冒号结尾；
        # 数据行进程名可能以 ':' 结尾，如 ...SandboxedProcessService0:）
        if 'Summary' in c0 or (len(cells) <= 2 and c0.endswith(('：', ':'))):
            cur = None
            continue
        blk = out[cur]
        if not blk['header']:
            blk['header'] = cells
        else:
            blk['rows'].append(cells)
    return out


def parse_summary(data: bytes, kind: str) -> tuple:
    """返回 (Thresholds, platform dict)。kind ∈ {'static','dynamic'}。"""
    th = Thresholds()
    platform = {}
    th_col = None   # 标准表中当前 kind 对应列
    valid_col = None
    first_rows = []
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    try:
        for si, ws in enumerate(wb.worksheets):
            for row in ws.iter_rows(values_only=True):
                if si == 0:
                    first_rows.append(row)
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
    platform['fail_summary'] = parse_fail_sections(first_rows)
    th.available = bool(th.levels) or th.free_ram is not None
    return th, platform
