"""memscope {static,dynamic} compare BASE TARGET —— 生成 report.md / report.html / data.xlsx。"""
from __future__ import annotations

import argparse
import re
import sys
import time
import zipfile
from pathlib import Path

from . import analysis, dynamic_analysis, inhouse, loader
from .render import dynamic_excel, dynamic_markdown, excel, html, markdown


def _sanitize(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('._') or 'unnamed'


def _default_labels(dsa, dsb):
    la = dsa.device.get('Rom Version') or Path(dsa.path).stem
    lb = dsb.device.get('Rom Version') or Path(dsb.path).stem
    if la == lb:
        la += '#' + (dsa.device.get('SN Number') or 'A')
        lb += '#' + (dsb.device.get('SN Number') or 'B')
    return la, lb


def _add_compare_args(c):
    c.add_argument('base', help='基线 A：平台 zip 或已解压目录')
    c.add_argument('target', help='对比 B：平台 zip 或已解压目录')


def build_parser():
    p = argparse.ArgumentParser(
        prog='memscope',
        description='Android 内存健康测试包 A/B 对比分析工具')
    sub = p.add_subparsers(dest='test_type', required=True,
                           metavar='{static,dynamic}',
                           help='测试类型')

    sp = sub.add_parser('static', help='静态内存测试包（整机静置采集）')
    ssub = sp.add_subparsers(dest='cmd', required=True, metavar='{compare}')
    c = ssub.add_parser('compare', help='对比两个静态内存测试包，生成报告')
    _add_compare_args(c)
    c.add_argument('-o', '--out', default=None,
                   help='输出目录（默认 out/static/<A>_vs_<B>）')
    c.add_argument('--labels', nargs=2, metavar=('A', 'B'), help='自定义两侧标签')
    c.add_argument('--whitelist', help='自研白名单文件（默认 config/internal_dev_package.txt，若存在）')
    c.add_argument('--prefixes', help='自研包名前缀文件（默认 config/inhouse_prefixes.txt 或内置）')
    c.add_argument('--google-prefixes', help='Google 包名前缀文件（默认 config/google_prefixes.txt 或内置）')
    c.add_argument('--gap-abs', type=float, default=5.0, help='显著差异绝对阈值 MB（默认 5）')
    c.add_argument('--gap-pct', type=float, default=10.0, help='显著差异百分比阈值 %%（默认 10）')
    c.add_argument('--growth-slope', type=float, default=1.0, help='进程增长斜率阈值 MB/轮（默认 1）')
    c.add_argument('--sys-growth-slope', type=float, default=5.0, help='整机增长斜率阈值 MB/轮（默认 5）')
    c.add_argument('--growth-r2', type=float, default=0.6, help='增长判定 R2 阈值（默认 0.6）')
    c.add_argument('--tmpdir', default=None, help='临时文件目录（嵌套包解压物化位置，默认系统临时目录）')

    dp = sub.add_parser('dynamic', help='动态内存测试包（长衰 monkey 测试）')
    dsub = dp.add_subparsers(dest='cmd', required=True, metavar='{compare}')
    dc = dsub.add_parser('compare', help='对比两个动态内存测试包，生成报告')
    _add_compare_args(dc)
    dc.add_argument('-o', '--out', default=None,
                    help='输出目录（默认 out/dynamic/<A>_vs_<B>）')
    dc.add_argument('--labels', nargs=2, metavar=('A', 'B'), help='自定义两侧标签')
    dc.add_argument('--whitelist', help='自研白名单文件（默认 config/internal_dev_package.txt，若存在）')
    dc.add_argument('--prefixes', help='自研包名前缀文件（默认 config/inhouse_prefixes.txt 或内置）')
    dc.add_argument('--google-prefixes', help='Google 包名前缀文件（默认 config/google_prefixes.txt 或内置）')
    dc.add_argument('--gap-abs', type=float, default=5.0, help='显著差异绝对阈值 MB（默认 5）')
    dc.add_argument('--gap-pct', type=float, default=10.0, help='显著差异百分比阈值 %%（默认 10）')
    dc.add_argument('--resident', type=float, default=0.9, help='常驻出现率阈值（默认 0.9）')
    dc.add_argument('--leak-delta', type=float, default=10.0, help='进程泄漏 delta 门限 MB（默认 10）')
    dc.add_argument('--leak-pct', type=float, default=20.0, help='进程泄漏 delta 相对门限 %%（默认 20）')
    dc.add_argument('--leak-r2', type=float, default=0.3, help='泄漏判定 R2 阈值（默认 0.3）')
    dc.add_argument('--sys-leak-delta', type=float, default=100.0, help='层级/整机泄漏 delta 门限 MB（默认 100）')
    dc.add_argument('--rate-pct', type=float, default=20.0, help='LMK/压力频率对比阈值 %%（默认 20）')
    dc.add_argument('--tmpdir', default=None, help='临时文件目录（嵌套包解压物化位置，默认系统临时目录）')
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
        if args.test_type == 'dynamic':
            return _cmd_dynamic_compare(args)
        return _cmd_static_compare(args)
    return 2


def _cmd_static_compare(args):
    try:
        whitelist, wl_src = inhouse.load_whitelist(args.whitelist)
        prefixes, px_src = inhouse.load_prefixes(args.prefixes)
        google_px, gp_src = inhouse.load_google_prefixes(args.google_prefixes)
    except FileNotFoundError as e:
        print(f'[memscope] {e}', file=sys.stderr)
        return 2

    try:
        dsa = loader.load_dataset(args.base, 'A', tmpdir=args.tmpdir)
        dsb = loader.load_dataset(args.target, 'B', tmpdir=args.tmpdir)
    except loader.LoadError as e:
        print(f'[memscope] {e}', file=sys.stderr)
        return 2
    except zipfile.BadZipFile as e:
        print(f'[memscope] 嵌套 zip 损坏: {e}', file=sys.stderr)
        return 2

    for ds in (dsa, dsb):
        err = loader.check_test_type(ds, 'static')
        if err:
            print(f'[memscope] {err}', file=sys.stderr)
            return 2

    if args.labels:
        la, lb = args.labels
    else:
        la, lb = _default_labels(dsa, dsb)
    dsa.label, dsb.label = la, lb

    opts = analysis.Options(
        gap_abs=args.gap_abs, gap_pct=args.gap_pct,
        growth_slope=args.growth_slope, sys_growth_slope=args.sys_growth_slope,
        growth_r2=args.growth_r2,
        whitelist=whitelist, whitelist_src=wl_src,
        prefixes=tuple(prefixes), prefixes_src=px_src,
        google_prefixes=tuple(google_px), google_prefixes_src=gp_src)

    result = analysis.analyze(dsa, dsb, opts)
    md = markdown.render(result)
    result['report_md'] = md

    out = (Path(args.out) if args.out
           else Path('out') / 'static' / f'{_sanitize(la)}_vs_{_sanitize(lb)}')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.md').write_text(md, encoding='utf-8')
    html.render(result, out / 'report.html')
    excel.render(result, out / 'data.xlsx')

    for side, ds in (('A', dsa), ('B', dsb)):
        for w in ds.warnings:
            print(f'[memscope] 警告[{side}]: {w}', file=sys.stderr)
    print(f'[memscope] 输出目录: {out}')
    print(f'[memscope]   report.md / report.html / data.xlsx')
    return 0


def _cmd_dynamic_compare(args):
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
        dsa = loader.load_dataset(args.base, 'A', kind='dynamic',
                                  tmpdir=args.tmpdir)
        print(f'[memscope]   有效轮次 {dsa.n}，耗时 {time.time() - t0:.0f} s',
              file=sys.stderr)
        t0 = time.time()
        print(f'[memscope] 加载 B: {args.target}', file=sys.stderr)
        dsb = loader.load_dataset(args.target, 'B', kind='dynamic',
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
        err = loader.check_test_type(ds, 'dynamic')
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
    opts = dynamic_analysis.DynOptions(
        gap_abs=args.gap_abs, gap_pct=args.gap_pct,
        resident=args.resident, leak_delta=args.leak_delta,
        leak_pct=args.leak_pct, leak_r2=args.leak_r2,
        sys_leak_delta=args.sys_leak_delta, rate_pct=args.rate_pct,
        whitelist=whitelist, whitelist_src=wl_src,
        prefixes=tuple(prefixes), prefixes_src=px_src,
        google_prefixes=tuple(google_px), google_prefixes_src=gp_src)
    result = dynamic_analysis.analyze(dsa, dsb, opts)
    md = dynamic_markdown.render(result)
    result['report_md'] = md
    print(f'[memscope]   耗时 {time.time() - t0:.0f} s', file=sys.stderr)

    t0 = time.time()
    print('[memscope] 渲染…', file=sys.stderr)
    out = (Path(args.out) if args.out
           else Path('out') / 'dynamic' / f'{_sanitize(la)}_vs_{_sanitize(lb)}')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.md').write_text(md, encoding='utf-8')
    html.render(result, out / 'report.html',
                template='report_dynamic_template.html',
                title=f'动态内存对比分析 {la} vs {lb}')
    dynamic_excel.render(result, out / 'data.xlsx')
    print(f'[memscope]   耗时 {time.time() - t0:.0f} s', file=sys.stderr)

    for side, ds in (('A', dsa), ('B', dsb)):
        for w in ds.warnings:
            print(f'[memscope] 警告[{side}]: {w}', file=sys.stderr)
    print(f'[memscope] 输出目录: {out}')
    print(f'[memscope]   report.md / report.html / data.xlsx')
    return 0
