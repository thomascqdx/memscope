"""生成 Markdown 对比报告（中文）。数据源为 analyze() 的 result dict。"""
from __future__ import annotations


def _f(v):
    return '—' if v is None else f'{v:.2f}'


def _gap_txt(p):
    if p['gapt']:
        return f"{p['gap']:+.2f} / {p['gapt']}"
    return f"{p['gap']:+.2f} / {p['gapp']:+.1f}%"


def _table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |',
           '|' + '---|' * len(header)]
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def _sig_procs(m):
    return [m['procs'][n] for n in m['top']
            if m['procs'][n]['sig'] and not m['procs'][n]['cached_only']]


def _summary_lines(R):
    m = R['metrics']['pss']
    sysrows = {r['name']: r for r in R['system']}
    out = []
    fr, ur = sysrows['Free RAM'], sysrows['Used RAM']
    out.append(f"- **Free RAM**：A {_f(fr['ra'])} MB → B {_f(fr['rb'])} MB（{_gap_txt(fr)}）；"
               f"**Used RAM**：A {_f(ur['ra'])} MB → B {_f(ur['rb'])} MB（{_gap_txt(ur)}）")
    lg = [r for r in m['level_rows'] if r['level'] != 'Cached']
    lg.sort(key=lambda r: -abs(r['gap']))
    out.append('- 层级 GAP Top3（不含 Cached）：' + '；'.join(
        f"**{r['level']}** {_gap_txt(r)} MB" for r in lg[:3]))
    top5 = _sig_procs(m)[:5]
    if top5:
        out.append('- 差异显著进程 Top5：' + '；'.join(
            f"**{p['name']}** {_gap_txt(p)} MB" for p in top5))
    else:
        out.append('- 无差异显著进程')
    ng = '；'.join(f"**{e['name']}** {e['v']:.2f} MB" for e in m['new_top']) or '无'
    gn = '；'.join(f"**{e['name']}** {e['v']:.2f} MB" for e in m['gone_top']) or '无'
    out.append(f"- 新增进程 Top3（按轮次均值，含 Cached）：{ng}；"
               f"消失进程 Top3：{gn}")
    out.append(f"- 独有进程：仅 A **{len(m['only_a'])}** 个 / 仅 B **{len(m['only_b'])}** 个；"
               f"层级变化进程 **{len(m['level_changed'])}** 个")
    ih = [p for p in m['procs'].values() if p['inhouse']]
    ta = sum(p['ra'] for p in ih)
    tb = sum(p['rb'] for p in ih)
    out.append(f"- 自研应用合计（PSS 轮次均值）：A {_f(ta)} MB → B {_f(tb)} MB"
               f"（{tb - ta:+.2f} MB）")
    viol = []
    for side, lab in (('a', 'A'), ('b', 'B')):
        st = R['standards'].get(side)
        if not st:
            continue
        n = sum(1 for r in st['levels'] if r['viol'])
        n += sum(1 for r in st['procs'] if r['viol'])
        if st['free_ram'] and st['free_ram']['viol']:
            n += 1
        viol.append(f"{lab} {n} 项")
    out.append('- 标准超标（均值口径）：' + ('、'.join(viol) if viol else '无/无阈值'))
    if R['growth']:
        out.append(f"- 疑似增长（提示，非泄漏结论）：**{len(R['growth'])}** 项，详见第 11 节")
    else:
        out.append('- 未发现疑似增长项')
    return out


def _wake_rows(R, names):
    rows = []
    for side, lab in ((0, 'A'), (1, 'B')):
        evs = R['datasets'][side]['events']
        for name in names:
            starts = [e for e in evs if e['kind'] == 'start' and e['proc'] == name]
            if not starts:
                continue
            test = [e for e in starts if e['phase'] == 'test']
            reasons = {}
            callers = {}
            for e in starts:
                reasons[e['reason'] or '(无)'] = reasons.get(e['reason'] or '(无)', 0) + 1
                if e['caller']:
                    callers[e['caller']] = callers.get(e['caller'], 0) + 1
            top_r = sorted(reasons.items(), key=lambda x: -x[1])[:3]
            top_c = sorted(callers.items(), key=lambda x: -x[1])[:3]
            rows.append([name, lab,
                         f"{len(test)}/{len(starts)}",
                         '、'.join(f'{k}×{v}' for k, v in top_r) or '—',
                         '、'.join(f'{k}×{v}' for k, v in top_c) or '—'])
    return rows


