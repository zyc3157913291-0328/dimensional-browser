"""页面槽位覆盖审计：按页面规范逐条核对每个物理量的空槽位。

用法：$env:DSH_ANACONDA\python.exe audit_pages.py [--verbose]
"""
from __future__ import annotations

import io
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from engine.registry import Registry  # noqa: E402
from engine.tree import TreeBuilder  # noqa: E402

reg = Registry.load()
tb = TreeBuilder(reg, max_depth=2)

# 页面规范的槽位（与 docs/2026-05-26-page-schema.md 一致）
SLOTS = [
    "symbol",        # 英文符号
    "category",      # 细分类
    "domain",        # 领域
    "dim",           # SI 量纲（引擎算出，必然有）
    "unit_forms",    # 常见写法
    "note",          # 一句话描述
    "decompositions",  # 等价写法
    "definitions",   # 定义式
    "formulas",      # 相关物理式子
    "generated",     # 由物理式子生成（求导/积分/矩阵）
    "homogeneous",   # 同量纲异构
    "glossary",      # 符号表（跟随公式）
]

missing: dict[str, list[str]] = {s: [] for s in SLOTS}
for qid, q in sorted(reg.quantities.items()):
    checks = {
        "symbol": bool(q.symbol),
        "category": bool(q.category),
        "domain": bool(q.domain),
        "dim": True,
        "unit_forms": bool(q.unit_forms),
        "note": bool(q.note),
        "decompositions": bool(q.decompositions),
        "definitions": any(f.defines == qid for f in reg.formulas.values()),
    }
    v = tb._view_for_quantity(q, 2)  # noqa: SLF001
    checks["formulas"] = bool(v.formulas)
    checks["generated"] = bool(v.generated)
    checks["homogeneous"] = bool(v.homogeneous)
    checks["glossary"] = any(f["symbols"] for f in v.formulas)
    for slot, ok in checks.items():
        if not ok:
            missing[slot].append(qid)

total = len(reg.quantities)
print(f"物理量总数 {total}\n")
print(f"{'槽位':<16}{'缺':>5}   缺失条目")
print("-" * 100)
for slot in SLOTS:
    ids = missing[slot]
    tag = "（见下表，不全是缺失）" if slot == "definitions" else ""
    print(f"{slot:<16}{len(ids):>5}   "
          f"{'、'.join(ids) if ids else '（全覆盖）'}{tag}")

print()
print("=" * 100)
print("定义式三态（页面规范 §3）——「不适用」不是缺失，补了反而是错的：")
has_def, na_def, miss_def = [], [], []
for qid, q in sorted(reg.quantities.items()):
    if any(f.defines == qid for f in reg.formulas.values()):
        has_def.append(qid)
    elif q.defined_as in ("si-base", "umbrella"):
        na_def.append(qid)
    else:
        miss_def.append(qid)
print(f"  有定义式     {len(has_def):>3} 个")
print(f"  不适用（有理由）{len(na_def):>3} 个：" + "、".join(
    f"{qid}({reg.quantities[qid].defined_as})" for qid in na_def))
print(f"  暂缺（要补录） {len(miss_def):>3} 个："
      + ("、".join(miss_def) if miss_def else "（无 — 每个条目都有定义式或明确理由）"))

print()
print("=" * 100)
print("关系类槽位（为空是正常的，不代表数据缺失）：")
empty_gen = [qid for qid, q in reg.quantities.items()
             if not tb._view_for_quantity(q, 2).generated]  # noqa: SLF001
empty_hom = [qid for qid, q in reg.quantities.items()
             if not tb._view_for_quantity(q, 2).homogeneous]  # noqa: SLF001
print(f"  「由物理式子生成」为空：{len(empty_gen)} 个"
      f"（已知运算里没有产生这个量纲的）")
print(f"  「同量纲异构」为空：    {len(empty_hom)} 个（该量纲下只有它一个）")

# 量纲 -> 有多少物理量
dimcount = Counter(q.dim.format_si() for q in reg.quantities.values())
print()
print(f"涉及量纲 {len(dimcount)} 种；其中只有 1 个物理量的量纲 "
      f"{sum(1 for v in dimcount.values() if v == 1)} 种")
