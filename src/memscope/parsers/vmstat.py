"""解析 round_N/proc_vmstat.txt（/proc/vmstat 快照，`name value` 逐行累计计数器）。"""
from __future__ import annotations

# 只保留分析需要的计数器（allocstall_* 全部保留用于求和）
_WANTED = {
    'pswpin', 'pswpout', 'pgmajfault',
    'workingset_refault_file', 'workingset_refault_anon',
    'pgscan_direct', 'pgsteal_direct', 'pgscan_kswapd',
    'compact_stall', 'oom_kill',
}


def parse_vmstat(text: str) -> dict:
    """返回 {counter: int}。忽略空行与非法行。"""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, val = parts
        if name not in _WANTED and not name.startswith('allocstall_'):
            continue
        try:
            out[name] = int(val)
        except ValueError:
            continue
    return out
