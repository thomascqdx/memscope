"""memscope {static,dynamic} compare BASE TARGET —— 生成 report.md / report.html / data.xlsx。"""
from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
from pathlib import Path

from . import __version__, analysis, inhouse, loader
from .render import excel, html, markdown


def _sanitize(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('._') or 'unnamed'


def _default_labels(dsa, dsb):
    la = dsa.device.get('Rom Version') or Path(dsa.path).stem
    lb = dsb.device.get('Rom Version') or Path(dsb.path).stem
    if la == lb:
        la += '#' + (dsa.device.get('SN Number') or 'A')
        lb += '#' + (dsb.device.get('SN Number') or 'B')
    return la, lb


def _add_compare_args(c, kind):
    d = analysis.default_options(kind)
    cn = '静态' if kind == 'static' else '动态'
    c.add_argument('base', help='基线 A：平台 zip/rar 或已解压目录')
    c.add_argument('target', help='对比 B：平台 zip/rar 或已解压目录')
    c.add_argument('-o', '--out', default=None,
                   help=f'输出目录（默认 out/{kind}/<A>_vs_<B>）')
    c.add_argument('--labels', nargs=2, metavar=('A', 'B'), help='自定义两侧标签')
    c.add_argument('--whitelist', help='自研白名单文件（默认 config/internal_dev_package.txt，若存在）')
    c.add_argument('--prefixes', help='自研包名前缀文件（默认 config/inhouse_prefixes.txt 或内置）')
    c.add_argument('--google-prefixes', help='Google 包名前缀文件（默认 config/google_prefixes.txt 或内置）')
    c.add_argument('--gap-abs', type=float, default=d['gap_abs'],
                   help=f"显著差异绝对阈值 MB（默认 {d['gap_abs']:g}）")
    c.add_argument('--gap-pct', type=float, default=d['gap_pct'],
                   help=f"显著差异百分比阈值 %%（默认 {d['gap_pct']:g}）")
    c.add_argument('--resident', type=float, default=d['resident'],
                   help=f"常驻出现率阈值（默认 {d['resident']:g}，{cn}）")
    c.add_argument('--leak-delta', type=float, default=d['leak_delta'],
                   help=f"进程泄漏 delta 门限 MB（默认 {d['leak_delta']:g}）")
    c.add_argument('--leak-pct', type=float, default=d['leak_pct'],
                   help=f"进程泄漏 delta 相对门限 %%（默认 {d['leak_pct']:g}）")
    c.add_argument('--leak-r2', type=float, default=d['leak_r2'],
                   help=f"泄漏判定 R2 阈值（默认 {d['leak_r2']:g}）")
    c.add_argument('--sys-leak-delta', type=float, default=d['sys_leak_delta'],
                   help=f"层级/整机泄漏 delta 门限 MB（默认 {d['sys_leak_delta']:g}）")
    c.add_argument('--rate-pct', type=float, default=d['rate_pct'],
                   help=f"LMK/压力频率对比阈值 %%（默认 {d['rate_pct']:g}）")
    c.add_argument('--tmpdir', default=None,
                   help='临时文件目录（嵌套包解压物化位置，默认系统临时目录）')


def build_parser():
    p = argparse.ArgumentParser(
        prog='memscope',
        description='Android 内存健康测试包 A/B 对比分析工具')
    p.add_argument('-V', '--version', action='version',
                   version=f'memscope {__version__}')
    sub = p.add_subparsers(dest='test_type', required=True,
                           metavar='{static,dynamic}',
                           help='测试类型')

    sp = sub.add_parser('static', help='静态内存测试包（整机静置采集）')
    ssub = sp.add_subparsers(dest='cmd', required=True, metavar='{compare}')
    c = ssub.add_parser('compare', help='对比两个静态内存测试包，生成报告')
    _add_compare_args(c, 'static')

    dp = sub.add_parser('dynamic', help='动态内存测试包（长衰 monkey 测试）')
    dsub = dp.add_subparsers(dest='cmd', required=True, metavar='{compare}')
    dc = dsub.add_parser('compare', help='对比两个动态内存测试包，生成报告')
    _add_compare_args(dc, 'dynamic')
    return p


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, 'reconfigure'):
                s.reconfigure(errors='replace')
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if args.cmd == 'compare':
        return _cmd_compare(args, args.test_type)
    return 2


