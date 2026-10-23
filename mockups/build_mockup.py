#!/usr/bin/env python3
"""Build a single-file offline mockup: inline proto.json + echarts into the template."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = HERE / "mockup_template.html"
ECHARTS = HERE / "vendor" / "echarts.min.js"
PROTO = ROOT / ".work" / "proto.json"
OUT = HERE / "static_compare_mockup.html"


def main():
    tpl = TEMPLATE.read_text(encoding="utf-8")
    echarts = ECHARTS.read_text(encoding="utf-8")
    proto = json.loads(PROTO.read_text(encoding="utf-8"))
    proto_js = json.dumps(proto, ensure_ascii=False, separators=(",", ":"))
    # prevent a literal </script> inside the JSON from breaking the script block
    proto_js = proto_js.replace("</", "<\\/")
    echarts = echarts.replace("</script", "<\\/script")

    if "//__ECHARTS_INLINE//" not in tpl:
        sys.exit("template missing //__ECHARTS_INLINE// placeholder")
    if "/*__PROTO_JSON__*/null" not in tpl:
        sys.exit("template missing /*__PROTO_JSON__*/null placeholder")

    html = tpl.replace("//__ECHARTS_INLINE//", echarts, 1)
    html = html.replace("/*__PROTO_JSON__*/null", proto_js, 1)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
