"""生成 Markdown 对比报告（中文）。数据源为 analyze() 的 result dict。

静态/动态共用同一章节结构与编号；差异仅来自数据有无与 meta 中的按类参数。
"""
from __future__ import annotations

from . import cross_notices, ev_src_text, std_level_name


def _f(v, nd=2):
    return '—' if v is None else f'{v:.{nd}f}'


def _gap_txt(gap, gapp, gapt):
    if gapt:
        return f'{gap:+.2f} / {gapt}'
    if gapp is None:
        return f'{gap:+.2f} / —'
    return f'{gap:+.2f} / {gapp:+.1f}%'


def _table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |',
           '|' + '---|' * len(header)]
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


_SEV_CN = {'high': '高', 'mid': '中', 'low': '低', 'info': '参考'}


def _fmt_hours(ds):
    return f"{ds['hours_total']:.1f}" if ds.get('hours_total') else '—'


def _proc_marks(p):
    marks = []
    if p['gapt'] == '新增':
        marks.append('新增')
    if p['gapt'] == '消失':
        marks.append('消失')
    if p['resident_b']:
        marks.append('常驻')
    if p['leak_a'] or p['leak_b']:
        marks.append('泄漏')
    if p['level_changed']:
        marks.append('层级变化')
    if p['inhouse']:
        marks.append('自研')
    if p['google']:
        marks.append('Google')
    if p['cached_only']:
        marks.append('Cached')
    return '、'.join(marks)


