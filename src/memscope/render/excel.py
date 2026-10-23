"""渲染 data.xlsx（openpyxl）：全部明细数据表。"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD_FONT = Font(bold=True)
HEAD_FILL = PatternFill('solid', fgColor='E2E8F0')
RED = Font(color='DC2626')
GREEN = Font(color='16A34A')
NUM = '0.00'


def _sheet(wb, title):
    ws = wb.create_sheet(title)
    return ws


def _write_table(ws, header, rows, num_cols=(), gap_col=None, widths=None):
    ws.append(header)
    for c in range(1, len(header) + 1):
        cell = ws.cell(1, c)
        cell.font = HEAD_FONT
        cell.fill = HEAD_FILL
    for r in rows:
        ws.append(list(r))
    ws.freeze_panes = 'A2'
    if rows:
        ws.auto_filter.ref = f'A1:{get_column_letter(len(header))}{len(rows) + 1}'
    for i in range(1, len(header) + 1):
        letter = get_column_letter(i)
        w = max((len(str(header[i - 1])) * 2,
                 *(len(str(r[i - 1])) * 1.1 for r in rows[:200]))) if rows else len(str(header[i - 1])) * 2
        ws.column_dimensions[letter].width = min(max(w, 8), 60)
    if widths:
        for i, w in widths.items():
            ws.column_dimensions[get_column_letter(i)].width = w
    for c in num_cols:
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(r, c)
            if isinstance(cell.value, (int, float)):
                cell.number_format = NUM
    if gap_col:
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(r, gap_col)
            if isinstance(cell.value, (int, float)):
                cell.number_format = NUM
                if cell.value > 0:
                    cell.font = RED
                elif cell.value < 0:
                    cell.font = GREEN


def _gaptxt(p):
    return p['gapt'] or (f"{p['gapp']:.1f}%" if p['gapp'] is not None else '—')


def _join(xs):
    return '、'.join(xs) if xs else '—'


def render(R: dict, out_path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    na, nb = R['datasets'][0]['n'], R['datasets'][1]['n']
    metrics = [('PSS', R['metrics']['pss']), ('RSS', R['metrics']['rss'])]

    # 设备信息
    ws = _sheet(wb, '设备信息')
    _write_table(ws, ['字段', 'A（基线）', 'B（对比）', '差异'],
                 [[d['field'], d['a'], d['b'], '不同' if d['diff'] else '']
                  for d in R['device_diff']], widths={2: 42, 3: 42})

    # 预装包差异
    ws = _sheet(wb, '预装包差异')
    pd = R['pkg_diff']
    rows = max(len(pd['a']), len(pd['b']))
    _write_table(ws, ['仅 A 存在', '仅 B 存在'],
                 [[pd['a'][i] if i < len(pd['a']) else '',
                   pd['b'][i] if i < len(pd['b']) else ''] for i in range(rows)],
                 widths={1: 50, 2: 50})

    # 整机汇总
    ws = _sheet(wb, '整机汇总')
    _write_table(ws, ['指标', '来源', 'A 均值', 'B 均值', 'GAP', 'GAP%',
                      'A 最小', 'A 最大', 'B 最小', 'B 最大', '方向'],
                 [[r['name'], r['src'], r['ra'], r['rb'], r['gap'], _gaptxt(r),
                   r['mina'], r['maxa'], r['minb'], r['maxb'],
                   '越高越好' if r['hb'] else '越低越好'] for r in R['system']],
                 num_cols=(3, 4, 7, 8, 9, 10), gap_col=5)

    # 层级汇总（双口径）
    ws = _sheet(wb, '层级汇总')
    _write_table(ws, ['口径', '层级', 'A 均值', 'B 均值', 'GAP', 'GAP%', '进程数A', '进程数B'],
                 [[k, r['level'], r['ra'], r['rb'], r['gap'], _gaptxt(r), r['ca'], r['cb']]
                  for k, m in metrics for r in m['level_rows']],
                 num_cols=(3, 4), gap_col=5)

    # 进程对比（双口径全量）
    ws = _sheet(wb, '进程对比')
    prow_rows = []
    for k, m in metrics:
        for n in m['top']:
            p = m['procs'][n]
            marks = []
            if p['ka'] and not p['kb']:
                marks.append('仅A')
            if p['kb'] and not p['ka']:
                marks.append('仅B')
            if p['level_changed']:
                marks.append('层级变化')
            if p['cached_only']:
                marks.append('仅Cached')
            if p['sig']:
                marks.append('显著')
            prow_rows.append([k, p['name'], '是' if p['inhouse'] else '',
                              p['ih_src'] or '', _join(p['la']), _join(p['lb']),
                              p['ka'], p['kb'], p['ra'], p['rb'], p['va'], p['vb'],
                              p['gap'], _gaptxt(p), ','.join(marks)])
    _write_table(ws, ['口径', '进程', '自研', '判定来源', '层级A', '层级B',
                      '出现A', '出现B', '轮均A', '轮均B', '值均A', '值均B',
                      'GAP', 'GAP%', '标记'],
                 prow_rows, num_cols=(9, 10, 11, 12), gap_col=13,
                 widths={2: 55, 5: 22, 6: 22})

    # 层级进程明细（双口径，层级×进程）
    ws = _sheet(wb, '层级进程明细')
    rows = []
    for k, m in metrics:
        for lr in m['level_rows']:
            lv = lr['level']
            for n in m['top']:
                p = m['procs'][n]
                sa, sb = p['sa'].get(lv), p['sb'].get(lv)
                if not sa and not sb:
                    continue
                ra = sum(x or 0 for x in (sa or [])) / na if sa else 0.0
                rb = sum(x or 0 for x in (sb or [])) / nb if sb else 0.0
                va = (lambda s: (sum(x for x in s if x is not None) /
                                 sum(1 for x in s if x is not None)) if any(x is not None for x in s) else None)
                rows.append([k, lv, n,
                             sum(1 for x in (sa or []) if x is not None),
                             sum(1 for x in (sb or []) if x is not None),
                             ra, rb, va(sa or []), va(sb or []), rb - ra])
    _write_table(ws, ['口径', '层级', '进程', '出现A', '出现B', '轮均A', '轮均B',
                      '值均A', '值均B', 'GAP'],
                 rows, num_cols=(6, 7, 8, 9), gap_col=10, widths={3: 55})

    # 差异显著（PSS）
    ws = _sheet(wb, '差异显著')
    mp = R['metrics']['pss']
    sig = [mp['procs'][n] for n in mp['top']
           if mp['procs'][n]['sig'] and not mp['procs'][n]['cached_only']]
    _write_table(ws, ['进程', '层级A', '层级B', '出现A', '出现B',
                      '轮均A', '轮均B', '值均A', '值均B', 'GAP', 'GAP%'],
                 [[p['name'], _join(p['la']), _join(p['lb']), p['ka'], p['kb'],
                   p['ra'], p['rb'], p['va'], p['vb'], p['gap'], _gaptxt(p)]
                  for p in sig], num_cols=(6, 7, 8, 9), gap_col=10, widths={1: 55})

    # 独有进程（双口径）
    ws = _sheet(wb, '独有进程')
    rows = []
    for k, m in metrics:
        for side, key in (('A', 'only_a'), ('B', 'only_b')):
            for n in m[key]:
                p = m['procs'][n]
                rows.append([k, n, f'仅{side}',
                             _join(p['la'] if side == 'A' else p['lb']),
                             p['ka'] if side == 'A' else p['kb'],
                             p['ra'] if side == 'A' else p['rb'],
                             p['va'] if side == 'A' else p['vb']])
    _write_table(ws, ['口径', '进程', '归属', '所在层级', '出现次数', '轮次均值', '数值均值'],
                 rows, num_cols=(6, 7), widths={2: 55})

    # 层级变化（双口径，各轮层级序列）
    ws = _sheet(wb, '层级变化')
    rows = []
    for k, m in metrics:
        for n in m['level_changed']:
            p = m['procs'][n]
            rows.append([k, n,
                         ','.join(x or '·' for x in p['pra']),
                         ','.join(x or '·' for x in p['prb'])])
    _write_table(ws, ['口径', '进程', 'A 各轮层级', 'B 各轮层级'],
                 rows, widths={2: 55, 3: 60, 4: 60})

    # 自研应用（双口径）
    ws = _sheet(wb, '自研应用')
    rows = []
    for k, m in metrics:
        for n in m['top']:
            p = m['procs'][n]
            if not p['inhouse']:
                continue
            rows.append([k, p['name'], '白名单' if p['ih_src'] == 'whitelist' else '前缀',
                         _join(p['la']), _join(p['lb']), p['ka'], p['kb'],
                         p['ra'], p['rb'], p['gap'], _gaptxt(p)])
    _write_table(ws, ['口径', '进程', '判定', '层级A', '层级B', '出现A', '出现B',
                      '轮均A', '轮均B', 'GAP', 'GAP%'],
                 rows, num_cols=(8, 9), gap_col=10, widths={2: 55})

    # 标准对照
    ws = _sheet(wb, '标准对照')
    rows = []
    for side, lab in (('a', 'A'), ('b', 'B')):
        st = R['standards'].get(side)
        if not st:
            rows.append([lab, '(无阈值数据)', '', '', '', '', '', ''])
            continue
        if st['free_ram']:
            r = st['free_ram']
            rows.append([lab, 'Free RAM(下限)', '', r['thr'], r['mean'], r['low'],
                         '超标' if r['viol'] else '达标',
                         '峰值超标' if r['peak_viol'] else ''])
        for r in st['levels']:
            rows.append([lab, r['name'], '', r['thr'], r['mean'], r['peak'],
                         '超标' if r['viol'] else '达标',
                         '峰值超标' if r['peak_viol'] else ''])
        for r in st['procs']:
            rows.append([lab, r['name'], r['level'], r['thr'], r['mean'], r['peak'],
                         '超标' if r['viol'] else '达标',
                         '峰值超标' if r['peak_viol'] else ''])
    _write_table(ws, ['数据集', '对象', '层级', '阈值', '均值', '峰值', '均值判定', '峰值判定'],
                 rows, num_cols=(4, 5, 6), widths={2: 50})

    # 疑似增长
    ws = _sheet(wb, '疑似增长')
    _write_table(ws, ['数据集', '对象', '类型', '斜率(MB/轮)', 'R²'],
                 [[h['ds'], h['name'],
                   '进程(PSS)' if h['kind'] == 'proc' else '整机',
                   h['slope'], h['r2']] for h in R['growth']],
                 num_cols=(4, 5), widths={2: 55})

    # 启动事件
    ws = _sheet(wb, '启动事件')
    rows = []
    for i, lab in ((0, 'A'), (1, 'B')):
        for e in R['datasets'][i]['events']:
            rows.append([lab, e['t'], e['phase'], e['kind'], e['proc'],
                         e['reason'], e['comp'], e['caller'], e['adj']])
    _write_table(ws, ['数据集', '时间', '阶段', '类型', '进程', '原因', '组件', '调用方', 'adj'],
                 rows, widths={5: 40, 7: 55, 8: 40})

    # 每轮明细_PSS / _RSS
    for k, m in metrics:
        ws = _sheet(wb, f'每轮明细_{k}')
        header = ['进程', '层级A'] + [f'A第{i + 1}轮' for i in range(na)] \
            + ['层级B'] + [f'B第{i + 1}轮' for i in range(nb)]
        rows = []
        for n in m['top']:
            p = m['procs'][n]
            rows.append([n, _join(p['la'])] + list(p['ta'])
                        + [_join(p['lb'])] + list(p['tb']))
        num_cols = tuple(range(3, 3 + na)) + tuple(range(3 + na, 3 + na + nb))
        _write_table(ws, header, rows, num_cols=num_cols, widths={1: 55, 2: 18, 3 + na: 18})

    out_path = Path(out_path)
    wb.save(out_path)
    return out_path
