"""把 index.html 里的 <script> 抽出来做语法检查（node --check）。

理由：浏览器的报错只在运行时出现，而语法错误会让整页白屏。
先离线卡一遍语法，再上浏览器验证交互。
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent

html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
m = re.search(r"<script>(.*?)</script>", html, re.S)
if not m:
    print("没找到 <script> 块")
    raise SystemExit(1)

out = ROOT / "_extracted.js"
out.write_text(m.group(1), encoding="utf-8")

for cmd in (["node", "--check", str(out)],):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        print("没装 node，跳过")
        raise SystemExit(0)
    print(f"$ {' '.join(cmd)}\n  exit={r.returncode}")
    if r.stdout.strip():
        print("  stdout:", r.stdout.strip()[:2000])
    if r.stderr.strip():
        print("  stderr:", r.stderr.strip()[:2000])

# 重复的**顶层**声明检查：同名顶层 const/let/function 会让后者静默覆盖前者，
# 而 node --check 只看语法、不报这个。缩进的同名变量是函数局部的，不算问题。
src = m.group(1)
names: dict[str, list[int]] = {}
for i, line in enumerate(src.splitlines(), 1):
    if line[:1].isspace():
        continue
    for pat in (r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
                r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="):
        mm = re.match(pat, line)
        if mm:
            names.setdefault(mm.group(1), []).append(i)
dups = {k: v for k, v in names.items() if len(v) > 1}
print()
if dups:
    print("!! 重复声明：")
    for k, v in sorted(dups.items()):
        print(f"   {k}: 行 {v}")
else:
    print("无重复声明")
out.unlink(missing_ok=True)
