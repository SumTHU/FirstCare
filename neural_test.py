# -*- coding: utf-8 -*-
"""神经链路协议测试：stub + 管线 JS + 本地 mock 服务（Jev /v1/systemone
与智算云 /chat/completions），验证 Key 配置后的真实调用形状、解析与逐级回落。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "medir_triage_ui_real.html"
CUT_MARK = "/* ============ 会话状态 ============ */"

html = HTML.read_text(encoding="utf-8")
m = re.search(r"<script>(.*)</script>", html, re.S)
if not m:
    sys.exit("未找到 <script> 块")
js = m.group(1)
idx = js.find(CUT_MARK)
if idx < 0:
    sys.exit("未找到会话状态切分标记")

STUB = """global.document = {
  querySelector: () => ({addEventListener(){}, classList:{add(){},remove(){},toggle(){}},
                         setAttribute(){}, style:{}, focus(){}}),
  querySelectorAll: () => [], addEventListener(){}, documentElement:{style:{}}
};
"""
out = ROOT / "_neural_test.js"
out.write_text(STUB + js[:idx] + "\n" +
               (ROOT / "_neural_body.js").read_text(encoding="utf-8"), encoding="utf-8")
r = subprocess.run(["node", str(out)], capture_output=True, text=True,
                   cwd=str(ROOT), timeout=180)
print(r.stdout)
if r.returncode != 0:
    print("STDERR:", r.stderr[-3000:])
    sys.exit(1)
