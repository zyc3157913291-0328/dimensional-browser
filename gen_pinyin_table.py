# -*- coding: utf-8 -*-
"""构建期生成拼音表：data/pinyin.json。

为什么用构建期生成而不是运行时依赖 pypinyin
--------------------------------------------
运行时要保持零依赖（可打包成单文件 exe、可上传 GitHub 直接跑、
将来上公网也不必装包）。pypinyin 是纯构建期工具：把**数据里真实出现过的
汉字**抽出拼音写进 data/pinyin.json，运行时只做字典查表。

代价是新增中文条目时这张表不会自动更新。所以 selfcheck 会把
「有条目的汉字不在拼音表里」报出来，提示重新跑本脚本——
漏字的后果只是那个字不能用拼音搜到，不影响任何正确性。

用法：$env:DSH_ANACONDA\python.exe gen_pinyin_table.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "pinyin.json"


def gather_chinese() -> set[str]:
    """从全部数据文件里收集出现过的汉字。"""
    chars: set[str] = set()
    for name in ("quantities.json", "units.json", "domains.json",
                 "formulas.json", "patterns.json", "symbol_glossary.json",
                 "algebra_rules.json", "variable_units.json"):
        path = DATA / name
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        _walk(doc, chars)
    return chars


def _walk(node: object, out: set[str]) -> None:
    if isinstance(node, str):
        for ch in node:
            if "\u4e00" <= ch <= "\u9fff":
                out.add(ch)
    elif isinstance(node, dict):
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def main() -> int:
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        print("需要 pypinyin 才能重新生成：$env:DSH_ANACONDA\\python.exe -m pip install pypinyin")
        return 1

    chars = sorted(gather_chinese())
    table: dict[str, list[str]] = {}
    for ch in chars:
        # Style.NORMAL 给全拼，Style.FIRST_LETTER 给首字母。
        # 多音字取第一个读音——量纲名词里的多音字极少，
        # 取错也只是搜不到，不会给出错误答案。
        full = lazy_pinyin(ch, style=Style.NORMAL, errors="ignore")
        first = lazy_pinyin(ch, style=Style.FIRST_LETTER, errors="ignore")
        table[ch] = [full[0] if full else "", first[0] if first else ""]

    doc = {
        "kind": "pinyin",
        "note": "构建期用 pypinyin 生成的汉字拼音表（全拼 + 首字母）。"
                "运行时只查表，不依赖 pypinyin。新增中文条目后请重跑 "
                "gen_pinyin_table.py，否则新字的拼音搜索不可用。",
        "source": "pypinyin",
        "chars": table,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"已写出 {OUT.name}：{len(table)} 个汉字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
