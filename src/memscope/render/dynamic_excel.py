"""动态内存对比 data.xlsx（openpyxl write_only）。数据源为 analyze() 的 result。"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill

HEAD_FONT = Font(bold=True)
HEAD_FILL = PatternFill('solid', fgColor='E2E8F0')


def _sheet(wb, title, header):
    ws = wb.create_sheet(title)
    ws.append([_head_cell(ws, h) for h in header])
    return ws


def _head_cell(ws, v):
    c = WriteOnlyCell(ws, value=v)
    c.font = HEAD_FONT
    c.fill = HEAD_FILL
    return c


def _f(v, nd=2):
    return None if v is None else round(v, nd)


def render(R: dict, out_path) -> Path:
    wb = Workbook(write_only=True)
    dsA, dsB = R['datasets']
    mrss, mpss = R['metrics']['rss'], R['metrics']['pss']

    # 测试信息
    ws = _sheet(wb, '测试信息', ['字段', 'A（基线）', 'B（对比）', '差异'])
    for d in R['device_diff']:
        ws.append([d['field'], d['a'], d['b'], '不同' if d['diff'] else ''])
    pd = R['pkg_diff']
    ws.append(['预装包总数', len(dsA['packages']), len(dsB['packages']),
               '不同' if len(dsA['packages']) != len(dsB['packages']) else ''])
    ws.append(['仅 A 预装包数', len(pd['a']), '', ''])
    ws.append(['仅 B 预装包数', '', len(pd['b']), ''])

    # 标准对照
    ws = _sheet(wb, '标准对照',
                ['数据集', '对象', '层级', '阈值', '均值', 'P90/P10', '峰值',
                 '均值判定', '超标轮比例'])
    for st, lab, side in ((R['standards']['a'], 'A', 'a'),
                          (R['standards']['b'], 'B', 'b')):
        if not st:
            ws.append([lab, '(无阈值数据)'] + [''] * 7)
            continue
        ws.append([lab, f'判定: {st["verdict"]}'] + [''] * 7)
        if st['free_ram']:
            r = st['free_ram']
            ws.append([lab, 'Free RAM(下限)', '', r['thr'], _f(r['mean']),
                       _f(r['p10']), _f(r['low']),
                       '超标' if r['viol'] else '达标',
                       round(r['exceed_rate'], 4)])
        for r in st['levels']:
            ws.append([lab, r['name'], '', r['thr'], _f(r['mean']),
                       _f(r['p90']), _f(r['peak']),
                       '超标' if r['viol'] else '达标',
                       round(r['exceed_rate'], 4)])
        for r in st['procs']:
            ws.append([lab, r['name'], r['level'], r['thr'], _f(r['mean']),
                       '', _f(r['peak']),
                       '超标' if r['viol'] else '达标',
                       round(r['exceed_rate'], 4)])

    # 结论（findings 全量）
    ws = _sheet(wb, '结论', ['级别', '类别', '标题', '详情', '链接'])
    for f in R['findings']:
        ws.append([f['sev'], f['cat'], f['title'], f['detail'], f['link']])

    # 整机对比
    ws = _sheet(wb, '整机对比',
                ['指标', '来源', '越高越好', 'A 均值', 'B 均值', 'GAP', 'GAP%',
                 'A P90', 'B P90', 'A 最大', 'B 最大', 'A 斜率/h', 'B 斜率/h',
                 'A R²', 'B R²', '疑似泄漏'])
    for r in R['system']:
        ws.append([r['name'], r['src'], '是' if r['hb'] else '',
                   _f(r['da']['mean']), _f(r['db']['mean']), _f(r['gap']),
                   r['gapt'] or _f(r['gapp'], 1),
                   _f(r['da']['p90']), _f(r['db']['p90']),
                   _f(r['da']['max']), _f(r['db']['max']),
                   _f(r['ta']['slope'] if r['ta'] else None),
                   _f(r['tb']['slope'] if r['tb'] else None),
                   _f(r['ta']['r2'] if r['ta'] else None),
                   _f(r['tb']['r2'] if r['tb'] else None),
                   ('A' if r['leak_a'] else '') + ('B' if r['leak_b'] else '')])

    # 层级对比（双口径）
    for tag, m in (('RSS', mrss), ('PSS', mpss)):
        ws = _sheet(wb, f'层级对比_{tag}',
                    ['层级', 'A mi', 'B mi', 'GAP', 'GAP%', 'A 均值', 'B 均值',
                     'A P50', 'B P50', 'A P90', 'B P90', 'A P95', 'B P95',
                     'A 最大', 'B 最大', 'P90差', '进程数A', '进程数B',
                     '显著', '泄漏'])
        for r in m['level_rows']:
            ws.append([r['level'], _f(r['da']['mi']), _f(r['db']['mi']),
                       _f(r['gap']), r['gapt'] or _f(r['gapp'], 1),
                       _f(r['da']['mean']), _f(r['db']['mean']),
                       _f(r['da']['p50']), _f(r['db']['p50']),
                       _f(r['da']['p90']), _f(r['db']['p90']),
                       _f(r['da']['p95']), _f(r['db']['p95']),
                       _f(r['da']['max']), _f(r['db']['max']),
                       _f(r['gap_p90']), r['ca'], r['cb'],
                       '✓' if r['sig'] else '',
                       ('A' if r['leak_a'] else '')
                       + ('B' if r['leak_b'] else '')])

    # 进程对比（双口径）
    for tag, m in (('RSS', mrss), ('PSS', mpss)):
        ws = _sheet(wb, f'进程对比_{tag}',
                    ['进程', '层级A', '层级B', '出现A', '出现B', '出现率A',
                     '出现率B', 'mi A', 'mi B', '均值A', '均值B', 'P90 A',
                     'P90 B', '最大A', '最大B', 'GAP', 'GAP%', '常驻A',
                     '常驻B', '泄漏', '自研', 'Google', '标记'])
        for n in m['top']:
            p = m['procs'][n]
            marks = []
            if p['gapt'] == '新增':
                marks.append('新增')
            if p['gapt'] == '消失':
                marks.append('消失')
            if p['sig']:
                marks.append('显著')
            if p['cached_only']:
                marks.append('仅Cached')
            ws.append([n, '、'.join(p['la']), '、'.join(p['lb']),
                       p['da']['k'], p['db']['k'],
                       _f(p['da']['rate']), _f(p['db']['rate']),
                       _f(p['da']['mi']), _f(p['db']['mi']),
                       _f(p['da']['mean']), _f(p['db']['mean']),
                       _f(p['da']['p90']), _f(p['db']['p90']),
                       _f(p['da']['max']), _f(p['db']['max']),
                       _f(p['gap']), p['gapt'] or _f(p['gapp'], 1),
                       '✓' if p['resident_a'] else '',
                       '✓' if p['resident_b'] else '',
                       ('A' if p['leak_a'] else '')
                       + ('B' if p['leak_b'] else ''),
                       ('白名单' if p['ih_src'] == 'whitelist' else '前缀')
                       if p['inhouse'] else '',
                       '是' if p['google'] else '',
                       ','.join(marks)])

    # 疑似泄漏
    ws = _sheet(wb, '疑似泄漏',
                ['数据集', '类型', '口径', '对象', 'head', 'tail', 'delta',
                 '斜率MB/h', 'R²'])
    for lk in R['leaks']:
        ws.append([lk['ds'], lk['kind'], lk['metric'], lk['name'],
                   _f(lk['head']), _f(lk['tail']), _f(lk['delta']),
                   _f(lk['slope']), _f(lk['r2'])])

    # 内存压力
    ws = _sheet(wb, '内存压力',
                ['计数器', '单位', 'A 总量', 'A /小时', 'A P90/轮',
                 'A 覆盖小时', 'B 总量', 'B /小时', 'B P90/轮', 'B 覆盖小时',
                 'GAP/小时', 'GAP%'])
    for c in R['pressure'].get('counters', []):
        ws.append([c['name'], c['unit'],
                   _f(c['sa']['total'] if c['sa'] else None),
                   _f(c['sa']['per_hour'] if c['sa'] else None),
                   _f(c['sa']['p90'] if c['sa'] else None),
                   _f(c['sa']['cov_hours'] if c['sa'] else None),
                   _f(c['sb']['total'] if c['sb'] else None),
                   _f(c['sb']['per_hour'] if c['sb'] else None),
                   _f(c['sb']['p90'] if c['sb'] else None),
                   _f(c['sb']['cov_hours'] if c['sb'] else None),
                   _f(c['gap']), _f(c['gapp'], 1)])

    # LMK查杀（全量明细）
    ws = _sheet(wb, 'LMK查杀',
                ['数据集', '时间', '进程', 'adj', '层级', 'rss_mb', 'code',
                 '原因'])
    for side, lab in (('a', 'A'), ('b', 'B')):
        e = R['events'][side]['lmk']
        rname = {r['code']: r['name'] for r in e['by_reason']}
        for x in e['list']:
            ws.append([lab, x['t'], x['proc'], x['adj'], x['level'],
                       _f(x['rss_mb']), x['code'],
                       rname.get(x['code'], '')])

    # am_kill原因
    ws = _sheet(wb, 'am_kill原因', ['数据集', '类别', '名称', '次数'])
    for side, lab in (('a', 'A'), ('b', 'B')):
        e = R['events'][side]['am_kill']
        for r in e['by_reason']:
            ws.append([lab, 'reason', r['reason'], r['n']])
        for r in e['top']:
            ws.append([lab, 'proc', r['proc'], r['n']])

    # ANR_Crash
    ws = _sheet(wb, 'ANR_Crash', ['数据集', '类型', '时间', '进程', '原因'])
    for side, lab in (('a', 'A'), ('b', 'B')):
        e = R['events'][side]
        for x in e['anr']:
            ws.append([lab, 'anr', x['t'], x['proc'], x['reason']])
        for x in e['crash']:
            ws.append([lab, 'crash', x['t'], x['proc'], x['reason']])

    # 每轮_整机_A / B
    sys_fields = [r['name'] for r in R['system']]
    for side, ds, lab in (('a', dsA, 'A'), ('b', dsB, 'B')):
        ws = _sheet(wb, f'每轮_整机_{lab}',
                    ['轮次', '时间', '小时']
                    + sys_fields
                    + [f'RSS:{lv}' for lv in mrss['levels']]
                    + [f'PSS:{lv}' for lv in mpss['levels']])
        n = ds['n']
        for i in range(n):
            row = [ds['rounds'][i], ds['start_times'][i],
                   _f(ds['hours'][i])]
            row += [r[side][i] for r in R['system']]
            row += [r[side][i] for r in mrss['level_rows']]
            row += [r[side][i] for r in mpss['level_rows']]
            ws.append(row)

    # 每轮_进程RSS_A / B（行=轮次，列=进程）
    for side, ds, lab in (('a', dsA, 'A'), ('b', dsB, 'B')):
        ws = _sheet(wb, f'每轮_进程RSS_{lab}', ['轮次'] + mrss['top'])
        procs = [mrss['procs'][n] for n in mrss['top']]
        for i in range(ds['n']):
            row = [ds['rounds'][i]]
            for p in procs:
                s = p[side]
                row.append(s[i] if s else None)
            ws.append(row)

    out_path = Path(out_path)
    wb.save(out_path)
    return out_path
