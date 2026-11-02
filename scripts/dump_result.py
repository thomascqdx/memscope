"""回归基线：加载两个包 → analyze() → 落 JSON（不渲染）。

用法: python dump_result.py <static|dynamic> <A> <B> <out.json> [tmpdir]
通过 PYTHONPATH 选择被测代码（基线 worktree 或当前 src）。
"""
import json
import sys
import time

from memscope import analysis, cli, inhouse, loader


def main():
    kind, a, b, out = sys.argv[1:5]
    tmpdir = sys.argv[5] if len(sys.argv) > 5 else None
    t0 = time.time()
    dsa = loader.load_dataset(a, 'A', kind=kind, tmpdir=tmpdir)
    dsb = loader.load_dataset(b, 'B', kind=kind, tmpdir=tmpdir)
    dsa.label, dsb.label = cli._default_labels(dsa, dsb)
    whitelist, wl_src = inhouse.load_whitelist(None)
    prefixes, px_src = inhouse.load_prefixes(None)
    google_px, gp_src = inhouse.load_google_prefixes(None)
    d = analysis.default_options(kind)
    opts = analysis.Options(
        kind=kind, gap_abs=d['gap_abs'], gap_pct=d['gap_pct'],
        resident=d['resident'], leak_delta=d['leak_delta'],
        leak_pct=d['leak_pct'], leak_r2=d['leak_r2'],
        sys_leak_delta=d['sys_leak_delta'], rate_pct=d['rate_pct'],
        primary_metric=d['primary_metric'], trend_min=d['trend_min'],
        whitelist=whitelist, whitelist_src=wl_src,
        prefixes=tuple(prefixes), prefixes_src=px_src,
        google_prefixes=tuple(google_px), google_prefixes_src=gp_src)
    res = analysis.analyze(dsa, dsb, opts)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    print(f'done {out} in {time.time() - t0:.0f}s', file=sys.stderr)


if __name__ == '__main__':
    main()
