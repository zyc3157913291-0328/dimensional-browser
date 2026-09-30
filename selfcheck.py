"""跑数据自检并打印结果。开发期用，不是给用户的功能。

用法：
    python selfcheck.py            # 打印全部问题
    python selfcheck.py --quiet    # 只打印结论
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文会乱码。先强制 UTF-8。
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.registry import Registry, RegistryError  # noqa: E402


def pinyin_gaps() -> list[str]:
    """检查拼音表是否覆盖了数据里出现的全部汉字。

    拼音表是**构建期**由 gen_pinyin_table.py 生成的（运行时零依赖）。
    代价是新增中文条目时它不会自动更新，所以这里主动核对一次：
    漏字的后果只是那个字不能用拼音搜到，不影响任何正确性，
    因此它算提示而不是启动阻塞项。
    """
    from engine.matcher import load_pinyin_table

    table = load_pinyin_table()
    if not table:
        return ["拼音表缺失或为空：联想功能会失去拼音匹配（其余匹配不受影响）。"
                "重新生成：python gen_pinyin_table.py"]

    import json
    base = Path(__file__).resolve().parent / "data"
    chars: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            chars.update(ch for ch in node if "\u4e00" <= ch <= "\u9fff")
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for name in ("quantities.json", "units.json", "domains.json",
                 "formulas.json", "patterns.json", "symbol_glossary.json",
                 "algebra_rules.json", "variable_units.json"):
        path = base / name
        if path.is_file():
            walk(json.loads(path.read_text(encoding="utf-8")))

    missing = sorted(chars - set(table))
    if missing:
        return [f"拼音表缺少 {len(missing)} 个汉字：{''.join(missing[:40])}"
                f"（重跑 python gen_pinyin_table.py 可修复）"]
    return []


def main() -> int:
    quiet = "--quiet" in sys.argv
    try:
        reg = Registry.load()
    except RegistryError as exc:
        print(f"加载失败：{exc}")
        return 2

    problems = reg.selfcheck()
    hints = pinyin_gaps()

    if not quiet:
        print(f"单位 {len(reg.units)} 个")
        print(f"物理量 {len(reg.quantities)} 个")
        print(f"公式 {len(reg.formulas)} 条")
        print(f"代数规则 {len(reg.algebra_rules)} 条")
        print(f"形状模式 {len(reg.patterns)} 条")
        print()

    if hints:
        print(f"提示 {len(hints)} 项（不阻塞启动）：")
        for h in hints:
            print(f"  - {h}")
        print()

    if problems:
        print(f"自检未通过，{len(problems)} 项问题：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("自检全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