def _cmd_compare(args, kind):
    print(f'[memscope] memscope {__version__}', file=sys.stderr)
    try:
        whitelist, wl_src = inhouse.load_whitelist(args.whitelist)
        prefixes, px_src = inhouse.load_prefixes(args.prefixes)
        google_px, gp_src = inhouse.load_google_prefixes(args.google_prefixes)
    except FileNotFoundError as e:
        print(f'[memscope] {e}', file=sys.stderr)
        return 2

    try:
        t0 = time.time()
        print(f'[memscope] 加载 A: {args.base}', file=sys.stderr)
        dsa = loader.load_dataset(args.base, 'A', kind=kind,
                                  tmpdir=args.tmpdir)
        print(f'[memscope]   有效轮次 {dsa.n}，耗时 {time.time() - t0:.0f} s',
              file=sys.stderr)
        t0 = time.time()
        print(f'[memscope] 加载 B: {args.target}', file=sys.stderr)
        dsb = loader.load_dataset(args.target, 'B', kind=kind,
                                  tmpdir=args.tmpdir)
        print(f'[memscope]   有效轮次 {dsb.n}，耗时 {time.time() - t0:.0f} s',
              file=sys.stderr)
    except loader.LoadError as e:
        print(f'[memscope] {e}', file=sys.stderr)
        return 2
    except zipfile.BadZipFile as e:
        print(f'[memscope] 嵌套 zip 损坏: {e}', file=sys.stderr)
        return 2

    for ds in (dsa, dsb):
        err = loader.check_test_type(ds, kind)
        if err:
            print(f'[memscope] {err}', file=sys.stderr)
            return 2

    if args.labels:
        la, lb = args.labels
    else:
        la, lb = _default_labels(dsa, dsb)
    dsa.label, dsb.label = la, lb

    t0 = time.time()
    print('[memscope] 分析…', file=sys.stderr)
    d = analysis.default_options(kind)
    opts = analysis.Options(
        kind=kind, gap_abs=args.gap_abs, gap_pct=args.gap_pct,
        resident=args.resident, leak_delta=args.leak_delta,
        leak_pct=args.leak_pct, leak_r2=args.leak_r2,
        sys_leak_delta=args.sys_leak_delta, rate_pct=args.rate_pct,
        primary_metric=d['primary_metric'], trend_min=d['trend_min'],
        whitelist=whitelist, whitelist_src=wl_src,
        prefixes=tuple(prefixes), prefixes_src=px_src,
        google_prefixes=tuple(google_px), google_prefixes_src=gp_src)
    result = analysis.analyze(dsa, dsb, opts)
    md = markdown.render(result)
    result['report_md'] = md
    print(f'[memscope]   耗时 {time.time() - t0:.0f} s', file=sys.stderr)

    t0 = time.time()
    print('[memscope] 渲染…', file=sys.stderr)
    out = (Path(args.out) if args.out
           else Path('out') / kind / f'{_sanitize(la)}_vs_{_sanitize(lb)}')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.md').write_text(md, encoding='utf-8')
    html.render(result, out / 'report.html')
    excel.render(result, out / 'data.xlsx')
    print(f'[memscope]   耗时 {time.time() - t0:.0f} s', file=sys.stderr)

    for side, ds in (('A', dsa), ('B', dsb)):
        for w in ds.warnings:
            print(f'[memscope] 警告[{side}]: {w}', file=sys.stderr)
    print(f'[memscope] 输出目录: {out}')
    print(f'[memscope]   report.md / report.html / data.xlsx')
    return 0
