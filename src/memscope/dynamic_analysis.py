"""动态内存对比分析：两个 Dataset（kind='dynamic'）→ JSON 可序列化 result dict。

与静态 analysis.py 共用口径基础（rmean/gap/is_significant/层级映射/自研判定），
新增：小时时间轴、分布统计（P50/P90/P95）、趋势回归、vmstat 内存压力、
LMK/ANR/crash 事件统计、平台健康标准对照与自动结论（findings）。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from math import ceil

from .analysis import (HIGHER_BETTER, LEVEL_ORDER, SYSTEM_FIELDS, cnt,
                       gap_info, is_significant, rmean, _dataset_metric,
                       _level_order)
from .inhouse import classify, is_google
from .model import Dataset
from .parsers.procstatus import oom_to_level

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
_CAT_ORDER = {'标准': 0, 'LMK': 1, '泄漏': 2, '稳定性': 3, '压力': 4,
              '层级': 5, '整机': 6, '进程': 7}
# 只有常驻层级做泄漏判定；其余层级成员随负载变化，趋势不代表泄漏
LEAK_LEVELS = {'Native', 'System', 'Persistent', 'Persistent Service'}
# 测试工具进程：不算泄漏、不进进程类 findings（数据仍保留）
TEST_TOOL_PREFIXES = ('com.android.commands.monkey',)


def _is_test_tool(name: str) -> bool:
    return name.startswith(TEST_TOOL_PREFIXES)


@dataclass
class DynOptions:
    gap_abs: float = 5.0
    gap_pct: float = 10.0
    resident: float = 0.9       # 常驻阈值：出现率 >= resident
    leak_delta: float = 10.0    # 进程泄漏 delta 门限 MB
    leak_pct: float = 20.0      # 进程泄漏 delta 相对门限 %
    leak_r2: float = 0.3        # 泄漏判定 R2 门限
    sys_leak_delta: float = 100.0  # 层级/整机泄漏 delta 门限 MB
    rate_pct: float = 20.0      # 频率对比阈值 %（LMK/压力）
    whitelist: set = None
    whitelist_src: str = None
    prefixes: tuple = ()
    prefixes_src: str = None
    google_prefixes: tuple = ()
    google_prefixes_src: str = None


# ---------- 统计 ----------
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


def trend(hours, series):
    """在出现轮上对 hours 线性回归：slope MB/小时 + head/tail（前/后 10% 中位数）。"""
    pts = [(h, v) for h, v in zip(hours, series) if v is not None]
    if len(pts) < 10:
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
    """每轮距测试开始的小时数；start 缺失时按 序号×平均间隔 回退。"""
    starts = [r.start for r in ds.rounds if r.start]
    iv = None
    if len(starts) > 1:
        d = sum((starts[i] - starts[i - 1]).total_seconds()
                for i in range(1, len(starts))) / (len(starts) - 1)
        iv = d  # 秒
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
    return hours, (iv / 60.0 if iv else None), total


# ---------- 单指标（rss/pss）聚合 ----------
def _series2(v):
    return [round(x, 2) if x is not None else None for x in v]


def _leak_flag(t, resident_ok: bool, delta_thr: float, opts: DynOptions) -> bool:
    if not resident_ok or t is None:
        return False
    return (t['slope'] > 0 and t['r2'] >= opts.leak_r2
            and t['delta'] >= max(delta_thr, t['head'] * opts.leak_pct / 100.0))


def build_metric(dsa: Dataset, dsb: Dataset, kind: str, opts: DynOptions,
                 ha, hb):
    na, nb = dsa.n, dsb.n
    lv_a, pl_a = _dataset_metric(dsa, kind)
    lv_b, pl_b = _dataset_metric(dsb, kind)
    levels = _level_order(set(lv_a) | set(lv_b))

    level_rows = []
    for lv in levels:
        sa = lv_a.get(lv, [None] * na)
        sb = lv_b.get(lv, [None] * nb)
        da, db = dist(sa), dist(sb)
        ta, tb = trend(ha, sa), trend(hb, sb)
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
        lvc_a = {}
        lvc_b = {}
        for lv, series in (sa or {}).items():
            k = cnt(series)
            if k:
                lvc_a[lv] = k
            for r, v in enumerate(series):
                if v is not None:
                    ta_[r] = (ta_[r] or 0.0) + v
        for lv, series in (sb or {}).items():
            k = cnt(series)
            if k:
                lvc_b[lv] = k
            for r, v in enumerate(series):
                if v is not None:
                    tb_[r] = (tb_[r] or 0.0) + v
        da = dist(ta_)
        db = dist(tb_)
        tda = trend(ha, ta_)
        tdb = trend(hb, tb_)
        g = gap_info(da['mi'], db['mi'])
        resident_a = da['rate'] >= opts.resident
        resident_b = db['rate'] >= opts.resident
        present_levels = set(lvc_a) | set(lvc_b)
        cached_only = (da['k'] + db['k']) > 0 and present_levels == {'Cached'}
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
            'cached_only': cached_only,
            'inhouse': ih is not None, 'ih_src': ih,
            'google': ih is None and is_google(name, opts.google_prefixes),
        }

    only_a = sorted(n for n, p in procs.items() if p['da']['k'] > 0
                    and p['db']['k'] == 0)
    only_b = sorted(n for n, p in procs.items() if p['db']['k'] > 0
                    and p['da']['k'] == 0)
    top = sorted(procs.values(), key=lambda p: -abs(p['gap']))
    return {
        'levels': levels,
        'level_rows': level_rows,
        'procs': procs,
        'only_a': only_a,
        'only_b': only_b,
        'top': [p['name'] for p in top],
    }


# ---------- 整机 ----------
def _sys_series(ds: Dataset, field, src):
    if src == 'tail':
        return [rd.tail.get(field) for rd in ds.rounds]
    return [rd.meminfo.get(field) for rd in ds.rounds]


def build_system(dsa: Dataset, dsb: Dataset, opts: DynOptions, ha, hb):
    rows = []
    for field, src in SYSTEM_FIELDS:
        a = _sys_series(dsa, field, src)
        b = _sys_series(dsb, field, src)
        da, db = dist(a), dist(b)
        ta, tb = trend(ha, a), trend(hb, b)
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


def _sys_leak(field, t, opts: DynOptions) -> bool:
    """Used RAM 上升 / Free RAM、MemAvailable 下降；|delta|>=sys_leak_delta 且 r2 达标。"""
    if field not in dict(_SYS_LEAK_FIELDS) or t is None:
        return False
    want = dict(_SYS_LEAK_FIELDS)[field]
    d = t['delta'] if want == 'up' else -t['delta']
    return d >= opts.sys_leak_delta and t['r2'] >= opts.leak_r2


# ---------- 内存压力（proc_vmstat 增量） ----------
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


def build_events(ds: Dataset, hours_total: float):
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
        'per_hour': (len(lmk_evs) / hours_total) if hours_total else None,
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
    }


# ---------- 健康标准对照（RSS 口径，用各侧自己的阈值） ----------
def build_standards(ds: Dataset, mrss, side: str, opts: DynOptions):
    th = ds.thresholds
    if not th.available:
        return None
    n = ds.n
    rows = {r['level']: r for r in mrss['level_rows']}
    res = {'levels': [], 'free_ram': None, 'procs': []}
    fail = []
    for lv, t in th.levels.items():
        r = rows.get(lv)
        if not r:
            continue
        s = r[side]
        d = r['d' + side]
        if d['mean'] is None:
            continue
        viol = d['mean'] > t
        res['levels'].append({
            'name': lv, 'thr': t, 'mean': d['mean'], 'p90': d['p90'],
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


# ---------- 数据集/设备信息 ----------
def _dataset_json(ds: Dataset, hours, interval_min, hours_total):
    starts = [r.start for r in ds.rounds if r.start]
    return {
        'label': ds.label,
        'device': ds.device,
        'packages': ds.packages,
        'n': ds.n,
        'failed_rounds': ds.failed_rounds,
        'reboots': ds.reboots,
        'platform': ds.platform,
        'rounds': [r.index for r in ds.rounds],
        'start_times': [r.start.strftime('%m-%d %H:%M:%S') if r.start else None
                        for r in ds.rounds],
        'span': (starts[0].strftime('%Y-%m-%d %H:%M:%S') + ' ~ '
                 + starts[-1].strftime('%Y-%m-%d %H:%M:%S')) if starts else '—',
        'interval_min': interval_min,
        'hours_total': hours_total,
        'boot_time': (ds.boot_time.strftime('%Y-%m-%d %H:%M:%S')
                      if ds.boot_time else None),
        'warnings': ds.warnings,
        'hours': [round(h, 4) for h in hours],
    }


def _fmt_h(h):
    return f'{h:.1f}' if h is not None else '—'


# ---------- findings ----------
def _findings(dsa, dsb, mrss, system, std_a, std_b, ea, eb, pressure, opts,
              leaks):
    out = []

    def add(sev, cat, title, detail, link, score=0.0, cached=False):
        out.append({'sev': sev, 'cat': cat, 'title': title,
                    'detail': detail, 'link': link, 'score': score,
                    'cached': cached})

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

    # 2. 层级 / 整机显著变化
    for r in mrss['level_rows']:
        if not r['sig']:
            continue
        worse = r['gap'] > 0
        add('mid' if worse else 'info', '层级',
            f"层级 {r['level']} RSS {'上升' if worse else '下降'}",
            f"mi {r['da']['mi']:.2f} → {r['db']['mi']:.2f} MB"
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
        link = (f'#proc={nm}&metric=rss' if kind == 'proc'
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
                       and mrss['procs'].get(name, {}).get('cached_only')))
        if g['B'] and g['A']:
            add('mid', '泄漏', f'两侧均疑似泄漏：{nm}（{kind_cn[kind]}）',
                detail, link, score, lcached)
        elif g['B']:
            add('high', '泄漏', f'B 侧疑似泄漏：{nm}（{kind_cn[kind]}）',
                detail, link, score, lcached)
        else:
            add('low', '泄漏', f'A 侧疑似泄漏：{nm}（{kind_cn[kind]}，仅 A）',
                detail + '；仅出现在基线侧', link, score, lcached)

    # 4. RSS 口径 sig 且 B 侧 resident 的增长 Top10 → mid（剔除测试工具进程）
    cand = [p for p in mrss['procs'].values()
            if p['sig'] and p['resident_b'] and p['gap'] > 0
            and not _is_test_tool(p['name'])]
    cand.sort(key=lambda p: -p['gap'])
    for p in cand[:10]:
        add('mid', '进程', f"常驻进程 {p['name']} RSS 显著上升",
            f"mi {p['da']['mi']:.2f} → {p['db']['mi']:.2f} MB"
            f"（{p['gap']:+.2f} MB），B 侧出现率 {p['db']['rate']:.0%}",
            f"#proc={p['name']}&metric=rss", abs(p['gap']), p['cached_only'])

    # 5. LMK 频率
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

    # 6. 压力计数器
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

    # 7. ANR / crash / 重启
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
def analyze(dsa: Dataset, dsb: Dataset, opts: DynOptions) -> dict:
    ha, iva, hta = _hours(dsa)
    hb, ivb, htb = _hours(dsb)

    mrss = build_metric(dsa, dsb, 'rss', opts, ha, hb)
    mpss = build_metric(dsa, dsb, 'pss', opts, ha, hb)
    system = build_system(dsa, dsb, opts, ha, hb)
    pressure = build_pressure(dsa, dsb, ha, hb)
    ea = build_events(dsa, hta)
    eb = build_events(dsb, htb)
    std_a = build_standards(dsa, mrss, 'a', opts)
    std_b = build_standards(dsb, mrss, 'b', opts)

    dsj = [_dataset_json(dsa, ha, iva, hta),
           _dataset_json(dsb, hb, ivb, htb)]

    pa, pb = set(dsa.packages), set(dsb.packages)
    pkg_diff = {'a': sorted(pa - pb), 'b': sorted(pb - pa)}

    fields = list(dict.fromkeys(list(dsa.device) + list(dsb.device)))
    device_diff = [{'field': f, 'a': dsa.device.get(f, ''),
                    'b': dsb.device.get(f, ''),
                    'diff': dsa.device.get(f, '') != dsb.device.get(f, '')}
                   for f in fields]
    device_diff += [
        {'field': '有效轮次', 'a': str(dsa.n), 'b': str(dsb.n),
         'diff': dsa.n != dsb.n},
        {'field': '失败轮次数', 'a': str(len(dsa.failed_rounds)),
         'b': str(len(dsb.failed_rounds)),
         'diff': len(dsa.failed_rounds) != len(dsb.failed_rounds)},
        {'field': '测试时长(小时)', 'a': _fmt_h(hta), 'b': _fmt_h(htb),
         'diff': False},
        {'field': '平均采样间隔(分钟)',
         'a': f'{iva:.1f}' if iva else '—',
         'b': f'{ivb:.1f}' if ivb else '—', 'diff': False},
        {'field': '平台判定',
         'a': dsa.platform.get('dynamic_result', '—'),
         'b': dsb.platform.get('dynamic_result', '—'),
         'diff': dsa.platform.get('dynamic_result')
         != dsb.platform.get('dynamic_result')},
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

    findings = _findings(dsa, dsb, mrss, system, std_a, std_b, ea, eb,
                         pressure, opts, leaks)
    findings.sort(key=lambda f: (_SEV_ORDER.get(f['sev'], 9),
                                 _CAT_ORDER.get(f['cat'], 9),
                                 -f['score']))

    return {
        'kind': 'dynamic',
        'labels': [dsa.label, dsb.label],
        'meta': {
            'tool': 'memscope 0.2.0',
            'gap_abs': opts.gap_abs, 'gap_pct': opts.gap_pct,
            'resident': opts.resident, 'leak_delta': opts.leak_delta,
            'leak_pct': opts.leak_pct, 'leak_r2': opts.leak_r2,
            'sys_leak_delta': opts.sys_leak_delta, 'rate_pct': opts.rate_pct,
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
