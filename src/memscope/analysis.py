"""统计分析：两个 Dataset → JSON 可序列化 result dict（所有渲染器共用同一份数据）。"""
from __future__ import annotations

from dataclasses import dataclass

from .inhouse import base_pkg, classify
from .model import Dataset

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


@dataclass
class Options:
    gap_abs: float = 5.0
    gap_pct: float = 10.0
    growth_slope: float = 1.0
    sys_growth_slope: float = 5.0
    growth_r2: float = 0.6
    whitelist: set = None
    whitelist_src: str = None
    prefixes: tuple = ()
    prefixes_src: str = None


# ---------- 基础统计 ----------
def cnt(v):
    return sum(1 for x in v if x is not None)


def rmean(v, n):
    """轮次均值：缺失轮按 0 计入。"""
    return sum(x or 0.0 for x in v) / n if n else 0.0


def vmean(v):
    """数值均值：仅在出现轮次上取均值；无出现返回 None。"""
    a = [x for x in v if x is not None]
    return sum(a) / len(a) if a else None


def vmin(v):
    a = [x for x in v if x is not None]
    return min(a) if a else None


def vmax(v):
    a = [x for x in v if x is not None]
    return max(a) if a else None


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


def linreg(ys):
    """对 round 索引做线性回归，返回 (slope, r2)。"""
    n = len(ys)
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    inter = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (inter + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2


# ---------- 单指标（pss / rss）数据集内聚合 ----------
def _round_levels(rd, kind):
    """某轮 → {level: (level_total_mb, {proc: mb})}"""
    if kind == 'pss':
        return {lv: (d['total'], d['procs']) for lv, d in rd.pss.items()}
    return {lv: (sum(d.values()), d) for lv, d in rd.rss.items()}


def _dataset_metric(ds: Dataset, kind):
    """单数据集聚合：level 序列、proc→{level:series}、proc 每轮层级。"""
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


def build_metric(dsa: Dataset, dsb: Dataset, kind: str, opts: Options):
    na, nb = dsa.n, dsb.n
    lv_a, pl_a = _dataset_metric(dsa, kind)
    lv_b, pl_b = _dataset_metric(dsb, kind)
    levels = _level_order(set(lv_a) | set(lv_b))

    level_series = {lv: {'a': lv_a.get(lv, [None] * na),
                         'b': lv_b.get(lv, [None] * nb)} for lv in levels}
    level_rows = []
    for lv in levels:
        sa, sb = level_series[lv]['a'], level_series[lv]['b']
        ra, rb = rmean(sa, na), rmean(sb, nb)
        g = gap_info(ra, rb)
        ca = sum(1 for p, m in pl_a.items() if lv in m and cnt(m[lv]) > 0)
        cb = sum(1 for p, m in pl_b.items() if lv in m and cnt(m[lv]) > 0)
        level_rows.append({'level': lv, 'ra': ra, 'rb': rb, 'gap': g['gap'],
                           'gapp': g['pct'], 'gapt': g['txt'], 'ca': ca, 'cb': cb})

    procs = {}
    for name in sorted(set(pl_a) | set(pl_b)):
        ma, mb_ = pl_a.get(name), pl_b.get(name)
        sa = ma or {}
        sb = mb_ or {}
        # 每轮合计 + 每轮所在层级
        ta = [None] * na
        tb = [None] * nb
        pra = [None] * na
        prb = [None] * nb
        for lv, series in sa.items():
            for r, v in enumerate(series):
                if v is not None:
                    ta[r] = (ta[r] or 0.0) + v
                    pra[r] = lv
        for lv, series in sb.items():
            for r, v in enumerate(series):
                if v is not None:
                    tb[r] = (tb[r] or 0.0) + v
                    prb[r] = lv
        ka, kb = cnt(ta), cnt(tb)
        ra, rb = rmean(ta, na), rmean(tb, nb)
        g = gap_info(ra, rb)
        seta = {lv for lv, s in sa.items() if cnt(s) > 0}
        setb = {lv for lv, s in sb.items() if cnt(s) > 0}
        present_levels = (set(pra) | set(prb)) - {None}
        cached_only = (ka + kb) > 0 and present_levels == {'Cached'}
        level_changed = bool(seta and setb) and (
            seta != setb or len(seta) > 1 or len(setb) > 1)
        ih = classify(name, opts.whitelist or set(), opts.prefixes)
        procs[name] = {
            'name': name,
            'ka': ka, 'kb': kb,
            'ra': ra, 'rb': rb,
            'va': vmean(ta), 'vb': vmean(tb),
            'mina': vmin(ta), 'maxa': vmax(ta),
            'minb': vmin(tb), 'maxb': vmax(tb),
            'gap': g['gap'], 'gapp': g['pct'], 'gapt': g['txt'],
            'sig': is_significant(ra, rb, opts),
            'cached_only': cached_only,
            'level_changed': level_changed,
            'inhouse': ih is not None, 'ih_src': ih,
            'la': sorted(seta), 'lb': sorted(setb),
            'pra': pra, 'prb': prb,
            'sa': sa, 'sb': sb,
            'ta': ta, 'tb': tb,
        }

    only_a = [p['name'] for p in procs.values() if p['ka'] > 0 and p['kb'] == 0]
    only_b = [p['name'] for p in procs.values() if p['kb'] > 0 and p['ka'] == 0]
    changed = sorted(p['name'] for p in procs.values() if p['level_changed'])
    top = sorted(procs.values(), key=lambda p: -abs(p['gap']))
    new_top = sorted(
        (p for p in procs.values() if p['ka'] == 0 and p['rb'] >= opts.gap_abs),
        key=lambda p: -p['rb'])[:3]
    gone_top = sorted(
        (p for p in procs.values() if p['kb'] == 0 and p['ra'] >= opts.gap_abs),
        key=lambda p: -p['ra'])[:3]
    return {
        'levels': levels,
        'level_series': level_series,
        'level_rows': level_rows,
        'procs': procs,
        'only_a': sorted(only_a),
        'only_b': sorted(only_b),
        'level_changed': changed,
        'top': [p['name'] for p in top],
        'new_top': [{'name': p['name'], 'v': p['rb']} for p in new_top],
        'gone_top': [{'name': p['name'], 'v': p['ra']} for p in gone_top],
    }


# ---------- 系统汇总 ----------
def _sys_series(ds: Dataset, field, src):
    if src == 'tail':
        return [rd.tail.get(field) for rd in ds.rounds]
    return [rd.meminfo.get(field) for rd in ds.rounds]


def build_system(dsa: Dataset, dsb: Dataset):
    rows = []
    for field, src in SYSTEM_FIELDS:
        a = _sys_series(dsa, field, src)
        b = _sys_series(dsb, field, src)
        ra, rb = rmean(a, dsa.n), rmean(b, dsb.n)
        g = gap_info(ra, rb)
        rows.append({
            'name': field,
            'src': 'dumpsys meminfo' if src == 'tail' else '/proc/meminfo',
            'hb': field in HIGHER_BETTER,
            'a': a, 'b': b,
            'ra': ra, 'rb': rb, 'gap': g['gap'], 'gapp': g['pct'], 'gapt': g['txt'],
            'mina': vmin(a), 'maxa': vmax(a), 'minb': vmin(b), 'maxb': vmax(b),
        })
    return rows


# ---------- 疑似增长 ----------
def growth_hints(dsa: Dataset, dsb: Dataset, mpss, opts: Options):
    hints = []
    for side, ds in (('A', dsa), ('B', dsb)):
        if ds.n < 5:
            continue
        for p in mpss['procs'].values():
            series = p['ta'] if side == 'A' else p['tb']
            if cnt(series) < ds.n:
                continue
            slope, r2 = linreg(series)
            if slope >= opts.growth_slope and r2 >= opts.growth_r2:
                hints.append({'ds': side, 'kind': 'proc', 'name': p['name'],
                              'slope': slope, 'r2': r2})
        for field, want in (('Used RAM', 'up'), ('Free RAM', 'down'),
                            ('MemAvailable', 'down')):
            src = 'tail' if field != 'MemAvailable' else 'proc'
            s = _sys_series(ds, field, src)
            if cnt(s) < ds.n:
                continue
            slope, r2 = linreg([x or 0.0 for x in s])
            ok = (slope >= opts.sys_growth_slope if want == 'up'
                  else slope <= -opts.sys_growth_slope)
            if ok and r2 >= opts.growth_r2:
                hints.append({'ds': side, 'kind': 'sys', 'name': field,
                              'dir': want, 'slope': slope, 'r2': r2})
    return hints


# ---------- 顶层 ----------
def _events_json(ds: Dataset):
    return [{
        't': e.t.strftime('%Y-%m-%d %H:%M:%S'),
        'kind': e.kind, 'proc': e.proc, 'reason': e.reason,
        'comp': e.comp, 'caller': e.caller, 'adj': e.adj, 'phase': e.phase,
    } for e in ds.events]


def _dataset_json(ds: Dataset):
    starts = [r.start for r in ds.rounds if r.start]
    interval = None
    if len(starts) > 1:
        d = sum((starts[i] - starts[i - 1]).total_seconds()
                for i in range(1, len(starts))) / (len(starts) - 1)
        interval = d / 60.0
    reasons = {}
    for e in ds.events:
        if e.kind == 'start' and e.phase == 'test':
            k = e.reason or '(无)'
            reasons[k] = reasons.get(k, 0) + 1
    return {
        'label': ds.label,
        'device': ds.device,
        'packages': ds.packages,
        'n': ds.n,
        'rounds': [{'i': r.index,
                    'start': r.start.strftime('%Y-%m-%d %H:%M:%S') if r.start else None,
                    'end': r.end.strftime('%Y-%m-%d %H:%M:%S') if r.end else None}
                   for r in ds.rounds],
        'span': (starts[0].strftime('%Y-%m-%d %H:%M:%S') + ' ~ '
                 + starts[-1].strftime('%Y-%m-%d %H:%M:%S')) if starts else '—',
        'interval_min': interval,
        'boot_time': ds.boot_time.strftime('%Y-%m-%d %H:%M:%S') if ds.boot_time else None,
        'events': _events_json(ds),
        'reason_counts': reasons,
        'warnings': ds.warnings,
    }


def _standards_table(ds: Dataset, m, side: str):
    """单数据集标准对照（RSS 口径）。"""
    th = ds.thresholds
    if not th.available:
        return None
    res = {'levels': [], 'free_ram': None, 'procs': []}
    for lv, t in th.levels.items():
        s = m['level_series'].get(lv, {}).get(side, [])
        mean = vmean(s)
        peak = vmax(s)
        if mean is None:
            continue
        res['levels'].append({'name': lv, 'thr': t, 'mean': mean, 'peak': peak,
                              'viol': mean > t, 'peak_viol': peak > t})
    if th.free_ram is not None:
        s = _sys_series(ds, 'Free RAM', 'tail')
        mean, low = vmean(s), vmin(s)
        if mean is not None:
            res['free_ram'] = {'name': 'Free RAM', 'thr': th.free_ram,
                               'mean': mean, 'low': low,
                               'viol': mean < th.free_ram,
                               'peak_viol': low < th.free_ram}
    for name, d in th.procs.items():
        p = m['procs'].get(name)
        if not p:
            continue
        va = p['va'] if side == 'a' else p['vb']
        pk = p['maxa'] if side == 'a' else p['maxb']
        if va is None:
            continue
        t = d['threshold']
        res['procs'].append({'name': name, 'level': d['level'], 'thr': t,
                             'mean': va, 'peak': pk,
                             'viol': va > t, 'peak_viol': pk > t})
    res['levels'].sort(key=lambda r: LEVEL_ORDER.index(r['name'])
                       if r['name'] in LEVEL_ORDER else 99)
    res['procs'].sort(key=lambda r: -r['thr'])
    return res


def analyze(dsa: Dataset, dsb: Dataset, opts: Options) -> dict:
    mpss = build_metric(dsa, dsb, 'pss', opts)
    mrss = build_metric(dsa, dsb, 'rss', opts)
    system = build_system(dsa, dsb)
    dsj = [_dataset_json(dsa), _dataset_json(dsb)]

    pa, pb = set(dsa.packages), set(dsb.packages)
    pkg_diff = {'a': sorted(pa - pb), 'b': sorted(pb - pa)}

    fields = list(dict.fromkeys(list(dsa.device) + list(dsb.device)))
    device_diff = [{'field': f, 'a': dsa.device.get(f, ''), 'b': dsb.device.get(f, ''),
                    'diff': dsa.device.get(f, '') != dsb.device.get(f, '')}
                   for f in fields]
    device_diff += [
        {'field': '采集轮次', 'a': str(dsa.n), 'b': str(dsb.n),
         'diff': dsa.n != dsb.n},
        {'field': '测试时间范围',
         'a': dsj[0]['span'], 'b': dsj[1]['span'], 'diff': True},
        {'field': '平均采样间隔(分钟)',
         'a': f"{dsj[0]['interval_min']:.1f}" if dsj[0]['interval_min'] else '—',
         'b': f"{dsj[1]['interval_min']:.1f}" if dsj[1]['interval_min'] else '—',
         'diff': False},
    ]

    ih = {
        'whitelist_src': opts.whitelist_src,
        'whitelist_count': len(opts.whitelist or set()),
        'prefixes': list(opts.prefixes),
        'prefixes_src': opts.prefixes_src,
    }

    growth = growth_hints(dsa, dsb, mpss, opts)
    std_a = _standards_table(dsa, mrss, 'a')
    std_b = _standards_table(dsb, mrss, 'b')

    return {
        'labels': [dsa.label, dsb.label],
        'meta': {
            'tool': 'memscope 0.2.0',
            'gap_abs': opts.gap_abs, 'gap_pct': opts.gap_pct,
            'growth_slope': opts.growth_slope,
            'sys_growth_slope': opts.sys_growth_slope,
            'growth_r2': opts.growth_r2,
        },
        'datasets': dsj,
        'device_diff': device_diff,
        'pkg_diff': pkg_diff,
        'metrics': {'pss': mpss, 'rss': mrss},
        'system': system,
        'inhouse': ih,
        'standards': {'a': std_a, 'b': std_b},
        'growth': growth,
    }
