"""统一分析：两个 Dataset → JSON 可序列化 result dict（所有渲染器共用同一份数据）。

静态/动态共用同一套口径与 schema：rmean/gap/is_significant/层级映射/自研判定 +
分布统计（P50/P90/P95）+ 小时/轮次时间轴趋势回归 + vmstat 内存压力 +
LMK/ANR/crash 事件统计 + 平台健康标准对照 + 自动结论（findings）。
场景差异仅来自数据有无与按 kind 取的默认参数（primary_metric / 泄漏门限 / 趋势最少点数）。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from math import ceil

from . import __version__
from .inhouse import classify, is_google
from .model import Dataset, RoundData
from .parsers.procstatus import oom_to_level

LEVEL_ORDER = ['Native', 'System', 'Persistent', 'Persistent Service', 'Foreground',
               'Visible', 'Perceptible', 'Perceptible Medium', 'Perceptible Low',
               'Backup', 'Heavy Weight', 'A Services', 'Home', 'Previous',
               'B Services', 'Cached']

SYSTEM_FIELDS = [
    ('Total RAM', 'tail'), ('Free RAM', 'tail'), ('cached pss', 'tail'),
    ('cached kernel', 'tail'), ('free', 'tail'), ('Used RAM', 'tail'),
    ('used pss', 'tail'), ('kernel', 'tail'), ('Lost RAM', 'tail'),
    ('ZRAM physical', 'tail'), ('in swap', 'tail'), ('DMA-BUF', 'tail'),
    ('GPU', 'tail'),
    ('MemTotal', 'proc'), ('MemFree', 'proc'), ('MemAvailable', 'proc'),
    ('Buffers', 'proc'), ('Cached', 'proc'), ('SwapCached', 'proc'),
    ('Active', 'proc'), ('Inactive', 'proc'), ('AnonPages', 'proc'),
    ('Mapped', 'proc'), ('Shmem', 'proc'), ('KReclaimable', 'proc'),
    ('Slab', 'proc'), ('SReclaimable', 'proc'), ('SUnreclaim', 'proc'),
    ('KernelStack', 'proc'), ('PageTables', 'proc'), ('VmallocUsed', 'proc'),
    ('SwapTotal', 'proc'), ('SwapUsed', 'proc'), ('CmaTotal', 'proc'),
    ('CmaFree', 'proc'),
]
HIGHER_BETTER = {'Free RAM', 'MemFree', 'MemAvailable', 'free', 'SwapFree'}

# kill_reason AOSP 枚举（LmkdStatsReporter.java）；其它值显示为厂商扩展(N)
LMK_REASONS = {
    0: 'PRESSURE_AFTER_KILL', 1: 'NOT_RESPONDING', 2: 'LOW_SWAP_AND_THRASHING',
    3: 'LOW_MEM_AND_SWAP', 4: 'LOW_MEM_AND_THRASHING',
    5: 'DIRECT_RECL_AND_THRASHING', 6: 'LOW_MEM_AND_SWAP_UTIL',
    7: 'LOW_FILECACHE_AFTER_THRASHING', 8: 'LOW_MEM', 9: 'DIRECT_RECL_STUCK',
}

# name → (unit, 需要 ×4KB→MB)
PRESSURE_COUNTERS = [
    ('pswpin', 'MB'), ('pswpout', 'MB'), ('pgmajfault', '次'),
    ('workingset_refault_file', '页'), ('workingset_refault_anon', '页'),
    ('allocstall', '次'), ('pgscan_direct', '页'), ('pgsteal_direct', '页'),
    ('pgscan_kswapd', '页'), ('compact_stall', '次'), ('oom_kill', '次'),
]
_SYS_LEAK_FIELDS = (('Used RAM', 'up'), ('Free RAM', 'down'),
                    ('MemAvailable', 'down'))
_SEV_ORDER = {'high': 0, 'mid': 1, 'low': 2, 'info': 3}
_CAT_ORDER = {'数据': -1, '标准': 0, 'LMK': 1, '泄漏': 2, '稳定性': 3, '压力': 4,
              '层级': 5, '整机': 6, '进程': 7, '归类': 8}
# 只有常驻层级做泄漏判定；其余层级成员随负载变化，趋势不代表泄漏
LEAK_LEVELS = {'Native', 'System', 'Persistent', 'Persistent Service'}
# 测试工具进程：不算泄漏、不进进程类 findings（数据仍保留）
TEST_TOOL_PREFIXES = ('com.android.commands.monkey',)
# 轮数 ≤ small_n → 轮次类目轴 + 进程每轮层级明细（pra/prb）
SMALL_N = 30
# datasets[i].events 内嵌上限；超过则置 None（明细见 data.xlsx）
EVENTS_INLINE_MAX = 5000

# 按测试类型的默认参数：primary_metric / 泄漏与趋势门限
_KIND_PARAMS = {
    'static': {'resident': 1.0, 'leak_delta': 10.0, 'leak_pct': 0.0,
               'leak_r2': 0.6, 'sys_leak_delta': 50.0,
               'primary_metric': 'pss', 'trend_min': 5},
    'dynamic': {'resident': 0.9, 'leak_delta': 10.0, 'leak_pct': 20.0,
                'leak_r2': 0.3, 'sys_leak_delta': 100.0,
                'primary_metric': 'rss', 'trend_min': 10},
}


def _is_test_tool(name: str) -> bool:
    return name.startswith(TEST_TOOL_PREFIXES)


@dataclass
class Options:
    kind: str = 'static'          # 'static' | 'dynamic'
    gap_abs: float = 5.0
    gap_pct: float = 10.0
    resident: float = 1.0         # 常驻阈值：出现率 >= resident
    leak_delta: float = 10.0      # 进程泄漏 delta 门限 MB
    leak_pct: float = 0.0         # 进程泄漏 delta 相对门限 %
    leak_r2: float = 0.6          # 泄漏判定 R2 门限
    sys_leak_delta: float = 50.0  # 层级/整机泄漏 delta 门限 MB
    rate_pct: float = 20.0        # 频率对比阈值 %（LMK/压力）
    primary_metric: str = 'pss'
    trend_min: int = 5            # 趋势回归最少出现轮数
    whitelist: set = None
    whitelist_src: str = None
    prefixes: tuple = ()
    prefixes_src: str = None
    google_prefixes: tuple = ()
    google_prefixes_src: str = None


def default_options(kind: str) -> dict:
    """CLI argparse 默认值（每种测试类型的默认参数）。"""
    d = {'kind': kind, 'gap_abs': 5.0, 'gap_pct': 10.0, 'rate_pct': 20.0}
    d.update(_KIND_PARAMS[kind])
    return d


# ---------- 基础统计 ----------
def cnt(v):
    return sum(1 for x in v if x is not None)


def rmean(v, n):
    """轮次均值（轮均）：缺失轮按 0 计入。"""
    return sum(x or 0.0 for x in v) / n if n else 0.0


def vmean(v):
    """数值均值（值均）：仅在出现轮次上取均值；无出现返回 None。"""
    a = [x for x in v if x is not None]
    return sum(a) / len(a) if a else None


def gap_info(a, b):
    """返回 {'gap','pct','txt'}；A==0→新增，B==0→消失。"""
    g = b - a
    if a == 0 and b == 0:
        return {'gap': 0.0, 'pct': 0.0, 'txt': None}
    if a == 0:
        return {'gap': g, 'pct': None, 'txt': '新增'}
    if b == 0:
        return {'gap': g, 'pct': None, 'txt': '消失'}
    return {'gap': g, 'pct': g / a * 100.0, 'txt': None}


def is_significant(ra, rb, opts: Options):
    g = abs(rb - ra)
    if g < opts.gap_abs:
        return False
    if ra == 0 or rb == 0:
        return True
    return g / ra >= opts.gap_pct / 100.0


def pct(sorted_vals, q: float):
    """第 q 百分位（0-100），线性插值（inclusive，同 numpy 默认 / Excel PERCENTILE.INC）。

    已用真实数据验证：与平台 Exception_Summary_Level.xlsx / level_memory 汇总文件
    的百分位完全一致（如 A Native P90=1846.11）；all_memory_data.xls 的尾部百分位
    与其自身其它报表差 ~0.01-0.02（平台内部口径差异），采用插值法为准。
    """
    n = len(sorted_vals)
    if not n:
        return None
    pos = q / 100.0 * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + (sorted_vals[lo + 1] - sorted_vals[lo]) * frac


def dist(series):
    """单序列分布：k/n/rate/mi（缺席按 0）+ mean/min/p50/p90/p95/max（仅出现轮）。"""
    a = [x for x in series if x is not None]
    n = len(series)
    k = len(a)
    d = {'k': k, 'n': n, 'rate': k / n if n else 0.0,
         'mi': rmean(series, n),
         'mean': None, 'min': None, 'p50': None, 'p90': None, 'p95': None,
         'max': None}
    if k:
        s = sorted(a)
        d['mean'] = sum(a) / k
        d['min'] = s[0]
        d['max'] = s[-1]
        d['p50'] = pct(s, 50)
        d['p90'] = pct(s, 90)
        d['p95'] = pct(s, 95)
    return d


def trend(hours, series, min_points=10):
    """在出现轮上对 hours 线性回归：slope MB/小时 + head/tail（前/后 10% 中位数）。"""
    pts = [(h, v) for h, v in zip(hours, series) if v is not None]
    if len(pts) < min_points:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(ys)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    slope = sxy / sxx
    inter = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (inter + slope * x)) ** 2 for x, y in pts)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    m = max(3, int(n * 0.1))
    head = statistics.median(ys[:m])
    tail = statistics.median(ys[-m:])
    return {'slope': slope, 'r2': r2, 'head': head, 'tail': tail,
            'delta': tail - head}


# ---------- 时间轴 ----------
def _hours(ds: Dataset):
    """每轮距测试开始的小时数；采样间隔取相邻有效轮 start 差的中位数；
    另返回中断区间 gaps 与有效时长 valid_hours（扣除中断净时长）。"""
    pairs = [(ds.rounds[j - 1], ds.rounds[j])
             for j in range(1, len(ds.rounds))
             if ds.rounds[j - 1].start and ds.rounds[j].start]
    diffs = [(c.start - p.start).total_seconds() for p, c in pairs]
    iv = statistics.median(diffs) if diffs else None  # 秒
    starts = [r.start for r in ds.rounds if r.start]
    base = starts[0] if starts else None
    hours = []
    for i, r in enumerate(ds.rounds):
        if base is not None and r.start is not None:
            hours.append((r.start - base).total_seconds() / 3600.0)
        elif iv:
            hours.append(i * iv / 3600.0)
        else:
            hours.append(float(i))
    total = hours[-1] if hours else 0.0
    gaps = []
    if iv:
        thr_s = max(3 * iv, 1800)
        for p, c in pairs:
            d = (c.start - p.start).total_seconds()
            if d > thr_s:
                gaps.append({'after_round': p.index, 'round': c.index,
                             'start': p.start.strftime('%Y-%m-%d %H:%M:%S'),
                             'end': c.start.strftime('%Y-%m-%d %H:%M:%S'),
                             'hours': d / 3600.0})
    iv_h = (iv / 3600.0) if iv else 0.0
    valid_hours = total - sum(g['hours'] - iv_h for g in gaps)
    return hours, (iv / 60.0 if iv else None), total, gaps, valid_hours


# ---------- 单指标（pss / rss）数据集内聚合 ----------
def _round_levels(rd, kind):
    """某轮 → {level: (level_total_mb, {proc: mb})}"""
    if kind == 'pss':
        return {lv: (d['total'], d['procs']) for lv, d in rd.pss.items()}
    return {lv: (sum(d.values()), d) for lv, d in rd.rss.items()}


def _dataset_metric(ds: Dataset, kind):
    """单数据集聚合：level 序列、proc→{level:series}。"""
    n = ds.n
    levels = {}
    proc_lv = {}   # proc -> {level: [mb|None]}
    for i, rd in enumerate(ds.rounds):
        for lv, (tot, procs) in _round_levels(rd, kind).items():
            levels.setdefault(lv, [None] * n)[i] = tot
            for p, v in procs.items():
                cur = proc_lv.setdefault(p, {}).setdefault(lv, [None] * n)
                cur[i] = (cur[i] or 0.0) + v
    return levels, proc_lv


def _level_order(union):
    out = [l for l in LEVEL_ORDER if l in union]
    out += sorted(l for l in union if l not in LEVEL_ORDER)
    return out


def _series2(v):
    return [round(x, 2) if x is not None else None for x in v]


def _lv_change_points(full):
    """每轮层级数组 → [[i, level|None]] 变化点：i=0 必发，之后仅值变时发。"""
    return [[i, v] for i, v in enumerate(full)
            if i == 0 or v != full[i - 1]]


def _leak_flag(t, resident_ok: bool, delta_thr: float, opts: Options) -> bool:
    if not resident_ok or t is None:
        return False
    return (t['slope'] > 0 and t['r2'] >= opts.leak_r2
            and t['delta'] >= max(delta_thr, t['head'] * opts.leak_pct / 100.0))


def build_metric(dsa: Dataset, dsb: Dataset, kind: str, opts: Options,
                 ha, hb):
    na, nb = dsa.n, dsb.n
    small = max(na, nb) <= SMALL_N
    lv_a, pl_a = _dataset_metric(dsa, kind)
    lv_b, pl_b = _dataset_metric(dsb, kind)
    levels = _level_order(set(lv_a) | set(lv_b))

    level_rows = []
    for lv in levels:
        sa = lv_a.get(lv, [None] * na)
        sb = lv_b.get(lv, [None] * nb)
        da, db = dist(sa), dist(sb)
        ta, tb = trend(ha, sa, opts.trend_min), trend(hb, sb, opts.trend_min)
        g = gap_info(da['mi'], db['mi'])
        level_rows.append({
            'level': lv, 'a': _series2(sa), 'b': _series2(sb),
            'da': da, 'db': db, 'ta': ta, 'tb': tb,
            'gap': g['gap'], 'gapp': g['pct'], 'gapt': g['txt'],
            'gap_p90': (db['p90'] - da['p90']
                        if da['p90'] is not None and db['p90'] is not None
                        else None),
            'sig': is_significant(da['mi'], db['mi'], opts),
            'ca': sum(1 for p, m in pl_a.items()
                      if lv in m and cnt(m[lv]) > 0),
            'cb': sum(1 for p, m in pl_b.items()
                      if lv in m and cnt(m[lv]) > 0),
            'leak_a': lv in LEAK_LEVELS
                      and _leak_flag(ta, True, opts.sys_leak_delta, opts),
            'leak_b': lv in LEAK_LEVELS
                      and _leak_flag(tb, True, opts.sys_leak_delta, opts),
        })

    procs = {}
    for name in sorted(set(pl_a) | set(pl_b)):
        sa = pl_a.get(name)
        sb = pl_b.get(name)
        ta_ = [None] * na
        tb_ = [None] * nb
        pra = [None] * na if small else None
        prb = [None] * nb if small else None
        lva = [None] * na   # 每轮层级（与 pra 同规则：level 迭代序，后者覆盖）
        lvb = [None] * nb
        lvc_a = {}
        lvc_b = {}
        lmi_a = {}
        lmi_b = {}
        for lv, series in (sa or {}).items():
            k = cnt(series)
            if k:
                lvc_a[lv] = k
                lmi_a[lv] = rmean(series, na)
            for r, v in enumerate(series):
                if v is not None:
                    ta_[r] = (ta_[r] or 0.0) + v
                    lva[r] = lv
                    if small:
                        pra[r] = lv
        for lv, series in (sb or {}).items():
            k = cnt(series)
            if k:
                lvc_b[lv] = k
                lmi_b[lv] = rmean(series, nb)
            for r, v in enumerate(series):
                if v is not None:
                    tb_[r] = (tb_[r] or 0.0) + v
                    lvb[r] = lv
                    if small:
                        prb[r] = lv
        da = dist(ta_)
        db = dist(tb_)
        tda = trend(ha, ta_, opts.trend_min)
        tdb = trend(hb, tb_, opts.trend_min)
        g = gap_info(da['mi'], db['mi'])
        resident_a = da['rate'] >= opts.resident
        resident_b = db['rate'] >= opts.resident
        present_levels = set(lvc_a) | set(lvc_b)
        cached_only = (da['k'] + db['k']) > 0 and present_levels == {'Cached'}
        level_changed = bool(lvc_a and lvc_b) and (
            set(lvc_a) != set(lvc_b) or len(lvc_a) > 1 or len(lvc_b) > 1)
        ih = classify(name, opts.whitelist or set(), opts.prefixes)
        procs[name] = {
            'name': name,
            'a': _series2(ta_) if da['k'] else None,
            'b': _series2(tb_) if db['k'] else None,
            'da': da, 'db': db, 'ta': tda, 'tb': tdb,
            'gap': g['gap'], 'gapp': g['pct'], 'gapt': g['txt'],
            'gap_p90': (db['p90'] - da['p90']
                        if da['p90'] is not None and db['p90'] is not None
                        else None),
            'sig': is_significant(da['mi'], db['mi'], opts),
            'resident_a': resident_a, 'resident_b': resident_b,
            'leak_a': (not _is_test_tool(name)
                       and _leak_flag(tda, resident_a, opts.leak_delta, opts)),
            'leak_b': (not _is_test_tool(name)
                       and _leak_flag(tdb, resident_b, opts.leak_delta, opts)),
            'la': sorted(lvc_a, key=lambda l: -lvc_a[l]),
            'lb': sorted(lvc_b, key=lambda l: -lvc_b[l]),
            'lvc_a': lvc_a, 'lvc_b': lvc_b,
            'lmi_a': lmi_a, 'lmi_b': lmi_b,
            'level_changed': level_changed,
            'pra': pra, 'prb': prb,
            'lvr_a': _lv_change_points(lva) if da['k'] else None,
            'lvr_b': _lv_change_points(lvb) if db['k'] else None,
            'swa': (_series2([dsa.rounds[i].swap.get(name)
                              if ta_[i] is not None else None
                              for i in range(na)])
                    if kind == 'rss' and da['k'] else None),
            'swb': (_series2([dsb.rounds[i].swap.get(name)
                              if tb_[i] is not None else None
                              for i in range(nb)])
                    if kind == 'rss' and db['k'] else None),
            'cached_only': cached_only,
            'inhouse': ih is not None, 'ih_src': ih,
            'google': ih is None and is_google(name, opts.google_prefixes),
        }

    only_a = sorted(n for n, p in procs.items() if p['da']['k'] > 0
                    and p['db']['k'] == 0)
    only_b = sorted(n for n, p in procs.items() if p['db']['k'] > 0
                    and p['da']['k'] == 0)
    changed = sorted(n for n, p in procs.items() if p['level_changed'])
    top = sorted(procs.values(), key=lambda p: -abs(p['gap']))
    return {
        'levels': levels,
        'level_rows': level_rows,
        'procs': procs,
        'only_a': only_a,
        'only_b': only_b,
        'level_changed': changed,
        'top': [p['name'] for p in top],
    }


# ---------- 整机 ----------
def _sys_series(ds: Dataset, field, src):
    if src == 'tail':
        return [rd.tail.get(field) for rd in ds.rounds]
    return [rd.meminfo.get(field) for rd in ds.rounds]


def build_system(dsa: Dataset, dsb: Dataset, opts: Options, ha, hb):
    rows = []
    for field, src in SYSTEM_FIELDS:
        a = _sys_series(dsa, field, src)
        b = _sys_series(dsb, field, src)
        da, db = dist(a), dist(b)
        ta, tb = trend(ha, a, opts.trend_min), trend(hb, b, opts.trend_min)
        g = gap_info(da['mean'] or 0.0, db['mean'] or 0.0)
        rows.append({
            'name': field,
            'src': 'dumpsys meminfo' if src == 'tail' else '/proc/meminfo',
            'hb': field in HIGHER_BETTER,
            'a': _series2(a), 'b': _series2(b),
            'da': da, 'db': db, 'ta': ta, 'tb': tb,
            'gap': g['gap'], 'gapp': g['pct'], 'gapt': g['txt'],
            'leak_a': _sys_leak(field, ta, opts),
            'leak_b': _sys_leak(field, tb, opts),
        })
    return rows


def _sys_leak(field, t, opts: Options) -> bool:
    """Used RAM 上升 / Free RAM、MemAvailable 下降；|delta|>=sys_leak_delta 且 r2 达标。"""
    if field not in dict(_SYS_LEAK_FIELDS) or t is None:
        return False
    want = dict(_SYS_LEAK_FIELDS)[field]
    d = t['delta'] if want == 'up' else -t['delta']
    return d >= opts.sys_leak_delta and t['r2'] >= opts.leak_r2


# ---------- 内存压力（proc_vmstat 增量；无 vmstat → 空计数器） ----------
def _vm_counter(rd: RoundData, name: str):
    if name == 'allocstall':
        vs = [v for k, v in rd.vmstat.items() if k.startswith('allocstall_')]
        return sum(vs) if vs else None
    return rd.vmstat.get(name)


def build_pressure(dsa: Dataset, dsb: Dataset, ha, hb):
    counters = []
    for name, unit in PRESSURE_COUNTERS:
        row = {'name': name, 'unit': unit}
        for tag, ds, hh in (('a', dsa, ha), ('b', dsb, hb)):
            raw = [_vm_counter(rd, name) for rd in ds.rounds]
            if not any(v is not None for v in raw):
                row[tag] = None
                row['s' + tag] = None
                continue
            reboot_at = {r['round'] for r in ds.reboots}
            deltas = [None]
            cov = 0.0
            for i in range(1, len(raw)):
                if (raw[i] is None or raw[i - 1] is None
                        or ds.rounds[i].index in reboot_at
                        or raw[i] - raw[i - 1] < 0):
                    deltas.append(None)
                    continue
                deltas.append(raw[i] - raw[i - 1])
                cov += hh[i] - hh[i - 1]
            vals = [d for d in deltas if d is not None]
            if unit == 'MB':
                deltas = [d * 4 / 1024.0 if d is not None else None
                          for d in deltas]
                vals = [d * 4 / 1024.0 for d in vals]
            total = sum(vals)
            if cov <= 0 and vals:
                cov = hh[-1] - hh[0] if len(hh) > 1 else 0.0
            row[tag] = _series2(deltas)
            row['s' + tag] = {
                'total': total,
                'per_hour': (total / cov) if cov > 0 else None,
                'p90': pct(sorted(vals), 90) if vals else None,
                'cov_hours': cov if cov > 0 else None,
            }
        sa, sb = row.get('sa'), row.get('sb')
        if sa and sb and sa['per_hour'] is not None and sb['per_hour'] is not None:
            gap = sb['per_hour'] - sa['per_hour']
            row['gap'] = gap
            row['gapp'] = (gap / sa['per_hour'] * 100.0
                           if sa['per_hour'] else None)
        else:
            row['gap'] = row['gapp'] = None
        counters.append(row)
    counters = [c for c in counters if c.get('a') is not None
                or c.get('b') is not None]
    return {'counters': counters}


# ---------- 事件统计（phase=='test'） ----------
def _lmk_reason(code):
    if code is None:
        return '未知'
    return LMK_REASONS.get(code, f'厂商扩展({code})')


def build_events(ds: Dataset, hours_total: float, gaps=None, iv_s=None):
    # 事件覆盖率口径：仅剔除零事件覆盖的中断区间（日志可能在 adb 离线时继续）
    ev_hours = hours_total
    if gaps:
        iv_h = (iv_s or 0) / 3600.0
        test_ts = [e.t for e in ds.events if e.phase == 'test']
        for g in gaps:
            gs = datetime.strptime(g['start'], '%Y-%m-%d %H:%M:%S')
            ge = datetime.strptime(g['end'], '%Y-%m-%d %H:%M:%S')
            if not any(gs < t < ge for t in test_ts):
                ev_hours -= g['hours'] - iv_h
    t0 = ds.rounds[0].start if ds.rounds and ds.rounds[0].start else None
    evs = [e for e in ds.events if e.phase == 'test']

    def hour_idx(t):
        if t0 is None:
            return None
        h = int((t - t0).total_seconds() // 3600)
        return h if h >= 0 else None

    nh = max(1, int(ceil(hours_total))) if hours_total else 0
    lmk_evs = [e for e in evs if e.kind == 'lmk']
    hourly = [0] * nh
    by_level = {}
    by_reason = {}
    by_proc = {}
    rss_by_proc = {}
    lst = []
    for e in lmk_evs:
        hi = hour_idx(e.t)
        if hi is not None and hi < nh:
            hourly[hi] += 1
        try:
            lv = oom_to_level(int(e.adj))
        except (TypeError, ValueError):
            lv = 'unknown'
        by_level[lv] = by_level.get(lv, 0) + 1
        rk = (e.code, _lmk_reason(e.code))
        by_reason[rk] = by_reason.get(rk, 0) + 1
        by_proc[e.proc] = by_proc.get(e.proc, 0) + 1
        if e.rss_mb is not None:
            rss_by_proc.setdefault(e.proc, []).append(e.rss_mb)
        lst.append({'t': e.t.strftime('%Y-%m-%d %H:%M:%S'), 'proc': e.proc,
                    'adj': e.adj, 'level': lv, 'rss_mb': e.rss_mb,
                    'code': e.code})
    lmk = {
        'total': len(lmk_evs),
        'per_hour': (len(lmk_evs) / ev_hours) if ev_hours else None,
        'by_level': by_level,
        'by_reason': [{'code': c, 'name': nm, 'n': n}
                      for (c, nm), n in sorted(by_reason.items(),
                                               key=lambda x: -x[1])],
        'top': [{'proc': p, 'n': n,
                 'rss_mean': (sum(rss_by_proc[p]) / len(rss_by_proc[p])
                            if p in rss_by_proc else None)}
                for p, n in sorted(by_proc.items(), key=lambda x: -x[1])[:30]],
        'hourly': hourly,
        'list': lst,
    }

    kills = [e for e in evs if e.kind == 'kill']
    krs = {}
    kps = {}
    for e in kills:
        krs[e.reason or '(无)'] = krs.get(e.reason or '(无)', 0) + 1
        kps[e.proc] = kps.get(e.proc, 0) + 1
    am_kill = {
        'total': len(kills),
        'by_reason': [{'reason': r, 'n': n}
                      for r, n in sorted(krs.items(), key=lambda x: -x[1])[:20]],
        'top': [{'proc': p, 'n': n}
                for p, n in sorted(kps.items(), key=lambda x: -x[1])[:20]],
    }

    starts = [e for e in evs if e.kind == 'start']
    sps = {}
    for e in starts:
        sps[e.proc] = sps.get(e.proc, 0) + 1

    return {
        'lmk': lmk,
        'am_kill': am_kill,
        'died_total': sum(1 for e in evs if e.kind == 'died'),
        'starts_total': len(starts),
        'start_top': [{'proc': p, 'n': n}
                      for p, n in sorted(sps.items(), key=lambda x: -x[1])[:30]],
        'anr': [{'t': e.t.strftime('%Y-%m-%d %H:%M:%S'), 'proc': e.proc,
                 'reason': e.reason} for e in evs if e.kind == 'anr'],
        'crash': [{'t': e.t.strftime('%Y-%m-%d %H:%M:%S'), 'proc': e.proc,
                   'reason': e.reason} for e in evs if e.kind == 'crash'],
        'lowmem_total': sum(1 for e in evs if e.kind == 'lowmem'),
        'ev_hours': ev_hours,
    }


# ---------- 健康标准对照（RSS 口径，用各侧自己的阈值） ----------
def build_standards(ds: Dataset, mrss, side: str, opts: Options):
    th = ds.thresholds
    if not th.available:
        return None
    n = ds.n
    rss_levels = _dataset_metric(ds, 'rss')[0]
    res = {'levels': [], 'free_ram': None, 'procs': []}
    fail = []
    for lv, t in th.levels.items():
        members = th.level_groups.get(lv, (lv,))
        s = []
        for i in range(n):
            vals = [rss_levels[m][i] for m in members
                    if m in rss_levels and rss_levels[m][i] is not None]
            s.append(sum(vals) if vals else None)
        d = dist(s)
        if d['mean'] is None:
            continue
        viol = d['mean'] > t
        res['levels'].append({
            'name': lv, 'members': list(members), 'thr': t,
            'mean': d['mean'], 'p90': d['p90'],
            'peak': d['max'], 'viol': viol,
            'exceed_rate': cnt([x for x in s if x is not None and x > t]) / n
            if n else 0.0})
        if viol:
            fail.append(lv)
    if th.free_ram is not None:
        s = _sys_series(ds, 'Free RAM', 'tail')
        d = dist(s)
        if d['mean'] is not None:
            p10 = pct(sorted(x for x in s if x is not None), 10)
            viol = d['mean'] < th.free_ram
            res['free_ram'] = {
                'name': 'Free RAM', 'thr': th.free_ram, 'mean': d['mean'],
                'p10': p10, 'low': d['min'], 'viol': viol,
                'exceed_rate': cnt([x for x in s
                                    if x is not None and x < th.free_ram]) / n
                if n else 0.0}
            if viol:
                fail.append('Free RAM')
    for name, dd in th.procs.items():
        p = mrss['procs'].get(name)
        if not p:
            continue
        s = p[side]
        if not s or not cnt(s):
            continue
        vals = [x for x in s if x is not None]
        mean = sum(vals) / len(vals)
        viol = mean > dd['threshold']
        res['procs'].append({
            'name': name, 'level': dd['level'], 'thr': dd['threshold'],
            'mean': mean, 'peak': max(vals), 'viol': viol,
            'exceed_rate': (sum(1 for x in vals if x > dd['threshold'])
                            / len(vals))})
    res['levels'].sort(key=lambda r: LEVEL_ORDER.index(r['name'])
                       if r['name'] in LEVEL_ORDER else 99)
    res['procs'].sort(key=lambda r: -r['thr'])
    res['verdict'] = 'Fail' if fail else 'Pass'
    res['fail_items'] = fail
    return res


# ---------- 平台判定依据（Exception_Summary_Report 超标 Summary 区块） ----------
def _hcol(header, *subs):
    """按表头文本定位列号；找不到返回 None。"""
    for i, h in enumerate(header):
        if any(s in str(h) for s in subs):
            return i
    return None


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gint(v):
    """单元格 → 次数字符串（数值去尾零）；非数值 → None。"""
    f = _fnum(v)
    return f'{f:g}' if f is not None else None


def platform_basis(fs):
    """platform.fail_summary → 平台判定依据片段列表（level→inhouse→resident）。"""
    parts = []
    lv_rows = (fs.get('level') or {}).get('rows') or []
    if lv_rows:
        names = [str(r[0]) for r in lv_rows if r]
        tail = '、…' if len(names) > 6 else ''
        parts.append(f"层级超标 {len(names)} 项（{'、'.join(names[:6])}{tail}）")
    ih_rows = (fs.get('inhouse') or {}).get('rows') or []
    if ih_rows:
        parts.append(f'自研进程数超标 {len(ih_rows)} 项')
    rs = fs.get('resident') or {}
    rs_rows = rs.get('rows') or []
    if rs_rows:
        h = rs.get('header') or []
        ic_std = _hcol(h, '内存占用标准')
        ic_mean = _hcol(h, '异常占用均值')
        ic_cnt = _hcol(h, '异常次数')
        std_rows, nostd = [], 0
        for r in rs_rows:
            std = (_fnum(r[ic_std])
                   if ic_std is not None and ic_std < len(r) else None)
            if std is None:
                continue
            if std <= 0:
                nostd += 1
                continue
            mean = (_fnum(r[ic_mean])
                    if ic_mean is not None and ic_mean < len(r) else None)
            cnt = (_gint(r[ic_cnt])
                   if ic_cnt is not None and ic_cnt < len(r) else None)
            ratio = mean / std if mean is not None else -1.0
            std_rows.append((ratio, str(r[0]), mean, std, cnt))
        if std_rows:
            std_rows.sort(key=lambda x: -x[0])
            top = '、'.join(
                f"{n} {f'{m:g}' if m is not None else '—'}/{s:g} MB"
                + (f'（{c} 次）' if c else '')
                for _rt, n, m, s, c in std_rows[:3])
            if len(std_rows) > 3:
                top += '、…'
            parts.append(f'常驻进程异常 {len(std_rows)} 项（有标准）：{top}')
        if nostd:
            parts.append(f'无标准的异常记录 {nostd} 条')
        if not std_rows and not nostd:
            parts.append(f'常驻进程异常记录 {len(rs_rows)} 条')
    return parts


# ---------- 数据集/设备信息 ----------
def _events_json(ds: Dataset):
    return [{
        't': e.t.strftime('%Y-%m-%d %H:%M:%S'),
        'kind': e.kind, 'proc': e.proc, 'reason': e.reason,
        'comp': e.comp, 'caller': e.caller, 'adj': e.adj, 'phase': e.phase,
    } for e in ds.events]


def _dataset_json(ds: Dataset, hours, interval_min, hours_total,
                  opts: Options, gaps, valid_hours, ev_hours):
    starts = [r.start for r in ds.rounds if r.start]
    reasons = {}
    for e in ds.events:
        if e.kind == 'start' and e.phase == 'test':
            k = e.reason or '(无)'
            reasons[k] = reasons.get(k, 0) + 1
    pk = 'static_result' if opts.kind == 'static' else 'dynamic_result'
    fsum = ds.platform.get('fail_summary') or {}
    basis = platform_basis(fsum)
    platform = {
        'static_result': ds.platform.get('static_result') or '—',
        'dynamic_result': ds.platform.get('dynamic_result') or '—',
        'tool_version': ds.platform.get('tool_version') or '—',
        'std_version': ds.platform.get('std_version') or '—',
        'std_tier': ds.platform.get('std_tier') or '—',
        'valid_count': ds.platform.get('valid_count'),
        'result': ds.platform.get(pk) or '—',
        'fail_summary': fsum,
        'basis': basis,
        'basis_note': ('' if basis else
                       '超标 Summary 区块均无记录' if fsum
                       else '报告未提供超标明细'),
    }
    return {
        'label': ds.label,
        'device': ds.device,
        'packages': ds.packages,
        'n': ds.n,
        'rounds': [r.index for r in ds.rounds],
        'start_times': [r.start.strftime('%m-%d %H:%M:%S') if r.start else None
                        for r in ds.rounds],
        'hours': [round(h, 4) for h in hours],
        'hours_total': hours_total,
        'span': (starts[0].strftime('%Y-%m-%d %H:%M:%S') + ' ~ '
                 + starts[-1].strftime('%Y-%m-%d %H:%M:%S')) if starts else '—',
        'interval_min': interval_min,
        'boot_time': (ds.boot_time.strftime('%Y-%m-%d %H:%M:%S')
                      if ds.boot_time else None),
        'failed_rounds': ds.failed_rounds,
        'reboots': ds.reboots,
        'model': ds.model,
        'chip': ds.chip,
        'detect_src': ds.detect_src,
        'round_dirs': ds.round_dirs,
        'valid_hours': round(valid_hours, 4),
        'ev_hours': round(ev_hours, 4),
        'gaps': gaps,
        'lmk_total': ds.lmk_total,
        'clock_offset_s': ds.clock_offset_s,
        'event_source': ds.event_source,
        'platform': platform,
        'warnings': ds.warnings,
        'reason_counts': reasons,
        'events_total': len(ds.events),
        'events': (_events_json(ds)
                   if len(ds.events) <= EVENTS_INLINE_MAX else None),
    }


def _fmt_h(h):
    return f'{h:.1f}' if h is not None else '—'


_CHIP_CN = {'mtk': 'MTK', 'unisoc': '展锐', 'qcom': '高通'}
_ES_CN = {'mtk_mobilelog': 'MTK mobilelog',
          'mtk_debuglogger': 'MTK debuglogger',
          'unisoc_ylog': '展锐 ylog',
          'bugreport': 'bugreport(仅末段)'}


def _model_txt(ds):
    t = ds.model or '未知'
    if (ds.detect_src or {}).get('model') == 'cli':
        t += '（命令行指定）'
    return t


def _chip_txt(ds):
    plat = ds.device.get('Platform') or '—'
    cn = _CHIP_CN.get(ds.chip)
    t = f'{cn} ({plat})' if cn else f'未知 ({plat})'
    if (ds.detect_src or {}).get('chip') == 'cli':
        t += '（命令行指定）'
    return t


def _gap_txt(gaps):
    if not gaps:
        return '无'
    return '；'.join(
        f"第{g['after_round']}–{g['round']}轮之间 {g['hours']:.1f}h"
        for g in gaps)


def _ev_src_txt(ds):
    es = ds.event_source or {}
    k = es.get('kind')
    if not k:
        return '无'
    return f"{_ES_CN.get(k, k)} {es.get('files', 0)} 文件 / {es.get('events', 0)} 条"


def _failed_ranges_txt(ds):
    """'772–1433(原因)' 式压缩描述；最多 10 段。"""
    if not ds.failed_rounds:
        return '无'
    reason_of = {d['index']: d['reason'] for d in ds.failed_rounds}
    out = []
    for i in sorted(reason_of):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    parts = []
    for a, b in out[:10]:
        rs = []
        for i in range(a, b + 1):
            if reason_of[i] not in rs:
                rs.append(reason_of[i])
        span = str(a) if a == b else f'{a}–{b}'
        parts.append(f"{span}({'|'.join(rs)})")
    return ', '.join(parts) + (' 等' if len(out) > 10 else '')


# ---------- findings ----------
def _findings(dsa, dsb, mp, system, std_a, std_b, ea, eb, pressure, opts,
              leaks, da, db):
    out = []
    pm = opts.primary_metric.upper()

    def add(sev, cat, title, detail, link, score=0.0, cached=False):
        out.append({'sev': sev, 'cat': cat, 'title': title,
                    'detail': detail, 'link': link, 'score': score,
                    'cached': cached})

    # 0. 数据质量（模型/芯片/轮次覆盖/事件来源）
    for side, ds, dj in (('A', dsa, da), ('B', dsb, db)):
        rd_ = ds.round_dirs or 0
        if rd_ and ds.n / rd_ < 0.8:
            add('mid', '数据',
                f'{side} 侧有效轮仅 {ds.n}/{rd_}（{ds.n / rd_:.0%}），'
                '样本可信度降低',
                '无效轮：' + _failed_ranges_txt(ds), '#tab=overview')
        gs = [g for g in dj['gaps'] if g['hours'] >= 1]
        if gs:
            add('mid', '数据',
                f'{side} 侧数据中断 {len(gs)} 段，'
                f'共 {sum(g["hours"] for g in gs):.1f} 小时',
                _gap_txt(gs), '#tab=overview')
        vc = ds.platform.get('valid_count')
        if vc is not None and vc != ds.n:
            add('low', '数据',
                f'{side} 侧工具有效轮 {ds.n} 与平台有效测试次数 {vc} 不一致',
                '空数据轮判定口径不同（工具：dumpsys/procStatus 为空即无效）',
                '#tab=overview')
        if (ds.event_source or {}).get('kind') == 'bugreport':
            add('low', '数据',
                f'{side} 侧事件仅来自 bugreport（覆盖测试末段）',
                '', '#tab=overview')
    ta_ = any(e.phase == 'test' for e in dsa.events)
    tb_ = any(e.phase == 'test' for e in dsb.events)
    if ta_ != tb_:
        add('mid', '数据',
            f"{'A' if not ta_ else 'B'} 侧无事件日志，"
            'LMK/ANR/crash 对比不可用', '', '#tab=overview')
    if dsa.model != dsb.model:
        add('mid', '数据',
            f'两侧体验模型不同（A {dsa.model} / B {dsb.model}）',
            '标准阈值、层级口径与采集方式不同，对比结论仅供参考',
            '#tab=overview')
    if dsa.chip and dsb.chip and dsa.chip != dsb.chip:
        add('mid', '数据',
            f"两侧芯片平台不同（A {_CHIP_CN.get(dsa.chip, dsa.chip)} / "
            f"B {_CHIP_CN.get(dsb.chip, dsb.chip)}）",
            'Native 进程构成差异大，进程级对比仅供参考', '#tab=overview')

    # 1. 标准
    fa = set((std_a or {}).get('fail_items', []))
    fb = set((std_b or {}).get('fail_items', []))
    if std_a or std_b:
        for name in sorted(fa | fb):
            if name in fb and name not in fa:
                add('high', '标准', f'「{name}」回归超标',
                    'A 达标而 B 超标（RSS 口径均值 vs 阈值）', '#tab=risk')
            elif name in fa and name not in fb:
                add('info', '标准', f'「{name}」已改善达标',
                    'A 超标而 B 达标', '#tab=risk')
            else:
                add('mid', '标准', f'「{name}」两侧均超标', 'A、B 均超标',
                    '#tab=risk')

    # 2. 层级 / 整机显著变化（主口径）
    for r in mp['level_rows']:
        if not r['sig']:
            continue
        worse = r['gap'] > 0
        add('mid' if worse else 'info', '层级',
            f"层级 {r['level']} {pm} {'上升' if worse else '下降'}",
            f"轮均 {r['da']['mi']:.2f} → {r['db']['mi']:.2f} MB"
            f"（{r['gap']:+.2f} MB）", '#tab=levels', abs(r['gap']),
            r['level'] == 'Cached')
    for r in system:
        g = gap_info(r['da']['mean'] or 0.0, r['db']['mean'] or 0.0)
        if not is_significant(r['da']['mean'] or 0.0,
                              r['db']['mean'] or 0.0, opts):
            continue
        worse = (g['gap'] < 0) if r['hb'] else (g['gap'] > 0)
        add('mid' if worse else 'info', '整机',
            f"{r['name']} {'上升' if g['gap'] > 0 else '下降'}",
            f"{(r['da']['mean'] or 0):.2f} → {(r['db']['mean'] or 0):.2f} MB"
            f"（{g['gap']:+.2f} MB）", '#tab=system', abs(g['gap']))

    # 3. 泄漏：按 (kind,name) 合并 RSS/PSS；两侧都有 → mid，仅 B → high，仅 A → low
    groups = {}
    for lk in leaks:
        groups.setdefault((lk['kind'], lk['name']), {'A': [], 'B': []})[
            lk['ds']].append(lk)
    kind_cn = {'proc': '进程', 'level': '层级', 'sys': '整机'}
    for (kind, name), g in groups.items():
        nm = name
        link = (f'#proc={nm}&metric={g["A"][0]["metric"] if g["A"] else g["B"][0]["metric"]}' if kind == 'proc'
                else '#tab=levels' if kind == 'level' else '#tab=system')
        parts = []
        for dsk in ('A', 'B'):
            for lk in g[dsk]:
                parts.append(
                    f"{dsk}/{lk['metric'].upper() or '整机'} "
                    f"delta {lk['delta']:+.1f} MB"
                    f"（{lk['head']:.1f}→{lk['tail']:.1f}），"
                    f"slope {lk['slope']:+.2f} MB/h，R²={lk['r2']:.2f}")
        detail = '；'.join(parts)
        score = max(abs(l['delta']) for e in g.values() for l in e)
        lcached = ((kind == 'level' and name == 'Cached')
                   or (kind == 'proc'
                       and mp['procs'].get(name, {}).get('cached_only')))
        if g['B'] and g['A']:
            add('mid', '泄漏', f'两侧均疑似泄漏：{nm}（{kind_cn[kind]}）',
                detail, link, score, lcached)
        elif g['B']:
            add('high', '泄漏', f'B 侧疑似泄漏：{nm}（{kind_cn[kind]}）',
                detail, link, score, lcached)
        else:
            add('low', '泄漏', f'A 侧疑似泄漏：{nm}（{kind_cn[kind]}，仅 A）',
                detail + '；仅出现在基线侧', link, score, lcached)

    # 4. 主口径 sig 且 B 侧 resident 的增长 Top10 → mid（剔除测试工具进程）
    cand = [p for p in mp['procs'].values()
            if p['sig'] and p['resident_b'] and p['gap'] > 0
            and not _is_test_tool(p['name'])]
    cand.sort(key=lambda p: -p['gap'])
    for p in cand[:10]:
        add('mid', '进程', f"常驻进程 {p['name']} {pm} 显著上升",
            f"轮均 {p['da']['mi']:.2f} → {p['db']['mi']:.2f} MB"
            f"（{p['gap']:+.2f} MB），B 侧出现率 {p['db']['rate']:.0%}",
            f"#proc={p['name']}&metric={opts.primary_metric}",
            abs(p['gap']), p['cached_only'])

    # 5. 新增 / 消失进程（轮均 ≥ gap_abs，各取 Top3 展示）
    news = sorted((p for p in mp['procs'].values()
                   if p['da']['k'] == 0 and p['db']['mi'] >= opts.gap_abs),
                  key=lambda p: -p['db']['mi'])
    if news:
        add('mid', '进程', f'新增进程 {len(news)} 个',
            '；'.join(f"{p['name']} {p['db']['mi']:.2f} MB"
                      for p in news[:3]) + (' 等' if len(news) > 3 else ''),
            '#tab=procs', news[0]['db']['mi'],
            all(p['cached_only'] for p in news))
    gone = sorted((p for p in mp['procs'].values()
                   if p['db']['k'] == 0 and p['da']['mi'] >= opts.gap_abs),
                  key=lambda p: -p['da']['mi'])
    if gone:
        add('info', '进程', f'消失进程 {len(gone)} 个',
            '；'.join(f"{p['name']} {p['da']['mi']:.2f} MB"
                      for p in gone[:3]) + (' 等' if len(gone) > 3 else ''),
            '#tab=procs', gone[0]['da']['mi'],
            all(p['cached_only'] for p in gone))

    # 6. 归类：自研 / Google 轮均合计变化
    for key, cn, tab in (('inhouse', '自研', 'inhouse'),
                         ('google', 'Google', 'google')):
        sa_ = sum(p['da']['mi'] for p in mp['procs'].values() if p[key])
        sb_ = sum(p['db']['mi'] for p in mp['procs'].values() if p[key])
        if is_significant(sa_, sb_, opts):
            up = sb_ > sa_
            add('mid' if up else 'info', '归类',
                f"{cn}应用轮均合计{'上升' if up else '下降'}（{pm}）",
                f"{sa_:.2f} → {sb_:.2f} MB（{sb_ - sa_:+.2f} MB）",
                f'#tab={tab}', abs(sb_ - sa_))

    # 7. LMK 频率
    la, lb = ea['lmk'], eb['lmk']
    if la['per_hour'] is not None and lb['per_hour'] is not None:
        diff = lb['per_hour'] - la['per_hour']
        if (la['per_hour'] > 0
                and diff >= la['per_hour'] * opts.rate_pct / 100.0
                and diff >= 1.0):
            add('high', 'LMK', 'B 侧 LMK 查杀频率显著升高',
                f"{la['per_hour']:.2f} → {lb['per_hour']:.2f} 次/小时"
                f"（{diff:+.2f}），A {la['total']} 次 / B {lb['total']} 次",
                '#tab=events', abs(diff))
        elif (la['per_hour'] > 0
              and diff <= -la['per_hour'] * opts.rate_pct / 100.0):
            add('info', 'LMK', 'B 侧 LMK 查杀频率下降',
                f"{la['per_hour']:.2f} → {lb['per_hour']:.2f} 次/小时",
                '#tab=events', abs(diff))

    # 8. 压力计数器
    for c in pressure.get('counters', []):
        if c['name'] not in ('pswpout', 'allocstall', 'pgmajfault',
                             'workingset_refault_file'):
            continue
        sa, sb = c.get('sa'), c.get('sb')
        if not sa or not sb or sa['per_hour'] in (None, 0):
            continue
        if (sb['per_hour'] is not None
                and sb['per_hour'] - sa['per_hour']
                >= sa['per_hour'] * opts.rate_pct / 100.0):
            add('mid', '压力', f"内存压力指标 {c['name']} 升高",
                f"{sa['per_hour']:.2f} → {sb['per_hour']:.2f} {c['unit']}/小时"
                f"（{(c['gapp'] or 0):+.1f}%）", '#tab=pressure',
                abs(c['gapp'] or 0))

    # 9. ANR / crash / 重启
    def _procs(lst):
        seen = {}
        for e in lst:
            seen[e['proc']] = seen.get(e['proc'], 0) + 1
        return '、'.join(sorted(seen, key=lambda x: -seen[x])[:5]) or '—'

    if len(eb['anr']) > len(ea['anr']):
        add('mid', '稳定性', f"ANR 增多（A {len(ea['anr'])} / B {len(eb['anr'])}）",
            f"B 侧涉及进程：{_procs(eb['anr'])}", '#tab=events')
    if len(eb['crash']) > len(ea['crash']):
        add('mid', '稳定性',
            f"crash 增多（A {len(ea['crash'])} / B {len(eb['crash'])}）",
            f"B 侧涉及进程：{_procs(eb['crash'])}", '#tab=events')
    if len(dsb.reboots) > len(dsa.reboots):
        add('mid', '稳定性',
            f"重启次数增多（A {len(dsa.reboots)} / B {len(dsb.reboots)}）",
            'B 侧重启点：'
            + '、'.join(f"第{r['round']}轮前" for r in dsb.reboots),
            '#tab=events')
    return out


# ---------- 顶层 ----------
def analyze(dsa: Dataset, dsb: Dataset, opts: Options) -> dict:
    ha, iva, hta, ga, vha = _hours(dsa)
    hb, ivb, htb, gb, vhb = _hours(dsb)

    mrss = build_metric(dsa, dsb, 'rss', opts, ha, hb)
    mpss = build_metric(dsa, dsb, 'pss', opts, ha, hb)
    system = build_system(dsa, dsb, opts, ha, hb)
    pressure = build_pressure(dsa, dsb, ha, hb)
    ea = build_events(dsa, hta, gaps=ga, iv_s=(iva or 0) * 60)
    eb = build_events(dsb, htb, gaps=gb, iv_s=(ivb or 0) * 60)
    std_a = build_standards(dsa, mrss, 'a', opts)
    std_b = build_standards(dsb, mrss, 'b', opts)

    dsj = [_dataset_json(dsa, ha, iva, hta, opts, ga, vha, ea['ev_hours']),
           _dataset_json(dsb, hb, ivb, htb, opts, gb, vhb, eb['ev_hours'])]

    pa, pb = set(dsa.packages), set(dsb.packages)
    pkg_diff = {'a': sorted(pa - pb), 'b': sorted(pb - pa)}

    model_a, model_b = _model_txt(dsa), _model_txt(dsb)
    chip_a, chip_b = _chip_txt(dsa), _chip_txt(dsb)
    fields = list(dict.fromkeys(list(dsa.device) + list(dsb.device)))
    device_diff = [
        {'field': '体验模型', 'a': model_a, 'b': model_b,
         'diff': model_a != model_b},
        {'field': '芯片平台', 'a': chip_a, 'b': chip_b,
         'diff': chip_a != chip_b},
    ]
    device_diff += [{'field': f, 'a': dsa.device.get(f, ''),
                    'b': dsb.device.get(f, ''),
                    'diff': dsa.device.get(f, '') != dsb.device.get(f, '')}
                   for f in fields]
    plat_a, plat_b = dsj[0]['platform']['result'], dsj[1]['platform']['result']
    vc_a = dsj[0]['platform']['valid_count']
    vc_b = dsj[1]['platform']['valid_count']
    gap_a, gap_b = _gap_txt(ga), _gap_txt(gb)
    eva, evb = _ev_src_txt(dsa), _ev_src_txt(dsb)
    device_diff += [
        {'field': '有效轮次', 'a': str(dsa.n), 'b': str(dsb.n),
         'diff': dsa.n != dsb.n},
        {'field': '轮次目录数', 'a': str(dsa.round_dirs),
         'b': str(dsb.round_dirs),
         'diff': dsa.round_dirs != dsb.round_dirs},
        {'field': '平台有效轮次',
         'a': str(vc_a) if vc_a is not None else '—',
         'b': str(vc_b) if vc_b is not None else '—',
         'diff': vc_a != vc_b},
        {'field': '失败轮次数', 'a': str(len(dsa.failed_rounds)),
         'b': str(len(dsb.failed_rounds)),
         'diff': len(dsa.failed_rounds) != len(dsb.failed_rounds)},
        {'field': '测试时间范围', 'a': dsj[0]['span'], 'b': dsj[1]['span'],
         'diff': True},
        {'field': '测试时长(小时)', 'a': _fmt_h(hta), 'b': _fmt_h(htb),
         'diff': False},
        {'field': '有效时长(小时)', 'a': _fmt_h(vha), 'b': _fmt_h(vhb),
         'diff': False},
        {'field': '中断区间', 'a': gap_a, 'b': gap_b,
         'diff': gap_a != gap_b},
        {'field': '事件来源', 'a': eva, 'b': evb, 'diff': eva != evb},
        {'field': 'LMK 累计(lmkcnt)',
         'a': str(dsa.lmk_total) if dsa.lmk_total is not None else '—',
         'b': str(dsb.lmk_total) if dsb.lmk_total is not None else '—',
         'diff': dsa.lmk_total != dsb.lmk_total},
        {'field': '采样间隔(分钟，中位数)',
         'a': f'{iva:.1f}' if iva else '—',
         'b': f'{ivb:.1f}' if ivb else '—', 'diff': False},
        {'field': '平台判定', 'a': plat_a, 'b': plat_b,
         'diff': plat_a != plat_b},
        {'field': '重启次数', 'a': str(len(dsa.reboots)),
         'b': str(len(dsb.reboots)),
         'diff': len(dsa.reboots) != len(dsb.reboots)},
    ]

    ih = {
        'whitelist_src': opts.whitelist_src,
        'whitelist_count': len(opts.whitelist or set()),
        'prefixes': list(opts.prefixes),
        'prefixes_src': opts.prefixes_src,
    }

    # 泄漏汇总
    leaks = []
    for metric, m in (('rss', mrss), ('pss', mpss)):
        for r in m['level_rows']:
            for side, t in (('A', r['ta']), ('B', r['tb'])):
                flag = r['leak_a'] if side == 'A' else r['leak_b']
                if flag and t:
                    leaks.append({'ds': side, 'kind': 'level', 'metric': metric,
                                  'name': r['level'], 'slope': t['slope'],
                                  'r2': t['r2'], 'head': t['head'],
                                  'tail': t['tail'], 'delta': t['delta']})
        for p in m['procs'].values():
            for side, t, flag in (('A', p['ta'], p['leak_a']),
                                  ('B', p['tb'], p['leak_b'])):
                if flag and t:
                    leaks.append({'ds': side, 'kind': 'proc', 'metric': metric,
                                  'name': p['name'], 'slope': t['slope'],
                                  'r2': t['r2'], 'head': t['head'],
                                  'tail': t['tail'], 'delta': t['delta']})
    for r in system:
        for side, t, flag in (('A', r['ta'], r['leak_a']),
                              ('B', r['tb'], r['leak_b'])):
            if flag and t:
                leaks.append({'ds': side, 'kind': 'sys', 'metric': '',
                              'name': r['name'], 'slope': t['slope'],
                              'r2': t['r2'], 'head': t['head'],
                              'tail': t['tail'], 'delta': t['delta']})

    mp = mrss if opts.primary_metric == 'rss' else mpss
    findings = _findings(dsa, dsb, mp, system, std_a, std_b, ea, eb,
                         pressure, opts, leaks, dsj[0], dsj[1])
    findings.sort(key=lambda f: (_SEV_ORDER.get(f['sev'], 9),
                                 _CAT_ORDER.get(f['cat'], 9),
                                 -f['score']))

    return {
        'kind': opts.kind,
        'labels': [dsa.label, dsb.label],
        'meta': {
            'tool': f'memscope {__version__}',
            'kind': opts.kind,
            'primary_metric': opts.primary_metric,
            'axis': 'round' if max(dsa.n, dsb.n) <= SMALL_N else 'hour',
            'small_n': SMALL_N,
            'gap_abs': opts.gap_abs, 'gap_pct': opts.gap_pct,
            'resident': opts.resident, 'leak_delta': opts.leak_delta,
            'leak_pct': opts.leak_pct, 'leak_r2': opts.leak_r2,
            'sys_leak_delta': opts.sys_leak_delta, 'rate_pct': opts.rate_pct,
            'cross_model': dsa.model != dsb.model,
            'cross_chip': bool(dsa.chip and dsb.chip
                               and dsa.chip != dsb.chip),
        },
        'datasets': dsj,
        'device_diff': device_diff,
        'pkg_diff': pkg_diff,
        'metrics': {'rss': mrss, 'pss': mpss},
        'system': system,
        'pressure': pressure,
        'events': {'a': ea, 'b': eb},
        'standards': {'a': std_a, 'b': std_b},
        'inhouse': ih,
        'google': {'prefixes': list(opts.google_prefixes),
                   'prefixes_src': opts.google_prefixes_src},
        'leaks': leaks,
        'findings': findings,
    }
