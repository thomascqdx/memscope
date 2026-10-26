"""渲染单文件离线 HTML 报告：内联 result JSON + echarts + 模板。"""
from __future__ import annotations

import importlib.resources
import json
from pathlib import Path


def _asset(name: str) -> str:
    return importlib.resources.files('memscope.assets').joinpath(name).read_text(encoding='utf-8')


def render(result: dict, out_path, template: str = 'report_template.html',
           title: str = None) -> Path:
    tpl = _asset(template)
    echarts = _asset('echarts.min.js')
    js = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    js = js.replace('</', '<\\/')
    echarts = echarts.replace('</script', '<\\/script')
    if title is None:
        title = f"静态内存对比分析 {result['labels'][0]} vs {result['labels'][1]}"
    html = (tpl.replace('//__ECHARTS_INLINE//', echarts, 1)
               .replace('/*__RESULT_JSON__*/null', js, 1)
               .replace('__TITLE__', title))
    out_path = Path(out_path)
    out_path.write_text(html, encoding='utf-8')
    return out_path
