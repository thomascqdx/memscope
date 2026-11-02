"""解析 2.0 包的 devinfo/*.txt、lmkcnt_*.txt 及顶层 log 文件。"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_MARKET = re.compile(r'\(([A-Z]+?)\d')
_GETPROP = re.compile(r'^\[([^\]]+)\]:\s*\[(.*)\]\s*$')
_KV = re.compile(r'^([^:\n]+):\s*(.*?)\s*$')
_ROUND = re.compile(r'=+\s*round_(\d+)\s*=+')
_PARAM = re.compile(r'^(-\S+):\s*(.*?)\s*$')
_NODATA = re.compile(r'round\s+(\d+)')
_TS = '%Y-%m-%d %H:%M:%S,%f'
_DEVICE_DATE = '%a %b %d %H:%M:%S %Y'


@dataclass
class RoundLog:
    start: datetime                    # 主机时间（round_N 行的 log 时间戳）
    device: datetime | None = None     # 设备时间（首个 device date，去掉时区 token）
    nodata: bool = False               # "No memory info ... round N"


@dataclass
class RunLog:
    params: dict = field(default_factory=dict)
    rounds: dict[int, RoundLog] = field(default_factory=dict)

    @property
    def test_type(self):
        return self.params.get('-std')


def parse_kv(text: str) -> dict:
    """deviceinfo.txt：`key: value` 行；value 可为空。"""
    out = {}
    for line in text.splitlines():
        m = _KV.match(line)
        if m:
            out[m.group(1).strip()] = m.group(2)
    return out


def parse_getprop(text: str) -> dict:
    """getprop.txt：`[k]: [v]` 行。"""
    out = {}
    for line in text.splitlines():
        m = _GETPROP.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def device_fields(devinfo: dict, props: dict, test_type: str | None) -> dict:
    """生成与 3.0 device_info.txt 相同的字段（键与顺序一致，空值跳过）。"""
    def v(key):
        return (props.get(key) or devinfo.get(key) or '').strip()

    display_id = v('ro.build.display.id')
    m = _MARKET.search(display_id)
    pairs = [
        ('Device Model', props.get('ro.product.model')),
        ('Brand', props.get('ro.product.brand')),
        ('Market', m.group(1) if m else None),
        ('SN Number', props.get('ro.serialno')),
        ('Test Type', test_type),
        ('Android Version', v('ro.build.version.release')),
        ('Device', props.get('ro.product.device')),
        ('Rom Version', display_id),
        ('TOS Version', props.get('ro.tranos.version')),
        ('Platform', v('ro.board.platform')),
        ('Kernel Version', devinfo.get('ro.kernel.version')),
    ]
    return {k: val for k, val in pairs if val}


def parse_packages(text: str) -> list:
    """package_list.txt：`package:<name>` 行 → 去重排序。"""
    return sorted(set(re.findall(r'package:(\S+)', text)))


def parse_lmkcnt(text: str):
    """lmkcnt_*.txt：`Total number of kills: N` → int 或 None。"""
    m = re.search(r'Total number of kills:\s*(\d+)', text)
    return int(m.group(1)) if m else None


def _ts(line: str):
    try:
        return datetime.strptime(line[:23], _TS)
    except ValueError:
        return None


def _device_date(v: str):
    """'Mon Oct 26 16:25:56 CST 2026' → datetime（丢弃时区）；'unknown' → None。"""
    p = v.split()
    if len(p) < 5 or not p[-1].isdigit():
        return None
    try:
        return datetime.strptime(f'{p[0]} {p[1]} {p[2]} {p[3]} {p[-1]}',
                                 _DEVICE_DATE)
    except ValueError:
        return None


def parse_runlog(text: str) -> RunLog:
    """顶层 log：Script Parameters 区段参数 + 各 round 的起止/设备时间/缺数标记。"""
    rl = RunLog()
    in_params = False
    cur: RoundLog | None = None
    cur_dseen = False  # 本轮已消费首个 device date 行
    for line in text.splitlines():
        if "Command '" in line:
            continue
        if 'Script Parameters' in line:
            in_params = True
            continue
        m = _ROUND.search(line)
        if m:
            cur = RoundLog(start=_ts(line))
            rl.rounds[int(m.group(1))] = cur
            cur_dseen = False
            continue
        if in_params:
            msg = line.rsplit(' - ', 1)[-1]
            if '=' in msg and not msg.strip('=').strip():
                in_params = False
                continue
            pm = _PARAM.match(msg)
            if pm:
                rl.params[pm.group(1)] = pm.group(2)
            continue
        if cur is not None and not cur_dseen and 'device date:' in line:
            cur.device = _device_date(line.split('device date:', 1)[1].strip())
            cur_dseen = True
            continue
        if 'No memory info' in line:
            nm = _NODATA.search(line)
            if nm and int(nm.group(1)) in rl.rounds:
                rl.rounds[int(nm.group(1))].nodata = True
    return rl


def clock_offset(runlog: RunLog) -> timedelta | None:
    """各轮 (主机时间 − 设备时间) 的中位数；无可用轮 → None。"""
    deltas = [r.start - r.device for r in runlog.rounds.values()
              if r.start is not None and r.device is not None]
    return statistics.median(deltas) if deltas else None