def render(R: dict) -> str:
    la, lb = R['labels']
    m = R['metrics']['pss']
    meta = R['meta']
    L = []
    L.append('# 静态内存对比报告')
    L.append('')
    L.append(f"- 基线 A：**{la}**　对比 B：**{lb}**")
    L.append(f"- 轮次：A {R['datasets'][0]['n']} 轮 / B {R['datasets'][1]['n']} 轮；"
             f"显著性阈值：|GAP| ≥ {meta['gap_abs']} MB 且 ≥ {meta['gap_pct']}%")
    L.append('')
    L.append('## 1. 概要')
    L.append('')
    L += _summary_lines(R)
    L.append('')

    L.append('## 2. 设备与测试信息')
    L.append('')
    L.append(_table(['字段', 'A（基线）', 'B（对比）', ''],
                    [[d['field'], d['a'], d['b'], '**不同**' if d['diff'] else '']
                     for d in R['device_diff']]))
    L.append('')
    pd = R['pkg_diff']
    L.append(f"预装包：A {len(R['datasets'][0]['packages'])} 个 / B {len(R['datasets'][1]['packages'])} 个；"
             f"仅 A **{len(pd['a'])}** 个，仅 B **{len(pd['b'])}** 个")
    L.append('')
    if pd['a']:
        L.append('- 仅 A：' + '、'.join(f'`{p}`' for p in pd['a']))
    if pd['b']:
        L.append('- 仅 B：' + '、'.join(f'`{p}`' for p in pd['b']))
    L.append('')

    L.append('## 3. 整机内存对比（均值，MB）')
    L.append('')
    L.append(_table(['指标', '来源', 'A 均值', 'B 均值', 'GAP', 'GAP%',
                     'A 最小', 'A 最大', 'B 最小', 'B 最大'],
                    [[r['name'], r['src'], _f(r['ra']), _f(r['rb']),
                      f"{r['gap']:+.2f}",
                      r['gapt'] or f"{r['gapp']:+.1f}%",
                      _f(r['mina']), _f(r['maxa']), _f(r['minb']), _f(r['maxb'])]
                     for r in R['system']]))
    L.append('')

    L.append('## 4. 层级内存对比（PSS 轮次均值，MB）')
    L.append('')
    L.append(_table(['层级', 'A 均值', 'B 均值', 'GAP', 'GAP%', '进程数 A', '进程数 B'],
                    [[r['level'], _f(r['ra']), _f(r['rb']), f"{r['gap']:+.2f}",
                      r['gapt'] or f"{r['gapp']:+.1f}%", r['ca'], r['cb']]
                     for r in m['level_rows']]))
    L.append('')

    sig = _sig_procs(m)[:20]
    L.append('## 5. 差异显著进程（PSS，Top 20，按 |GAP|，不含仅 Cached 进程）')
    L.append('')
    L.append(_table(['进程', '层级 A', '层级 B', '出现 A', '出现 B',
                     '轮均 A', '轮均 B', 'GAP / GAP%'],
                    [[p['name'], '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                      f"{p['ka']}/{R['datasets'][0]['n']}",
                      f"{p['kb']}/{R['datasets'][1]['n']}",
                      _f(p['ra']), _f(p['rb']), _gap_txt(p)]
                     for p in sig]))
    L.append('')

    ga = R['meta']['gap_abs']
    only_rows = []
    for side, key, lab in (('a', 'only_a', '仅 A'), ('b', 'only_b', '仅 B')):
        for n in m[key]:
            p = m['procs'][n]
            if abs(p['gap']) < ga:
                continue
            lv = p['la'] if side == 'a' else p['lb']
            k = p['ka'] if side == 'a' else p['kb']
            rm = p['ra'] if side == 'a' else p['rb']
            vm = p['va'] if side == 'a' else p['vb']
            only_rows.append([n, lab, '、'.join(lv), k, _f(rm), _f(vm)])
    L.append(f'## 6. 独有进程（|GAP| ≥ {ga} MB）')
    L.append('')
    L.append(_table(['进程', '归属', '所在层级', '出现次数', '轮次均值', '数值均值'],
                    only_rows) if only_rows else '（无）')
    L.append('')

    L.append('## 7. 层级变化进程')
    L.append('')
    ch = [m['procs'][n] for n in m['level_changed']]
    L.append(_table(['进程', '层级 A（各轮）', '层级 B（各轮）'],
                    [[p['name'],
                      ','.join(x or '·' for x in p['pra']),
                      ','.join(x or '·' for x in p['prb'])]
                     for p in ch]) if ch else '（无）')
    L.append('')

    ih = sorted((p for p in m['procs'].values() if p['inhouse']),
                key=lambda p: -abs(p['gap']))
    ih_show = [p for p in ih if p['sig'] or p['ka'] == 0 or p['kb'] == 0]
    L.append('## 8. 自研应用')
    L.append('')
    L.append(f"自研进程数：A {sum(1 for p in ih if p['ka'])} / B {sum(1 for p in ih if p['kb'])}；"
             f"合计轮次均值：A {_f(sum(p['ra'] for p in ih))} MB / "
             f"B {_f(sum(p['rb'] for p in ih))} MB")
    L.append('')
    L.append('下表为差异显著或单侧独有的自研进程：')
    L.append('')
    L.append(_table(['进程', '判定', '层级 A', '层级 B', '轮均 A', '轮均 B', 'GAP / GAP%'],
                    [[p['name'], '白名单' if p['ih_src'] == 'whitelist' else '前缀',
                      '、'.join(p['la']) or '—', '、'.join(p['lb']) or '—',
                      _f(p['ra']), _f(p['rb']), _gap_txt(p)]
                     for p in ih_show]) if ih_show else '（无）')
    L.append('')

    L.append('## 9. 启动/唤醒（关联进程）')
    L.append('')
    names = sorted({p['name'] for p in sig} |
                   {r[0] for r in only_rows} |
                   {p['name'] for p in ih_show})
    names = names[:40]
    rows = _wake_rows(R, names)
    L.append(_table(['进程', '数据集', '启动次数 测试/全部', '主要原因', '调用方'],
                    rows) if rows else '（无相关事件）')
    L.append('')

    L.append('## 10. 标准对照（RSS 口径）')
    L.append('')
    for side, lab in (('a', 'A'), ('b', 'B')):
        st = R['standards'].get(side)
        L.append(f'数据集 {lab}：' + ('无阈值文件' if not st else ''))
        if not st:
            L.append('')
            continue
        if st['levels'] or st['free_ram']:
            rows = []
            if st['free_ram']:
                r = st['free_ram']
                rows.append(['Free RAM(下限)', _f(r['thr']), _f(r['mean']), _f(r['low']),
                             '超标' if r['viol'] else '达标',
                             '峰值超标' if r['peak_viol'] else '—'])
            for r in st['levels']:
                rows.append([r['name'], _f(r['thr']), _f(r['mean']), _f(r['peak']),
                             '超标' if r['viol'] else '达标',
                             '峰值超标' if r['peak_viol'] else '—'])
            L.append(_table(['层级', '阈值', '均值', '峰值(最低)', '均值判定', '峰值判定'], rows))
            L.append('')
        if st['procs']:
            L.append(_table(['进程', '层级', '阈值', '数值均值', '峰值', '均值判定', '峰值判定'],
                            [[r['name'], r['level'], _f(r['thr']), _f(r['mean']), _f(r['peak']),
                              '超标' if r['viol'] else '达标',
                              '峰值超标' if r['peak_viol'] else '—']
                             for r in st['procs']]))
            L.append('')

    L.append('## 11. 疑似增长（提示，非泄漏结论）')
    L.append('')
    if R['growth']:
        L.append(_table(['数据集', '对象', '类型', '斜率 MB/轮', 'R²'],
                        [[h['ds'], h['name'],
                          '进程(PSS)' if h['kind'] == 'proc' else '整机',
                          f"{h['slope']:+.2f}", f"{h['r2']:.2f}"]
                         for h in R['growth']]))
    else:
        L.append('（无）')
    L.append('')

    L.append('## 12. 口径说明')
    L.append('')
    ih = R['inhouse']
    wl = (f"白名单 `{ih['whitelist_src']}`（{ih['whitelist_count']} 项）"
          if ih['whitelist_src'] else '未提供白名单')
    L += [
        '- **PSS**：dumpsys meminfo `Total PSS by OOM adjustment`（含 swap）；'
        '**RSS**：procStatus `VmRSS+VmSwap`，层级按 OomScore 映射。',
        '- 出现次数 = 非空轮数 k/N；轮次均值 = 各轮合计 ÷ N（缺失按 0）；数值均值 = 仅出现轮均值。',
        '- GAP = B − A（轮次均值）；GAP% = GAP/A；A=0 记「新增」，B=0 记「消失」。',
        f"- 显著差异：|GAP| ≥ {meta['gap_abs']} MB 且（单侧出现或 |GAP|/A ≥ {meta['gap_pct']}%）。",
        '- 仅出现于 Cached 层的进程（cached_only）默认不计入差异显著/Top 榜单。',
        f"- 自研判定：{wl} + 前缀匹配 `{ih['prefixes_src']}`（{len(ih['prefixes'])} 条）。",
        '- 事件阶段：boot=开机至第 1 轮前；test=测试窗口内；早于本次开机的日志已丢弃。',
        f"- 疑似增长：逐轮线性回归，斜率 ≥ {meta['growth_slope']} MB/轮（整机 {meta['sys_growth_slope']}）且 R² ≥ {meta['growth_r2']}。",
    ]
    L.append('')
    for side, lab in ((0, 'A'), (1, 'B')):
        w = R['datasets'][side]['warnings']
        if w:
            L.append(f"数据集 {lab} 警告：" + '；'.join(w))
            L.append('')
    return '\n'.join(L)
