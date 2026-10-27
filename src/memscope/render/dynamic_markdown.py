"""生成动态内存对比 Markdown 报告（中文）。数据源为 dynamic_analysis.analyze()。"""
from __future__ import annotations


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


def render(R: dict) -> str:
    la, lb = R['labels']
    meta = R['meta']
    dsA, dsB = R['datasets']
    mrss, mpss = R['metrics']['rss'], R['metrics']['pss']
    L = []
    L.append('# 动态内存对比报告')
    L.append('')
    L.append(f'- 基线 A：**{la}**　对比 B：**{lb}**')
    L.append(f"- 有效轮次：A {dsA['n']} / B {dsB['n']}；"
             f"测试时长：A {_fmt_hours(dsA)} h / B {_fmt_hours(dsB)} h；"
             f"显著性阈值：|GAP| ≥ {meta['gap_abs']} MB 且 ≥ {meta['gap_pct']}%")
    L.append('')

    # ---- 1. 测试信息 ----
    L.append('## 1. 测试信息')
    L.append('')
    L.append(_table(['字段', 'A（基线）', 'B（对比）', ''],
                    [[d['field'], d['a'], d['b'], '**不同**' if d['diff'] else '']
                     for d in R['device_diff']]))
    L.append('')
    pd = R['pkg_diff']
    L.append(f"预装包：A {len(dsA['packages'])} 个 / B {len(dsB['packages'])} 个；"
             f"仅 A **{len(pd['a'])}** 个，仅 B **{len(pd['b'])}** 个")
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
        plat = ds['platform'].get('dynamic_result', '—') or '—'
        ver = st['verdict'] if st else '—（无阈值）'
        items = '、'.join(st['fail_items']) if st and st['fail_items'] else '—'
        L.append(f'| {lab} | {plat} | {ver} | {items} |')
    L.append('')
    if R['findings']:
        L.append('自动结论（前 15 条，按严重度排序，完整列表见 HTML「风险」页）：')
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
            rows.append([r['name'], _f(r['thr']), _f(r['mean']),
                         _f(r['p90']) + '(P90)', _f(r['peak']),
                         '超标' if r['viol'] else '达标',
                         f"{r['exceed_rate'] * 100:.1f}%"])
        if rows:
            L.append(_table(['指标', '阈值', '均值', '分位', '峰值',
                             '均值判定', '超标轮比例'], rows))
            L.append('')
        if st['procs']:
            L.append(_table(['进程', '层级', '阈值', '均值', '峰值',
                             '均值判定', '超标轮比例'],
                            [[r['name'], r['level'], _f(r['thr']),
                              _f(r['mean']), _f(r['peak']),
                              '超标' if r['viol'] else '达标',
                              f"{r['exceed_rate'] * 100:.1f}%"]
                             for r in st['procs']]))
            L.append('')

    # ---- 4. 整机内存 ----
    L.append('## 4. 整机内存（出现轮均值，MB）')
    L.append('')
    L.append(_table(['指标', '来源', 'A 均值', 'B 均值', 'GAP', 'GAP%',
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
    L.append('')

    # ---- 5. 层级内存 ----
    for tag, m in (('RSS', mrss), ('PSS', mpss)):
        L.append(f'## 5. 层级内存对比（{tag}，mi=轮次均值 MB）')
        L.append('')
        L.append(_table(
            ['层级', 'A mi', 'B mi', 'GAP', 'GAP%', 'A P90', 'B P90',
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
        L.append('')

    # ---- 6. 进程 GAP Top20（RSS） ----
    L.append('## 6. 进程 GAP Top 20（RSS，按 |GAP|，mi=轮次均值）')
    L.append('')
    top20 = [mrss['procs'][n] for n in mrss['top'][:20]]
    L.append(_table(
        ['进程', '层级 A', '层级 B', '出现率 A', '出现率 B',
         'mi A', 'mi B', 'GAP / GAP%', '标记'],
        [[p['name'], '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
          f"{p['da']['rate'] * 100:.0f}%", f"{p['db']['rate'] * 100:.0f}%",
          _f(p['da']['mi']), _f(p['db']['mi']),
          _gap_txt(p['gap'], p['gapp'], p['gapt']),
          _proc_marks(p)] for p in top20]))
    L.append('')

    # ---- 7. 疑似泄漏 ----
    L.append('## 7. 疑似泄漏（趋势回归，提示非定论）')
    L.append('')
    if R['leaks']:
        L.append(_table(
            ['数据集', '类型', '口径', '对象', '头(中位)', '尾(中位)',
             'delta MB', '斜率 MB/h', 'R²'],
            [[lk['ds'], {'proc': '进程', 'level': '层级',
                         'sys': '整机'}[lk['kind']],
              lk['metric'].upper() or '—', lk['name'],
              _f(lk['head']), _f(lk['tail']), f"{lk['delta']:+.1f}",
              f"{lk['slope']:+.2f}", f"{lk['r2']:.2f}"]
             for lk in R['leaks']]))
    else:
        L.append('（无）')
    L.append('')

    # ---- 8. 查杀与稳定性 ----
    L.append('## 8. 查杀与稳定性（test 阶段）')
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
    for lab, e in (('A', ea), ('B', eb)):
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
    L.append('')

    # ---- 9. 内存压力 ----
    L.append('## 9. 内存压力（proc_vmstat 每轮增量）')
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
        L.append('（无 vmstat 数据）')
    L.append('')

    # ---- 10. 自研 / Google 进程 ----
    ih = [p for p in mrss['procs'].values() if p['inhouse']]
    ih.sort(key=lambda p: -abs(p['gap']))
    gg = [p for p in mrss['procs'].values() if p['google']]
    gg.sort(key=lambda p: -abs(p['gap']))
    L.append('## 10. 自研 / Google 进程（RSS）')
    L.append('')
    L.append('### 10.1 自研')
    L.append('')
    ihA = sum(p['da']['mi'] for p in ih)
    ihB = sum(p['db']['mi'] for p in ih)
    L.append(f"自研进程数：{len(ih)}；合计 mi：A {_f(ihA)} MB / "
             f"B {_f(ihB)} MB（{ihB - ihA:+.2f} MB）")
    L.append('')
    L.append(_table(['进程', '判定', '层级 A', '层级 B', 'mi A', 'mi B',
                     'GAP / GAP%', '常驻B', '泄漏'],
                    [[p['name'],
                      '白名单' if p['ih_src'] == 'whitelist' else '前缀',
                      '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                      _f(p['da']['mi']), _f(p['db']['mi']),
                      _gap_txt(p['gap'], p['gapp'], p['gapt']),
                      '✓' if p['resident_b'] else '',
                      ('A' if p['leak_a'] else '')
                      + ('B' if p['leak_b'] else '')]
                     for p in ih[:50]]) if ih else '（无）')
    L.append('')
    L.append('### 10.2 Google')
    L.append('')
    ggA = sum(p['da']['mi'] for p in gg)
    ggB = sum(p['db']['mi'] for p in gg)
    L.append(f"Google 进程数：{len(gg)}；合计 mi：A {_f(ggA)} MB / "
             f"B {_f(ggB)} MB（{ggB - ggA:+.2f} MB）")
    L.append('')
    L.append(_table(['进程', '层级 A', '层级 B', 'mi A', 'mi B',
                     'GAP / GAP%', '常驻B', '泄漏'],
                    [[p['name'],
                      '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                      _f(p['da']['mi']), _f(p['db']['mi']),
                      _gap_txt(p['gap'], p['gapp'], p['gapt']),
                      '✓' if p['resident_b'] else '',
                      ('A' if p['leak_a'] else '')
                      + ('B' if p['leak_b'] else '')]
                     for p in gg[:50]]) if gg else '（无）')
    L.append('')

    # ---- 11. 口径说明 ----
    L.append('## 11. 口径说明')
    L.append('')
    L += [
        '- **RSS** 层级/进程：procStatus `VmRSS+VmSwap`，层级按 OomScore 映射'
        '（与平台 level_memory 汇总一致）；**PSS**：dumpsys meminfo '
        '`Total PSS by OOM adjustment`；整机字段来自 dumpsys 尾部与 '
        '/proc/meminfo。默认口径 RSS。',
        '- mi（Mean Influence）= 各轮合计 ÷ 总轮数（缺席按 0）；mean/min/P50/'
        'P90/P95/max 仅在出现轮上计算。',
        '- 百分位：线性插值 inclusive（numpy 默认 / Excel PERCENTILE.INC）。'
        '已与平台 level_memory/Exception_Summary_Level 完全对齐；'
        'all_memory_data.xls 尾部百分位与平台自身其它报表差 ~0.01-0.02'
        '（平台内部口径差异），本工具取插值法。',
        f"- 趋势：出现轮对测试小时数线性回归（slope MB/h）；head/tail = "
        f"前/后 10% 出现轮中位数，delta=tail-head；出现轮 <10 不判定。",
        f"- 进程泄漏：常驻（出现率 ≥ {meta['resident']}）且 delta ≥ "
        f"max({meta['leak_delta']}, head×{meta['leak_pct']}%) MB 且 "
        f"slope>0 且 R² ≥ {meta['leak_r2']}；层级/整机泄漏 delta 门限 "
        f"{meta['sys_leak_delta']} MB（Used RAM 升 / Free RAM、MemAvailable 降）。",
        '- LMK 来自 events_log `killinfo`（rss=f4/1024 MB，kill_reason=f5）；'
        'reason 枚举按 AOSP LmkdStatsReporter（0-9），其它记 `厂商扩展(N)`；'
        '进程名按同 pid 最近 start 回填，缺失按 10s 内 died/kill 回退。',
        '- 无效轮：dumpsys_meminfo 缺失或 PSS by OOM 为空或 procStatus 缺失'
        '（含平台 failed_rounds.log 记录），不计入轮数，汇总为警告。',
        '- vmstat 压力：累计计数器逐轮差值；中间有重启或差值为负的间隔不计；'
        'pswpin/out 按 4KB/页换算 MB。',
        '- 时间轴：x = 距测试开始的小时数；start 缺失按序号×平均间隔回退。',
        f"- Google 判定：前缀匹配 `{R['google']['prefixes_src']}`"
        f"（{len(R['google']['prefixes'])} 条），自研优先。",
    ]
    L.append('')
    for side, ds in ((0, dsA), (1, dsB)):
        if ds['warnings']:
            L.append(f"数据集 {'AB'[side]} 警告：" + '；'.join(ds['warnings']))
            L.append('')
    return '\n'.join(L)


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
    if p['inhouse']:
        marks.append('自研')
    if p['google']:
        marks.append('Google')
    if p['cached_only']:
        marks.append('Cached')
    return '、'.join(marks)
