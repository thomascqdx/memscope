"""数据模型：测试包解析结果。所有内存数值单位均为 MB，保留完整精度。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Event:
    """一条 events_log 事件。t 为补全年份后的时间；phase ∈ boot|test。"""
    t: datetime
    kind: str  # start | kill | died | lmk | anr | crash | lowmem
    proc: str = ""
    reason: str = ""
    comp: str = ""
    caller: str = ""
    adj: str = ""
    phase: str = ""
    pid: Optional[int] = None
    rss_mb: Optional[float] = None
    code: Optional[int] = None


@dataclass
class RoundData:
    index: int
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    uptime_ms: Optional[int] = None
    # pss[level] = {'total': mb, 'procs': {name: mb}}
    pss: dict = field(default_factory=dict)
    # rss[level] = {proc_name: mb}
    rss: dict = field(default_factory=dict)
    tail: dict = field(default_factory=dict)     # dumpsys 尾部汇总, MB
    meminfo: dict = field(default_factory=dict)  # /proc/meminfo, MB
    vmstat: dict = field(default_factory=dict)   # /proc/vmstat 累计计数器（存在才解析）


@dataclass
class Thresholds:
    levels: dict = field(default_factory=dict)        # level -> threshold MB
    procs: dict = field(default_factory=dict)         # name -> {'level':..,'threshold':..}
    free_ram: Optional[float] = None                  # 下限阈值
    available: bool = False


@dataclass
class Dataset:
    label: str
    path: str = ""
    device: dict = field(default_factory=dict)
    packages: list = field(default_factory=list)
    rounds: list = field(default_factory=list)        # list[RoundData]
    events: list = field(default_factory=list)        # list[Event]
    boot_time: Optional[datetime] = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    warnings: list = field(default_factory=list)
    failed_rounds: list = field(default_factory=list)  # list[dict(index, reason)]
    reboots: list = field(default_factory=list)        # list[dict(after_round, round)]
    platform: dict = field(default_factory=dict)       # 平台判定（Exception_Summary_Report.xlsx）

    @property
    def n(self) -> int:
        return len(self.rounds)
