"""memscope static compare BASE TARGET —— 生成 report.md / report.html / data.xlsx。"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

from . import analysis, inhouse, loader
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
    c.add_argument('--gap-abs', type=float, default=5.0, help='显著差异绝对阈值 MB（默认 5）')
    c.add_argument('--gap-pct', type=float, default=10.0, help='显著差异百分比阈值 %%（默认 10）')
    c.add_argument('--growth-slope', type=float, default=1.0, help='进程增长斜率阈值 MB/轮（默认 1）')
    c.add_argument('--sys-growth-slope', type=float, default=5.0, help='整机增长斜率阈值 MB/轮（默认 5）')
    c.add_argument('--growth-r2', type=float, default=0.6, help='增长判定 R2 阈值（默认 0.6）')

    dp = sub.add_parser('dynamic', help='动态内存测试包（尚未实现）')
    dsub = dp.add_subparsers(dest='cmd', required=True, metavar='{compare}')
    dc = dsub.add_parser('compare', help='对比两个动态内存测试包（尚未实现）')
    _add_compare_args(dc)
    return p


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, 'reconfigure'):
                s.reconfigure(errors='replace')
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if args.test_type == 'dynamic':
        if args.cmd == 'compare':
            print('[memscope] 动态内存分析尚未实现', file=sys.stderr)
            return 2
        return 2
    if args.cmd == 'compare':
        return _cmd_static_compare(args)
    return 2


def _cmd_static_compare(args):
    try:
        whitelist, wl_src = inhouse.load_whitelist(args.whitelist)
        prefixes, px_src = inhouse.load_prefixes(args.prefixes)
    except FileNotFoundError as e:
        print(f'[memscope] {e}', file=sys.stderr)
        return 2

    try:
        dsa = loader.load_dataset(args.base, 'A')
        dsb = loader.load_dataset(args.target, 'B')
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
        prefixes=tuple(prefixes), prefixes_src=px_src)

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
