"""命令行预览：把一棵树打印成文本。

开发期用来看「树长得好不好看」，也是 Web 界面之前的第一个检查点。
用法：
    python cli.py 牛
    python cli.py 电场强度 --depth 2
    python cli.py N/C
"""

from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.registry import Registry  # noqa: E402
from engine.search import DimensionSearch, SearchBounds  # noqa: E402
from engine.tree import EntityView, TreeBuilder  # noqa: E402

KIND_MARK = {
    "equivalence": "≡",
    "derivative": "∂",
    "integral": "∫",
    "matrix": "▦",
    "search": "?",
}


def print_entity(view: EntityView, indent: str = "", searched: list | None = None) -> None:
    struct = f"  [{view.structure}]" if view.structure != "scalar" else ""
    print(f"{indent}{view.zh} ({view.symbol}){struct}")
    print(f"{indent}  量纲：{view.dim.format_si()}   中文：{view.dim_zh}")
    if view.category:
        print(f"{indent}  类别：{view.category}")
    if view.unit_forms:
        print(f"{indent}  常见写法：{'、'.join(view.unit_forms)}")
    if view.note:
        print(f"{indent}  说明：{view.note}")

    if view.homogeneous:
        names = "、".join(f"{h['zh']}（{h['symbol']}）" for h in view.homogeneous)
        print(f"{indent}  ⚠ 同量纲异构：{names}")
        print(f"{indent}     —— 量纲相同，但物理意义不同")

    if view.equivalent:
        print(f"{indent}  等价写法（同一个量换写法）：")
        for b in view.equivalent:
            _print_branch(b, indent + "    ")

    if view.generated:
        print(f"{indent}  由物理式子生成（量纲变化）：")
        for b in view.generated:
            _print_branch(b, indent + "    ")

    if view.formulas:
        print(f"{indent}  相关公式：")
        for f in view.formulas:
            pat = f"  〔{f['pattern_zh']}〕" if f.get("pattern_zh") else ""
            print(f"{indent}    · {f['tex']:<26s} {f['zh']}{pat}")

    if searched:
        print(f"{indent}  算法枚举（有界搜索，未对应具体物理式子）：")
        for b in searched:
            print(f"{indent}    ? {b.expr:<22s} {b.zh}")


def _print_branch(b, indent: str) -> None:
    mark = KIND_MARK.get(b.kind, "·")
    src = ""
    if b.source.is_from_formula:
        src = f"   ← {b.source.tex}"
    elif b.source.note:
        src = f"   ← {b.source.note[:34]}"
    print(f"{indent}{mark} {b.expr:<22s} {b.zh}{src}")
    if b.points_to:
        print(f"{indent}    也是：{'、'.join(b.points_to)}")
    if b.source.note and b.source.is_from_formula:
        print(f"{indent}    {b.source.note}")
    for c in b.children:
        _print_branch(c, indent + "    ")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    depth = 2
    want_search = "--search" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--depth"):
            depth = int(a.split("=", 1)[1]) if "=" in a else 2

    if not args:
        print(__doc__)
        return 0

    reg = Registry.load()
    problems = reg.selfcheck()
    if problems:
        print(f"数据自检未通过（{len(problems)} 项），拒绝建树：")
        for p in problems:
            print(f"  - {p}")
        return 1

    builder = TreeBuilder(reg, max_depth=depth)
    for query in args:
        result = builder.lookup(query, max_depth=depth)
        print("=" * 72)
        print(f"查询：{query}    （匹配类型：{result.matched_kind}）")
        print("=" * 72)
        if result.error:
            print(f"  {result.error}")
            continue
        for i, view in enumerate(result.entities):
            if i:
                print("-" * 72)
            searched = None
            if want_search:
                builder.with_search(view)
                searched = view.searched
            print_entity(view, searched=searched)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