def render(R: dict) -> str:
    la, lb = R['labels']
    meta = R['meta']
    kind = meta.get('kind', 'static')
    pm = meta.get('primary_metric', 'pss')
    dsA, dsB = R['datasets']
    mrss, mpss = R['metrics']['rss'], R['metrics']['pss']
    mp = mrss if pm == 'rss' else mpss
    L = []
    L.append(f"# {'静态' if kind == 'static' else '动态'}内存对比报告")
    L.append('')
    L.append(f'- 基线 A：**{la}**　对比 B：**{lb}**（{meta["tool"]}）')
    dd = {d['field']: d for d in R['device_diff']}
    if '体验模型' in dd or '芯片平台' in dd:
        L.append(f"- 体验模型：A {dd.get('体验模型', {}).get('a', '—')} / "
                 f"B {dd.get('体验模型', {}).get('b', '—')}；"
                 f"芯片平台：A {dd.get('芯片平台', {}).get('a', '—')} / "
                 f"B {dd.get('芯片平台', {}).get('b', '—')}")
    L.append(f"- 有效轮次：A {dsA['n']} / B {dsB['n']}；"
             f"测试时长：A {_fmt_hours(dsA)} h / B {_fmt_hours(dsB)} h；"
             f"显著性阈值：|GAP| ≥ {meta['gap_abs']} MB 且 ≥ {meta['gap_pct']}%")
    L.append('')
    for n in cross_notices(R):
        L.append(f'> {n}')
        L.append('')

    # ---- 1. 测试信息 ----
    L.append('## 1. 测试信息（设备与判定）')
    L.append('')
    L.append(_table(['字段', 'A（基线）', 'B（对比）', ''],
                    [[d['field'], d['a'], d['b'], '**不同**' if d['diff'] else '']
                     for d in R['device_diff']]))
    L.append('')
    pd = R['pkg_diff']
    L.append(f"预装包：A {len(dsA['packages'])} 个 / B {len(dsB['packages'])} 个；"
             f"仅 A **{len(pd['a'])}** 个，仅 B **{len(pd['b'])}** 个")
    L.append('')
    if pd['a']:
        L.append('- 仅 A：' + '、'.join(f'`{p}`' for p in pd['a']))
    if pd['b']:
        L.append('- 仅 B：' + '、'.join(f'`{p}`' for p in pd['b']))
    L.append('')
    for side, ds, lab in ((0, dsA, 'A'), (1, dsB, 'B')):
        if ds['failed_rounds']:
            L.append(f"- {lab} 无效轮：" + '、'.join(
                f"{d['index']}({d['reason']})" for d in ds['failed_rounds'][:10]))
        if ds['reboots']:
            L.append(f"- {lab} 检测到重启：" + '、'.join(
                f"第 {r['round']} 轮前" for r in ds['reboots']))
    L.append('')

    # ---- 2. 结论摘要 ----
    L.append('## 2. 结论摘要')
    L.append('')
    sa, sb = R['standards']['a'], R['standards']['b']
    L.append('| | 平台判定 | memscope 判定 | 超标项 |')
    L.append('|---|---|---|---|')
    for st, ds, lab in ((sa, dsA, 'A'), (sb, dsB, 'B')):
        plat = ds['platform'].get('result', '—') or '—'
        ver = st['verdict'] if st else '—（无阈值）'
        items = '、'.join(st['fail_items']) if st and st['fail_items'] else '—'
        L.append(f'| {lab} | {plat} | {ver} | {items} |')
    L.append('')
    for st, ds, lab in ((sa, dsA, 'A'), (sb, dsB, 'B')):
        basis = ds['platform'].get('basis') or []
        if basis:
            L.append(f"- {lab} 平台依据：" + '；'.join(basis))
        else:
            L.append(f"- {lab} 平台依据：" +
                     (ds['platform'].get('basis_note') or '报告未提供超标明细'))
        if st:
            k = sum(1 for r in st['procs'] if r['viol'])
            L.append(f"- {lab} memscope 依据：层级 + Free RAM 计入判定；"
                     f'进程超标 {k} 项不计入判定（见标准与风险）')
    L.append('')
    if R['findings']:
        L.append('自动结论（前 15 条，按严重度排序，完整列表见 HTML「标准与风险」页）：')
        L.append('')
        for f in R['findings'][:15]:
            L.append(f"- [{_SEV_CN.get(f['sev'], f['sev'])}] "
                     f"**{f['title']}** — {f['detail']}")
    else:
        L.append('无自动结论。')
    L.append('')

    # ---- 3. 健康标准对照 ----
    L.append('## 3. 健康标准对照（RSS 口径，各侧用自身阈值）')
    L.append('')
    for st, lab in ((sa, 'A'), (sb, 'B')):
        L.append(f'数据集 {lab}：' + ('无阈值文件' if not st
                 else f"判定 **{st['verdict']}**"))
        L.append('')
        if not st:
            continue
        rows = []
        if st['free_ram']:
            r = st['free_ram']
            rows.append(['Free RAM(下限)', _f(r['thr']), _f(r['mean']),
                         _f(r['p10']) + '(P10)', _f(r['low']),
                         '超标' if r['viol'] else '达标',
                         f"{r['exceed_rate'] * 100:.1f}%"])
        for r in st['levels']:
            rows.append([std_level_name(r), _f(r['thr']), _f(r['mean']),
                         _f(r['p90']) + '(P90)', _f(r['peak']),
                         '超标' if r['viol'] else '达标',
                         f"{r['exceed_rate'] * 100:.1f}%"])
        if rows:
            L.append(_table(['指标', '阈值', '值均', '分位', '峰值',
                             '值均判定', '超标轮比例'], rows))
            L.append('')
        if st['procs']:
            L.append(_table(['进程', '层级', '阈值', '值均', '峰值',
                             '值均判定', '超标轮比例'],
                            [[r['name'], r['level'], _f(r['thr']),
                              _f(r['mean']), _f(r['peak']),
                              '超标' if r['viol'] else '达标',
                              f"{r['exceed_rate'] * 100:.1f}%"]
                             for r in st['procs']]))
            L.append('')

    # ---- 4. 整机内存 ----
    L.append('## 4. 整机内存（值均 = 出现轮均值，MB）')
    L.append('')
    if R['system']:
        L.append(_table(['指标', '来源', 'A 值均', 'B 值均', 'GAP', 'GAP%',
                         'A P90', 'B P90', 'A 最大', 'B 最大',
                         'A 斜率/h', 'B 斜率/h'],
                        [[r['name'], r['src'], _f(r['da']['mean']),
                          _f(r['db']['mean']), f"{r['gap']:+.2f}",
                          r['gapt'] or (f"{r['gapp']:+.1f}%"
                                        if r['gapp'] is not None else '—'),
                          _f(r['da']['p90']), _f(r['db']['p90']),
                          _f(r['da']['max']), _f(r['db']['max']),
                          _f(r['ta']['slope'] if r['ta'] else None),
                          _f(r['tb']['slope'] if r['tb'] else None)]
                         for r in R['system']]))
    else:
        L.append('无数据')
    L.append('')

    # ---- 5. 层级内存 ----
    L.append('## 5. 层级内存')
    L.append('')
    for tag, m in (('RSS', mrss), ('PSS', mpss)):
        L.append(f'### 5.{1 if tag == "RSS" else 2} 层级内存对比（{tag}，轮均 MB）')
        L.append('')
        if m['level_rows']:
            L.append(_table(
                ['层级', 'A 轮均', 'B 轮均', 'GAP', 'GAP%', 'A P90', 'B P90',
                 'A 最大', 'B 最大', '进程数 A/B', '泄漏', '显著'],
                [[r['level'], _f(r['da']['mi']), _f(r['db']['mi']),
                  f"{r['gap']:+.2f}",
                  r['gapt'] or (f"{r['gapp']:+.1f}%"
                                if r['gapp'] is not None else '—'),
                  _f(r['da']['p90']), _f(r['db']['p90']),
                  _f(r['da']['max']), _f(r['db']['max']),
                  f"{r['ca']}/{r['cb']}",
                  ('A' if r['leak_a'] else '') + ('B' if r['leak_b'] else ''),
                  '✓' if r['sig'] else '']
                 for r in m['level_rows']]))
        else:
            L.append('无数据')
        L.append('')

    # ---- 6. 进程 GAP Top20（主口径，不含仅 Cached） ----
    L.append(f'## 6. 进程 GAP Top 20（{pm.upper()}，按 |GAP|，不含仅 Cached 进程）')
    L.append('')
    top20 = [mp['procs'][n] for n in mp['top']
             if not mp['procs'][n]['cached_only']][:20]
    if top20:
        L.append(_table(
            ['进程', '层级 A', '层级 B', '出现率 A', '出现率 B',
             '轮均 A', '轮均 B', 'GAP / GAP%', '标记'],
            [[p['name'], '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
              f"{p['da']['rate'] * 100:.0f}%", f"{p['db']['rate'] * 100:.0f}%",
              _f(p['da']['mi']), _f(p['db']['mi']),
              _gap_txt(p['gap'], p['gapp'], p['gapt']),
              _proc_marks(p)] for p in top20]))
    else:
        L.append('无数据')
    L.append('')

    # ---- 7. 独有进程与层级变化 ----
    L.append(f'## 7. 独有进程与层级变化（{pm.upper()}）')
    L.append('')
    ga = meta['gap_abs']
    only_rows = []
    for side, key, lab in (('a', 'only_a', '仅 A'), ('b', 'only_b', '仅 B')):
        for n in mp[key]:
            p = mp['procs'][n]
            if abs(p['gap']) < ga:
                continue
            lv = p['la'] if side == 'a' else p['lb']
            dd = p['da'] if side == 'a' else p['db']
            only_rows.append([n, lab, '、'.join(lv), f"{dd['k']}/{dd['n']}",
                              _f(dd['mi']), _f(dd['mean'])])
    L.append(f'独有进程（|GAP| ≥ {ga} MB）：')
    L.append('')
    if only_rows:
        L.append(_table(['进程', '归属', '所在层级', '出现次数', '轮均', '值均'],
                        only_rows))
    else:
        L.append('（无）')
    L.append('')
    ch = [mp['procs'][n] for n in mp['level_changed']]
    L.append(f'层级变化进程（{len(ch)} 个）：')
    L.append('')
    if ch:
        head = ['进程', '层级 A', '层级 B']
        if ch[0]['pra'] is not None:
            head += ['A 各轮', 'B 各轮']
        rows = []
        for p in ch:
            r = [p['name'], '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—']
            if p['pra'] is not None:
                r += [','.join(x or '·' for x in p['pra']),
                      ','.join(x or '·' for x in p['prb'])]
            rows.append(r)
        L.append(_table(head, rows))
    else:
        L.append('（无）')
    L.append('')

    # ---- 8. 自研 / Google 应用 ----
    ih = [p for p in mp['procs'].values() if p['inhouse']]
    ih.sort(key=lambda p: -abs(p['gap']))
    gg = [p for p in mp['procs'].values() if p['google']]
    gg.sort(key=lambda p: -abs(p['gap']))
    L.append(f'## 8. 自研 / Google 应用（{pm.upper()}）')
    L.append('')
    L.append('### 8.1 自研')
    L.append('')
    ihA = sum(p['da']['mi'] for p in ih)
    ihB = sum(p['db']['mi'] for p in ih)
    L.append(f"自研进程数：A {sum(1 for p in ih if p['da']['k'])} / "
             f"B {sum(1 for p in ih if p['db']['k'])}；合计轮均："
             f"A {_f(ihA)} MB / B {_f(ihB)} MB（{ihB - ihA:+.2f} MB）")
    L.append('')
    if ih:
        L.append(_table(['进程', '判定', '层级 A', '层级 B', '出现率 A/B',
                         '轮均 A', '轮均 B', 'GAP / GAP%', '常驻B', '泄漏'],
                        [[p['name'],
                          '白名单' if p['ih_src'] == 'whitelist' else '前缀',
                          '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                          f"{p['da']['rate'] * 100:.0f}%/"
                          f"{p['db']['rate'] * 100:.0f}%",
                          _f(p['da']['mi']), _f(p['db']['mi']),
                          _gap_txt(p['gap'], p['gapp'], p['gapt']),
                          '✓' if p['resident_b'] else '',
                          ('A' if p['leak_a'] else '')
                          + ('B' if p['leak_b'] else '')]
                         for p in ih[:50]]))
    else:
        L.append('（无）')
    L.append('')
    L.append('### 8.2 Google')
    L.append('')
    ggA = sum(p['da']['mi'] for p in gg)
    ggB = sum(p['db']['mi'] for p in gg)
    L.append(f"Google 进程数：A {sum(1 for p in gg if p['da']['k'])} / "
             f"B {sum(1 for p in gg if p['db']['k'])}；合计轮均："
             f"A {_f(ggA)} MB / B {_f(ggB)} MB（{ggB - ggA:+.2f} MB）")
    L.append('')
    if gg:
        L.append(_table(['进程', '层级 A', '层级 B', '出现率 A/B',
                         '轮均 A', '轮均 B', 'GAP / GAP%', '常驻B', '泄漏'],
                        [[p['name'],
                          '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                          f"{p['da']['rate'] * 100:.0f}%/"
                          f"{p['db']['rate'] * 100:.0f}%",
                          _f(p['da']['mi']), _f(p['db']['mi']),
                          _gap_txt(p['gap'], p['gapp'], p['gapt']),
                          '✓' if p['resident_b'] else '',
                          ('A' if p['leak_a'] else '')
                          + ('B' if p['leak_b'] else '')]
                         for p in gg[:50]]))
    else:
        L.append('（无）')
    L.append('')

    # ---- 9. 疑似泄漏 ----
    L.append('## 9. 疑似泄漏（趋势回归，提示非定论）')
    L.append('')
    if R['leaks']:
        L.append(_table(
            ['数据集', '类型', '口径', '对象', '头(中位)', '尾(中位)',
             'delta MB', '斜率 MB/h', 'R²'],
            [[lk['ds'], {'proc': '进程', 'level': '层级',
                         'sys': '整机', 'heap': '进程堆'}[lk['kind']],
              (lk['metric'] if lk['kind'] == 'heap'
               else lk['metric'].upper()) or '—', lk['name'],
              _f(lk['head']), _f(lk['tail']), f"{lk['delta']:+.1f}",
              f"{lk['slope']:+.2f}", f"{lk['r2']:.2f}"]
             for lk in R['leaks']]))
    else:
        L.append('（无）')
    L.append('')

    # ---- 10. 进程 meminfo 明细 ----
    L.append('## 10. 进程 meminfo 明细（dumpsys Pss Total，MB）')
    L.append('')
    am = R.get('appmem') or {}
    if am.get('procs'):
        cov = {'a': [0, 0], 'b': [0, 0]}   # [进程数, 明细点数]
        for p in am['procs'].values():
            for sd in ('a', 'b'):
                if p['cov_' + sd]:
                    cov[sd][0] += 1
                    cov[sd][1] += p['cov_' + sd]
        L.append(f"覆盖进程：A {cov['a'][0]} 个 / B {cov['b'][0]} 个；"
                 f"明细数据点（进程×轮）：A {cov['a'][1]} / B {cov['b'][1]}。"
                 '采集哪些进程由测试平台决定，逐轮可能不同。')
        heap_leaks = [lk for lk in R['leaks'] if lk['kind'] == 'heap']
        if heap_leaks:
            L.append('')
            L.append('堆疑似泄漏（Native Heap / Dalvik Heap 趋势回归）：')
            L.append('')
            L.append(_table(
                ['数据集', '进程', '指标', '头(中位)', '尾(中位)',
                 'delta MB', '斜率 MB/h', 'R²'],
                [[lk['ds'], lk['name'], lk['metric'], _f(lk['head']),
                  _f(lk['tail']), f"{lk['delta']:+.1f}",
                  f"{lk['slope']:+.2f}", f"{lk['r2']:.2f}"]
                 for lk in heap_leaks]))
        else:
            L.append('')
            L.append('堆疑似泄漏：无。')
        L.append('')
        L.append('逐轮明细见 data.xlsx「进程meminfo明细」、'
                 '堆趋势判定见「进程堆趋势」；HTML 进程详情页可交互查看。')
    else:
        L.append('测试平台没有采集进程级 dumpsys meminfo 明细。')
    L.append('')

    # ---- 11. 进程事件 ----
    L.append('## 11. 进程事件（test 阶段）')
    L.append('')
    ea, eb = R['events']['a'], R['events']['b']
    L.append('| 指标 | A | B |')
    L.append('|---|---|---|')
    L.append(f"| LMK 总次数 | {ea['lmk']['total']} | {eb['lmk']['total']} |")
    L.append(f"| LMK 次/小时 | {_f(ea['lmk']['per_hour'])} | "
             f"{_f(eb['lmk']['per_hour'])} |")
    L.append(f"| am_kill 次数 | {ea['am_kill']['total']} | "
             f"{eb['am_kill']['total']} |")
    L.append(f"| am_proc_died 次数 | {ea['died_total']} | {eb['died_total']} |")
    L.append(f"| am_proc_start 次数 | {ea['starts_total']} | "
             f"{eb['starts_total']} |")
    L.append(f"| ANR | {len(ea['anr'])} | {len(eb['anr'])} |")
    L.append(f"| crash | {len(ea['crash'])} | {len(eb['crash'])} |")
    L.append(f"| am_low_memory | {ea['lowmem_total']} | {eb['lowmem_total']} |")
    L.append(f"| 重启 | {len(dsA['reboots'])} | {len(dsB['reboots'])} |")
    L.append('')
    for lab, e, ds in (('A', ea, dsA), ('B', eb, dsB)):
        L.append(f'- {lab} {ev_src_text(ds, e)}')
        if ds['reason_counts']:
            L.append(f"- {lab} 启动原因分布：" + '；'.join(
                f"{r}×{n}" for r, n in sorted(ds['reason_counts'].items(),
                                             key=lambda x: -x[1])[:10]))
        if e['lmk']['by_reason']:
            L.append(f"- {lab} LMK 原因：" + '；'.join(
                f"{r['name']}×{r['n']}" for r in e['lmk']['by_reason'][:8]))
        if e['lmk']['by_level']:
            L.append(f"- {lab} LMK 层级：" + '；'.join(
                f"{k}×{v}" for k, v in sorted(e['lmk']['by_level'].items(),
                                             key=lambda x: -x[1])[:8]))
        if e['lmk']['top']:
            L.append(f"- {lab} LMK Top 进程：" + '；'.join(
                f"{t['proc']}×{t['n']}" for t in e['lmk']['top'][:10]))
        if e['am_kill']['by_reason']:
            L.append(f"- {lab} am_kill 原因 Top：" + '；'.join(
                f"{r['reason']}×{r['n']}" for r in e['am_kill']['by_reason'][:8]))
        if e['start_top']:
            L.append(f"- {lab} 反复拉起 Top：" + '；'.join(
                f"{t['proc']}×{t['n']}" for t in e['start_top'][:10]))
        for e2 in e['anr'][:10]:
            L.append(f"- {lab} ANR {e2['t']} `{e2['proc']}` {e2['reason']}")
        for e2 in e['crash'][:10]:
            L.append(f"- {lab} crash {e2['t']} `{e2['proc']}` {e2['reason']}")
        if ds['events'] is None:
            L.append(f"- {lab} 事件共 {ds['events_total']} 条，"
                     '数量过多未内嵌，明细见 data.xlsx')
    L.append('')

    # ---- 12. 内存压力 ----
    L.append('## 12. 内存压力（proc_vmstat 每轮增量）')
    L.append('')
    cs = R['pressure'].get('counters', [])
    if cs:
        L.append(_table(
            ['计数器', '单位', 'A 总量', 'A /小时', 'A P90/轮',
             'B 总量', 'B /小时', 'B P90/轮', 'GAP/小时', 'GAP%'],
            [[c['name'], c['unit'],
              _f(c['sa']['total'] if c['sa'] else None),
              _f(c['sa']['per_hour'] if c['sa'] else None),
              _f(c['sa']['p90'] if c['sa'] else None),
              _f(c['sb']['total'] if c['sb'] else None),
              _f(c['sb']['per_hour'] if c['sb'] else None),
              _f(c['sb']['p90'] if c['sb'] else None),
              _f(c['gap']), (f"{c['gapp']:+.1f}%"
                             if c['gapp'] is not None else '—')]
             for c in cs]))
    else:
        L.append('（本测试包无 proc_vmstat 数据）')
    L.append('')

    # ---- 13. 口径说明 ----
    L.append('## 13. 口径说明')
    L.append('')
    ih = R['inhouse']
    wl = (f"白名单 `{ih['whitelist_src']}`（{ih['whitelist_count']} 项）"
          if ih['whitelist_src'] else '未提供白名单')
    trend_min = 5 if kind == 'static' else 10
    L += [
        '- **RSS** 层级/进程：procStatus `VmRSS+VmSwap`，层级按 OomScore 映射'
        '（与平台 level_memory 汇总一致）；**PSS**：dumpsys meminfo '
        '`Total PSS by OOM adjustment`；整机字段来自 dumpsys 尾部与 '
        f'/proc/meminfo。主口径 {pm.upper()}。',
        '- 轮均 = 各轮合计 ÷ 总轮数（缺席按 0）；值均/最小/P50/P90/P95/最大 '
        '仅在出现轮上计算；出现率 = 出现轮数 ÷ 总轮数。',
        '- 百分位：线性插值 inclusive（numpy 默认 / Excel PERCENTILE.INC）。'
        '已与平台 level_memory/Exception_Summary_Level 完全对齐；'
        'all_memory_data.xls 尾部百分位与平台自身其它报表差 ~0.01-0.02'
        '（平台内部口径差异），本工具取插值法。',
        '- GAP = B − A（轮均）；GAP% = GAP/A；A=0 记「新增」，B=0 记「消失」。',
        f"- 显著差异：|GAP| ≥ {meta['gap_abs']} MB 且（单侧出现或 "
        f"|GAP|/A ≥ {meta['gap_pct']}%）。",
        '- 仅出现于 Cached 层的进程（cached_only）默认不计入差异排行；'
        'HTML 右上角开关可纳入。',
        '- 趋势：出现轮对测试开始时间（小时）线性回归（slope MB/h）；'
        f'head/tail = 前/后 10% 出现轮中位数，delta=tail-head；'
        f'出现轮 <{trend_min} 不判定。',
        f"- 进程泄漏：常驻（出现率 ≥ {meta['resident']}）且 delta ≥ "
        f"max({meta['leak_delta']}, head×{meta['leak_pct']}%) MB 且 "
        f"slope>0 且 R² ≥ {meta['leak_r2']}；层级/整机泄漏 delta 门限 "
        f"{meta['sys_leak_delta']} MB（Used RAM 升 / Free RAM、MemAvailable 降）。",
        '- 进程 meminfo 明细：round_N/dumpsys_meminfo_<proc>[_<pid>].txt '
        '明细表 Pss Total 列；行缺失按 0 计；进程堆趋势（Native/Dalvik '
        'Heap）按进程泄漏同款门限判定，覆盖率 = 明细覆盖轮/有效轮。',
        '- LMK 来自 events_log `killinfo`（rss=f4/1024 MB，kill_reason=f5）；'
        'reason 枚举按 AOSP LmkdStatsReporter（0-9），其它记 `厂商扩展(N)`；'
        '进程名按同 pid 最近 start 回填，缺失按 10s 内 died/kill 回退。',
        '- 无效轮：dumpsys_meminfo 缺失或 PSS by OOM 为空或 procStatus 缺失'
        '（含平台 failed_rounds.log 记录），不计入轮数，汇总为警告。',
        '- vmstat 压力：累计计数器逐轮差值；中间有重启或差值为负的间隔不计；'
        'pswpin/out 按 4KB/页换算 MB。',
        '- 时间轴：轮次少（≤30）时按轮类目轴，否则 x = 距测试开始的小时数；'
        'start 缺失按序号×平均间隔回退。',
        '- 事件阶段：boot=开机至第 1 轮前；test=测试窗口内；早于本次开机的日志已丢弃。',
        f"- 自研判定：{wl} + 前缀匹配 `{ih['prefixes_src']}`"
        f"（{len(ih['prefixes'])} 条）。",
        f"- Google 判定：前缀匹配 `{R['google']['prefixes_src']}`"
        f"（{len(R['google']['prefixes'])} 条），自研优先。",
    ]
    L.append('')
    for side, ds in ((0, dsA), (1, dsB)):
        if ds['warnings']:
            L.append(f"数据集 {'AB'[side]} 警告：" + '；'.join(ds['warnings']))
            L.append('')
    return '\n'.join(L)
