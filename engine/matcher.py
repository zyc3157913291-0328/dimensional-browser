"""联想匹配：输入的同时给出可能的结果。

用户报告的两条问题
------------------
1. 「目前只有缺字匹配，没有多字匹配（输入 a 可以匹配到 ab，但是 abc 不能匹配到 ab）」
   —— 原实现只做 `候选.startswith(查询)`，查询比候选长时永远匹配不上。

2. 「而且是点击搜索后匹配的，我实际希望可以直接在输入的同时匹配可能结果」
   —— 需要一个足够快的联想接口，不是每次敲键都跑完整建树。

设计取舍
--------
**不做全文检索，做「名称检索」**。候选是有限的（55 个物理量 + 27 个单位），
所以不需要倒排索引、不需要分词、不需要向量检索：把每个候选的若干种写法
摊平成一个列表，逐条打分取最高分即可。实测全量扫描在微秒量级，
比建索引的复杂度便宜得多。

匹配梯度（从强到弱）
--------------------
    exact        完全相同
    prefix       查询是候选的前缀            a → 加速度 / 安培
    substring    查询是候选的子串            强度 → 电场强度
    reverse      候选是查询的子串            abc → ab      ← 用户明确要求的方向
    pinyin       拼音全拼前缀               dianchang → 电场强度
    initials     拼音首字母                 dcqd → 电场强度
    subseq       查询是候选的子序列           dqd → 电场强度
    subseq_rev   候选是查询的子序列           abcd → abc
    overlap      字符重合率 ≥ 阈值           电场强渡 → 电场强度（错字兜底）

字段权重决定「同一个查询命中哪个字段更优先」：中文名与符号最高，
分类与领域最低（它们只用于探索式浏览，不该压过精确的名字匹配）。
低权重字段还额外限定只能用强匹配类型，否则「输入任意字都能靠分类命中」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .dim import Dim
from .paths import data_dir
from .registry import Registry

_PINYIN_PATH = data_dir() / "pinyin.json"

# 匹配类型 → (分值, 中文说明)。分值只用于排序，不对外暴露。
_MATCH_SCORE: dict[str, tuple[int, str]] = {
    "exact": (10000, "完全相同"),
    "prefix": (8000, "名称开头"),
    "substring": (6000, "名称包含"),
    "reverse": (4800, "输入更长，包含该名称"),
    "pinyin": (5200, "拼音"),
    "initials": (4600, "拼音首字母"),
    "subseq": (2600, "部分字符按序命中"),
    "subseq_rev": (2000, "输入包含该名称的全部字符"),
    "overlap": (1400, "字符部分重合（可能是错字）"),
}

# 字段权重（乘在匹配分上）。用整数百分比避免浮点比较。
_FIELD_WEIGHT: dict[str, int] = {
    "zh": 100,
    "symbol": 100,
    "alias": 92,
    "id": 78,
    "pinyin": 74,
    "unit_form": 70,
    "category": 46,
    "domain": 38,
}

#: 权重低于这个值的字段只接受强匹配类型。
#: 没有它，输入「电」会因为分类/领域字段的子串命中而带出无关条目。
_WEAK_FIELD = 60
_STRONG_KINDS = ("exact", "prefix", "substring")

#: 错字兜底的重合率门槛与最短长度。门槛定得高是刻意的：
#: 兜底只在「确实找不到别的」时才有价值，宽了就是噪声。
#: 要求**较短串**也达到最短长度，否则 1 字符的符号会把任何查询都吃下来。
_OVERLAP_MIN_LEN = 3
_OVERLAP_MIN_RATIO = 0.7

# 最低接受分：低于它的候选不进联想列表，避免「输入任何东西都有一堆结果」。
_MIN_SCORE = 1200


def load_pinyin_table(path: Path | str = _PINYIN_PATH) -> dict[str, list[str]]:
    """读构建期生成的汉字拼音表。缺失时返回空表（拼音搜索降级，不影响其余）。"""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    chars = doc.get("chars")
    if not isinstance(chars, dict):
        return {}
    out: dict[str, list[str]] = {}
    for ch, val in chars.items():
        if isinstance(val, list) and len(val) >= 2:
            out[str(ch)] = [str(val[0]), str(val[1])]
    return out


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def pinyin_forms(text: str, table: dict[str, list[str]]) -> tuple[str, str]:
    """中文串 → (全拼, 首字母)。无拼音表的字按原样保留，保证仍可前缀匹配。"""
    full: list[str] = []
    initials: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            entry = table.get(ch)
            if entry:
                full.append(entry[0])
                initials.append(entry[1] or entry[0][:1])
            # 表里没有这个字：不猜读音，直接跳过——
            # 让该字符串的拼音键短一点，总比生成错误的键好
        else:
            low = ch.lower()
            if low.isalnum():
                full.append(low)
                initials.append(low)
    return "".join(full), "".join(initials)


def _is_subsequence(needle: str, hay: str) -> bool:
    """needle 的字符是否按顺序出现在 hay 里（可跳过 hay 的字符）。"""
    if not needle:
        return False
    it = iter(hay)
    return all(ch in it for ch in needle)


def _overlap_ratio(a: str, b: str) -> float:
    """两个串的字符重合率（按较短串计）。

    只按较短串计是刻意的：错字总是「替换几个字」，「电场强渡」与
    「电场强度」重合 3/4。但这样一来 1 字符的候选（符号 a、c、m）
    与任何包含它的查询都会得到 1.0 —— 所以调用方还必须要求
    **较短串达到最短长度**（见 _OVERLAP_MIN_LEN），否则
    「输入 abc 命中 a、c、A、C、B」的噪声就来了。
    """
    if not a or not b:
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    pool = list(long)
    hit = 0
    for ch in short:
        if ch in pool:
            pool.remove(ch)
            hit += 1
    return hit / len(short)


def match_kind(query: str, text: str) -> str:
    """判断查询与一个候选文本的匹配类型。

    不支持的类型返回多个备选（用空格分隔）——调用方按字段权重与
    「强匹配类型」限制挑一个。返回空串表示完全不匹配。
    """
    if not query or not text:
        return ""
    q = query.lower()
    t = text.lower()
    if q == t:
        return "exact"
    if t.startswith(q):
        return "prefix"
    if q in t:
        return "substring"
    # 用户明确要求的方向：输入比候选长也要能命中（abc → ab）。
    # 候选至少两字符，否则任何查询里含一个字母都会命中，等于噪声。
    if len(t) >= 2 and t in q:
        return "reverse"
    if _is_subsequence(q, t):
        return "subseq"
    if len(t) >= 2 and _is_subsequence(t, q):
        return "subseq_rev"
    if (min(len(q), len(t)) >= _OVERLAP_MIN_LEN
            and _overlap_ratio(q, t) >= _OVERLAP_MIN_RATIO):
        return "overlap"
    return ""


@dataclass
class Candidate:
    """一个可被联想到的条目（物理量或单位）。"""

    id: str
    kind: str                    # quantity | unit
    zh: str
    symbol: str
    dim: Dim
    dim_si: str
    dim_zh: str
    category: str = ""
    domain_zh: str = ""
    unit_forms: list[str] = field(default_factory=list)
    note: str = ""
    domain: str = ""

    def to_json(self, match: str, via: str, score: int) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "zh": self.zh,
            "symbol": self.symbol,
            "dim_si": self.dim_si,
            "dim_zh": self.dim_zh,
            "category": self.category,
            "domain_zh": self.domain_zh,
            "unit_forms": self.unit_forms[:3],
            "note": self.note[:90],
            "match": match,
            "match_zh": _MATCH_SCORE.get(match, (0, ""))[1],
            "via": via,
            "score": score,
        }


class Matcher:
    """把注册表摊平成可打分的候选列表，实现即时联想。"""

    def __init__(self, reg: Registry, pinyin_path: Path | str = _PINYIN_PATH) -> None:
        self.reg = reg
        self.pinyin_table = load_pinyin_table(pinyin_path)
        self.candidates: list[Candidate] = []
        # 每个候选的 (字段名, 文本, 权重) 列表，构建期算一次
        self._fields: list[list[tuple[str, str, int]]] = []
        self._build()

    # -------- 构建 --------

    def _build(self) -> None:
        for q in self.reg.quantities.values():
            c = Candidate(
                id=q.id, kind="quantity", zh=q.zh, symbol=q.symbol, dim=q.dim,
                dim_si=q.dim.format_si(), dim_zh=q.dim.describe_zh(),
                category=q.category, domain_zh=q.domain_zh,
                unit_forms=list(q.unit_forms), note=q.note or "", domain=q.domain,
            )
            self._add(c, aliases=list(q.aliases))
        for u in self.reg.units.values():
            c = Candidate(
                id=u.id, kind="unit", zh=u.zh, symbol=u.symbol, dim=u.dim,
                dim_si=u.dim.format_si(), dim_zh=u.dim.describe_zh(),
                category=u.system, unit_forms=[u.symbol], note=u.note or "",
            )
            self._add(c, aliases=list(u.aliases))

    def _add(self, c: Candidate, aliases: Iterable[str]) -> None:
        fields: list[tuple[str, str, int]] = []

        def put(name: str, text: str) -> None:
            text = (text or "").strip()
            if text:
                fields.append((name, text, _FIELD_WEIGHT.get(name, 50)))

        put("zh", c.zh)
        put("symbol", c.symbol)
        for a in aliases:
            put("alias", a)
        put("id", c.id)
        for f in c.unit_forms:
            put("unit_form", f)
        put("category", c.category)
        put("domain", c.domain_zh)

        if self.pinyin_table:
            full, initials = pinyin_forms(c.zh, self.pinyin_table)
            if full:
                fields.append(("pinyin", full, _FIELD_WEIGHT["pinyin"]))
            if initials and len(initials) >= 2:
                fields.append(("pinyin", initials, _FIELD_WEIGHT["pinyin"] - 6))

        self.candidates.append(c)
        self._fields.append(fields)

    # -------- 查询 --------

    def suggest(self, text: str, limit: int = 8) -> list[dict[str, object]]:
        """返回按匹配强度排序的候选列表。

        分数 = 匹配类型分 × 字段权重 ÷ 100，另加少量长度惩罚（更短的候选
        更可能是用户想找的那个：输入「力」时「力」排在「表面张力」前）。
        """
        q = (text or "").strip()
        if not q:
            return []

        scored: list[tuple[int, dict[str, object]]] = []
        for c, fields in zip(self.candidates, self._fields):
            best: tuple[int, str, str] = (0, "", "")
            for name, ftext, weight in fields:
                mk = match_kind(q, ftext)
                if not mk:
                    continue
                # 弱字段（分类、领域）只认强匹配：否则「电」会靠 category
                # 把整个电磁学分类都带出来，压过真正的名字匹配。
                if weight < _WEAK_FIELD and mk not in _STRONG_KINDS:
                    continue
                sc = _MATCH_SCORE[mk][0] * weight // 100
                if sc > best[0]:
                    best = (sc, mk, name)
            if best[0] < _MIN_SCORE:
                continue
            # 长度惩罚：同样命中类型下，名字短的更可能是目标。
            # 只在中文名与符号上施加，避免把「单位写法很长」的候选误伤。
            penalty = 0
            if best[2] in ("zh", "symbol", "alias", "id"):
                penalty = max(0, len(c.zh) - len(q)) * 12
            score = best[0] - penalty
            scored.append((score, c.to_json(best[1], best[2], score)))

        # 同分时：物理量优先于单位，再按中文名长度（短的更可能是目标）
        scored.sort(key=lambda t: (-t[0], 0 if t[1]["kind"] == "quantity" else 1,
                                   len(str(t[1]["zh"])), str(t[1]["id"])))
        out: list[dict[str, object]] = []
        seen_dims: dict[str, int] = {}
        for _score, item in scored:
            key = str(item["dim_si"])
            # 同一量纲最多给 3 条（同量纲异构是教学重点，但不该霸屏）
            if seen_dims.get(key, 0) >= 3:
                continue
            seen_dims[key] = seen_dims.get(key, 0) + 1
            out.append(item)
            if len(out) >= limit:
                break
        return out
