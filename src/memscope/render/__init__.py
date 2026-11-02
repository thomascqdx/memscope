"""渲染器共享展示文本：芯片中文名 / 跨模型提示 / 事件来源行 / 标准层级名。"""
_CHIP_CN = {'mtk': 'MTK', 'unisoc': '展锐', 'qcom': '高通'}
_ES_CN = {'mtk_mobilelog': 'MTK mobilelog',
          'mtk_debuglogger': 'MTK debuglogger',
          'unisoc_ylog': '展锐 ylog',
          'bugreport': 'bugreport（仅覆盖测试末段）'}


def chip_cn(chip):
    return _CHIP_CN.get(chip, chip or '未知')


def cross_notices(R):
    dsA, dsB = R['datasets']
    out = []
    if R['meta'].get('cross_model'):
        out.append(f"注意：两侧体验模型不同（A {dsA.get('model')} / "
                   f"B {dsB.get('model')}），标准阈值、层级口径与采集方式不同，"
                   '对比结论仅供参考。')
    if R['meta'].get('cross_chip'):
        out.append(f"注意：两侧芯片平台不同（A {chip_cn(dsA.get('chip'))} / "
                   f"B {chip_cn(dsB.get('chip'))}），Native 进程构成差异大，"
                   '进程级对比仅供参考。')
    return out


def ev_src_text(ds, ev):
    """事件来源行；无事件 → '事件来源：无事件日志'。"""
    es = ds.get('event_source') or {}
    k = es.get('kind')
    if not k or not (ds.get('events_total') or es.get('events')):
        return '事件来源：无事件日志'
    t = (f"事件来源：{_ES_CN.get(k, k)}（{es.get('files', 0)} 文件，"
         f"过滤后 {es.get('lines', 0)} 行，事件 {es.get('events', 0)} 条")
    if es.get('first') and es.get('last'):
        t += f"，测试期覆盖 {str(es['first'])[:16]} ~ {str(es['last'])[:16]}"
    t += '）'
    if ds.get('lmk_total') is not None:
        t += (f"；lmkcnt 累计 {ds['lmk_total']} 次 / killinfo 解析 "
              f"{ev['lmk']['total']} 次")
    evh, ht = ev.get('ev_hours'), ds.get('hours_total')
    if evh is not None and ht is not None and abs(evh - ht) > 0.05:
        t += f"；LMK 频率按事件覆盖时长 {evh:.1f} h 计"
    return t


def std_level_name(r):
    """len(members)>1 → 'Persistent（含 Persistent Service）'。"""
    mem = r.get('members') or []
    extra = [m for m in mem if m != r['name']]
    return r['name'] + (f"（含 {'、'.join(extra)}）" if extra else '')
