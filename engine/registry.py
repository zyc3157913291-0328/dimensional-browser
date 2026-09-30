"""注册表：加载数据、别名归一、启动自检、索引。

设计文档 3.7 的落地。这里最重要的不是加载，而是 **selfcheck()**：
手写 dim 与公式签名必然出错，唯一可持续的办法是让引擎自己验算。
自检不通过就直接报错，绝不允许「带着错误数据继续跑」。

自检覆盖五项：
  1. 单位/别名无冲突
  2. 物理量的 unit_forms / decompositions 表达式量纲 == 条目 dim
  3. 公式签名的 lhs 与 rhs 按 ops 推导后必须齐次（公式库自身的公式检查）
  4. relations 引用的单位/物理量必须存在
  5. 代数规则表声明的 transform 必须有实现
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import algebra, parser as P
from .dim import Dim, DimError
from .evaluator import (
    EvalContext,
    EvalError,
    Operand,
    UnitResolver,
    evaluate,
    variables_from_signature,
)
from .paths import data_dir

DEFAULT_DATA_DIR = data_dir()


class RegistryError(RuntimeError):
    """数据加载或自检失败。"""


# ------------------------------------------------------------------ 数据类


@dataclass(frozen=True)
class Unit:
    id: str
    zh: str
    symbol: str
    aliases: tuple[str, ...]
    dim: Dim
    factor: float
    prefixable: bool
    system: str
    note: str = ""


@dataclass(frozen=True)
class Prefix:
    symbol: str
    zh: str
    factor: float


@dataclass(frozen=True)
class Decomposition:
    expr: str
    zh: str = ""
    note: str = ""


@dataclass(frozen=True)
class Domain:
    """物理领域（力/热/声/光/电…）。分类体系是数据，不是代码。

    domain 是粗分类，用于首页浏览导航；quantity.category 是细分类，
    用于条目定位。两者分工不同，都保留。
    """

    id: str
    zh: str
    en: str
    order: int
    desc: str
    covers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Quantity:
    id: str
    zh: str
    symbol: str
    dim: Dim
    category: str
    structure: str
    unit_forms: tuple[str, ...]
    decompositions: tuple[Decomposition, ...]
    aliases: tuple[str, ...] = ()
    homogeneous_with: tuple[str, ...] = ()
    note: str = ""
    # 所属领域 id，指向 domains.json。空串表示尚未分类（自检会报错）。
    domain: str = ""
    # 领域中文名。由注册表在索引时从 domains.json 填入，条目数据里不写，
    # 避免两处重复维护（改领域名只需改 domains.json 一处）。
    domain_zh: str = ""
    # 「没有定义式」是不是正确答案。取值：
    #   ""          —— 未标注。若也没有 defines 公式，页面留空位 + 补录入口
    #   "si-base"   —— SI 基本量，由基本单位（定义常数）确立，本来就没有定义式
    #   "umbrella"  —— 总称量纲（如「能量」），没有唯一定义式是它的性质
    # 这个字段存在的理由：用户报告「有些量纲没有定义式」。查下来发现
    # 大部分是数据缺失（已补齐），但少数几类**本来就不该有**定义式——
    # 把「缺失」和「不适用」混为一种是数据建模问题，不是内容问题。
    defined_as: str = ""
    definition_note: str = ""


@dataclass(frozen=True)
class Relation:
    """公式产生的量纲关系边。kind 区分等价重写与泛函生成（设计文档 2.2）。"""

    kind: str
    from_: str
    to: str
    via: str
    note: str = ""
    wrt: str = ""
    order: int = 1

    @property
    def is_equivalence(self) -> bool:
        return self.kind == "equivalence"


@dataclass(frozen=True)
class Formula:
    id: str
    tex: str
    zh: str
    category: str
    pattern: str
    ops: tuple[str, ...]
    lhs_symbol: str
    lhs_dim: Dim
    lhs_structure: str
    rhs_entries: tuple[Mapping[str, Any], ...]
    relations: tuple[Relation, ...]
    # 该公式定义哪个物理量（物理量 id）。空串表示它只是一条关系式/定律。
    # 这个字段必须是数据标注，不能从量纲推断——见 tree._definitions_for。
    defines: str = ""


@dataclass(frozen=True)
class Pattern:
    id: str
    zh: str
    rule: str
    explain: str = ""


@dataclass(frozen=True)
class AlgebraRule:
    op: str
    zh: str
    transform: str
    structure: str
    explain: str


# ------------------------------------------------------------------ 注册表


@dataclass(frozen=True)
class VariableUnit:
    """公式里的变量名 → 它惯用的单位（x→m, t→s, V→m³）。

    存在的理由：物理公式里求导/积分变量写成 x、t、V 是惯例，
    但引擎需要单位才能算量纲。把惯例做成数据而不是硬编码，
    既让物理惯例可审阅，也不污染单位表（x 不是单位）。
    """

    symbol: str
    unit: str
    zh: str
    note: str = ""


@dataclass(frozen=True)
class SymbolEntry:
    """公式符号表条目：某个字母在这条公式里是什么意思。"""

    symbol: str
    zh: str
    meaning: str
    unit_sym: str          # 该符号常用单位的符号写法，如 "kg·m/s²"
    unit_zh: str           # 单位的英文/中文名，如 "牛顿"
    dim: Dim
    jump: str = ""         # 可跳转的物理量 id（同量纲唯一命中时才有）
    jump_zh: str = ""
    resolved_by: str = ""  # "glossary" 或 "lookup"，便于排查张冠李戴


@dataclass
class Registry:
    units: dict[str, Unit]
    prefixes: tuple[Prefix, ...]
    quantities: dict[str, Quantity]
    formulas: dict[str, Formula]
    patterns: dict[str, Pattern]
    algebra_rules: dict[str, AlgebraRule]
    variable_units: dict[str, VariableUnit] = field(default_factory=dict)
    symbol_glossary: dict[tuple[str, Dim], SymbolEntry] = field(default_factory=dict)
    # 领域分类体系（domains.json）。顺序由 Domain.order 决定。
    domains: dict[str, Domain] = field(default_factory=dict)
    # 物理量的中心度（由公式库结构算出），用于同量纲组排序
    canonicality: dict[str, float] = field(default_factory=dict, repr=False)
    _unit_keys: dict[str, str] = field(default_factory=dict, repr=False)
    _unit_objects: dict[str, Unit] = field(default_factory=dict, repr=False)
    _quantity_keys: dict[str, str] = field(default_factory=dict, repr=False)
    _by_dim: dict[Dim, list[str]] = field(default_factory=dict, repr=False)
    resolver: UnitResolver | None = field(default=None, repr=False)

    # -------- 加载 --------

    @staticmethod
    def load(data_dir: Path | str = DEFAULT_DATA_DIR) -> "Registry":
        base = Path(data_dir)
        if not base.is_dir():
            raise RegistryError(f"数据目录不存在：{base}")

        units_doc = _read_json(base / "units.json")
        quantities_doc = _read_json(base / "quantities.json")
        formulas_doc = _read_json(base / "formulas.json")
        rules_doc = _read_json(base / "algebra_rules.json")
        patterns_doc = (
            _read_json(base / "patterns.json")
            if (base / "patterns.json").is_file()
            else {"patterns": []}
        )
        var_doc = (
            _read_json(base / "variable_units.json")
            if (base / "variable_units.json").is_file()
            else {"variables": []}
        )
        glossary_doc = (
            _read_json(base / "symbol_glossary.json")
            if (base / "symbol_glossary.json").is_file()
            else {"symbols": []}
        )
        domains_doc = (
            _read_json(base / "domains.json")
            if (base / "domains.json").is_file()
            else {"domains": []}
        )

        domains: dict[str, Domain] = {}
        for raw in domains_doc.get("domains", []):
            d = Domain(
                id=str(raw["id"]),
                zh=str(raw.get("zh", raw["id"])),
                en=str(raw.get("en", "")),
                order=int(raw.get("order", 999)),
                desc=str(raw.get("desc", "")),
                covers=tuple(str(c) for c in raw.get("covers", [])),
            )
            if d.id in domains:
                raise RegistryError(f"领域 id 重复：{d.id}")
            domains[d.id] = d

        units: dict[str, Unit] = {}
        for raw in units_doc.get("units", []):
            unit = _build_unit(raw)
            if unit.id in units:
                raise RegistryError(f"单位 id 重复：{unit.id}")
            units[unit.id] = unit

        prefixes = tuple(
            Prefix(str(p["symbol"]), str(p["zh"]), float(p["factor"]))
            for p in units_doc.get("prefixes", [])
        )

        quantities: dict[str, Quantity] = {}
        for raw in quantities_doc.get("quantities", []):
            q = _build_quantity(raw)
            if q.id in quantities:
                raise RegistryError(f"物理量 id 重复：{q.id}")
            quantities[q.id] = q

        formulas: dict[str, Formula] = {}
        for raw in formulas_doc.get("formulas", []):
            f = _build_formula(raw)
            if f.id in formulas:
                raise RegistryError(f"公式 id 重复：{f.id}")
            formulas[f.id] = f

        rules: dict[str, AlgebraRule] = {}
        for raw in rules_doc.get("rules", []):
            r = AlgebraRule(
                op=str(raw["op"]),
                zh=str(raw.get("zh", raw["op"])),
                transform=str(raw["transform"]),
                structure=str(raw.get("structure", "any")),
                explain=str(raw.get("explain", "")),
            )
            rules[r.op] = r

        patterns: dict[str, Pattern] = {}
        for raw in patterns_doc.get("patterns", []):
            p = Pattern(
                id=str(raw["id"]),
                zh=str(raw.get("zh", raw["id"])),
                rule=str(raw.get("rule", "")),
                explain=str(raw.get("explain", "")),
            )
            patterns[p.id] = p

        variable_units: dict[str, VariableUnit] = {}
        for raw in var_doc.get("variables", []):
            v = VariableUnit(
                symbol=str(raw["symbol"]),
                unit=str(raw["unit"]),
                zh=str(raw.get("zh", "")),
                note=str(raw.get("note", "")),
            )
            variable_units[v.symbol] = v

        # 符号表的「例外表」：只需要写引擎自动解析不出来的那些。
        # 结构是**数组**而非对象——因为键是 (symbol, dim) 复合的，
        # 同一个符号可能出现多条。
        glossary_raw: list[Mapping[str, Any]] = [
            r for r in glossary_doc.get("symbols", []) if isinstance(r, Mapping)
        ]

        reg = Registry(
            units=units,
            prefixes=prefixes,
            quantities=quantities,
            formulas=formulas,
            patterns=patterns,
            algebra_rules=rules,
            variable_units=variable_units,
            domains=domains,
        )
        reg._index()
        # 求值器要「键 → 单位对象」，所以在这里把 id 映射展开成对象映射。
        # 与 _unit_keys（键 → id）分开，两者用途不同，不要合并。
        reg._unit_objects = {k: units[uid] for k, uid in reg._unit_keys.items()}
        reg.resolver = UnitResolver(reg._unit_objects, prefixes)
        reg._attach_domain_names()
        reg._build_canonicality()
        reg._build_symbol_glossary(glossary_raw)
        return reg

    def _attach_domain_names(self) -> None:
        """把领域中文名回填到物理量上。

        domain 的「id → 中文名」映射只维护在 domains.json 一处；
        物理量条目只写 domain id。这样改领域名不必逐个改条目。
        """
        replaced: dict[str, Quantity] = {}
        for qid, q in self.quantities.items():
            if not q.domain:
                replaced[qid] = q
                continue
            d = self.domains.get(q.domain)
            replaced[qid] = replace(q, domain_zh=d.zh if d else "")
        self.quantities = replaced

    def domains_ordered(self) -> list[Domain]:
        """按 order 排序的领域列表，供首页浏览使用。"""
        return sorted(self.domains.values(), key=lambda d: (d.order, d.id))

    def quantities_in_domain(self, domain_id: str) -> list[Quantity]:
        """某领域下的全部物理量，按中心度排序（更常用的在前）。"""
        qs = [q for q in self.quantities.values() if q.domain == domain_id]
        return self.rank_quantities(qs)

    # -------- 索引 --------

    def _index(self) -> None:
        keys: dict[str, str] = {}
        conflicts: list[str] = []

        def add(key: str, uid: str, source: str) -> None:
            if not key:
                return
            existing = keys.get(key)
            if existing is not None and existing != uid:
                conflicts.append(
                    f"{key!r} 同时指向 {existing} 与 {uid}（来源：{source}）"
                )
                return
            keys[key] = uid

        # 优先级：id > symbol > 别名。但同一优先级里的冲突要报错，
        # 因为那意味着数据本身有歧义。
        # 优先级：id > symbol > 中文名 > 别名。
        # 中文名必须入索引：用户按「牛」「焦」「帕」查找是最主要的入口，
        # 只有 aliases 里恰好写了「牛顿」并不足以覆盖「牛」。
        for uid, u in self.units.items():
            add(uid, uid, f"{uid}.id")
        for uid, u in self.units.items():
            add(u.symbol, uid, f"{uid}.symbol")
        for uid, u in self.units.items():
            add(u.zh, uid, f"{uid}.zh")
        for uid, u in self.units.items():
            for a in u.aliases:
                add(a, uid, f"{uid}.aliases")

        if conflicts:
            raise RegistryError("单位索引冲突：\n  - " + "\n  - ".join(conflicts))
        self._unit_keys = keys

        qkeys: dict[str, str] = {}
        qconf: list[str] = []
        for qid, q in self.quantities.items():
            for key in (qid, q.symbol, q.zh, *q.aliases):
                if not key:
                    continue
                old = qkeys.get(key)
                if old is not None and old != qid:
                    qconf.append(f"{key!r} 同时指向 {old} 与 {qid}")
                    continue
                qkeys[key] = qid
        if qconf:
            raise RegistryError("物理量索引冲突：\n  - " + "\n  - ".join(qconf))
        self._quantity_keys = qkeys

        by_dim: dict[Dim, list[str]] = {}
        for qid, q in self.quantities.items():
            by_dim.setdefault(q.dim, []).append(qid)
        self._by_dim = by_dim

    # -------- 查询 --------

    def find_unit(self, name: str) -> Unit | None:
        uid = self._unit_keys.get(name)
        return self.units[uid] if uid else None

    def find_quantity(self, name: str) -> Quantity | None:
        qid = self._quantity_keys.get(name)
        return self.quantities[qid] if qid else None

    def find_variable_unit(self, name: str) -> VariableUnit | None:
        """查变量名（x / t / V …）惯用的单位。"""
        return self.variable_units.get(name)

    def suggest_quantities(self, text: str, limit: int = 8) -> list[Quantity]:
        """按名称/符号/别名给出候选物理量。

        为什么需要它：物理符号天然有歧义。`I` 在文献里同时是电流、冲量、
        转动惯量；`p` 是动量也是压强；`U` 是势能也是电势。
        硬把它绑到某一个条目上是猜——猜错比不猜更糟。

        匹配策略按输入形态分流（这里改过一版：初版把「精确匹配」和
        「子串兜底」串在一起，导致输入「势」时被子串命中的「势能」
        短路，电势能/化学势能/重力势能全被跳过）：

          单字母（如 I、p、U）  匹配**符号与别名的前缀**。
                                这是符号检索，用户想找符号为 I 的量。
          多字符（如 势、电场） 匹配**中文名与 id 的子串**，外加符号前缀。
                                这是名称检索，「势」应当带出全部含「势」的量。

        两种命中的并集按中心度排序（更常用的量在前）。
        """
        t = (text or "").strip()
        if not t:
            return []
        low = t.lower()
        # 单字母走符号前缀匹配（那是符号检索）；
        # 但单个**中文字**要走名称子串匹配——「势」应当带出全部含「势」的量。
        is_symbol_query = len(t) == 1 and t.isascii() and t.isalpha()

        hits: dict[str, Quantity] = {}

        def take(q: Quantity) -> None:
            hits.setdefault(q.id, q)

        if is_symbol_query:
            for q in self.quantities.values():
                for k in (q.symbol, *q.aliases):
                    if k and k.lower().startswith(low):
                        take(q)
                        break
            return self.rank_quantities(list(hits.values()))[:limit]

        # 名称检索：中文名 / id / 别名 子串匹配，外加符号前缀
        for q in self.quantities.values():
            keys = (q.zh, q.id, *q.aliases)
            if any(low in (k or "").lower() for k in keys):
                take(q)
                continue
            if q.symbol.lower().startswith(low):
                take(q)

        if not hits:
            # 兜底：全部键做子串匹配
            for q in self.quantities.values():
                keys = (q.zh, q.id, q.symbol, *q.aliases)
                if any(low in (k or "").lower() for k in keys):
                    take(q)

        return self.rank_quantities(list(hits.values()))[:limit]

    def var_dim(self, name: str) -> Dim | None:
        """把公式里的求导/积分变量名解析成量纲。

        解析顺序**刻意**是映射表优先于单位符号表：

          1. variable_units.json 里的变量名映射
          2. 直接当单位符号
          3. 当单位表达式求值

        为什么映射表要优先：单字母变量与单位符号高度重名，且语义相反。
        `s` 既是「秒」的单位符号，又是物理里常见的「路程」变量；
        `m` 既是「米」，又是「质量」；`T` 既是「特斯拉」，又是「温度」。
        在 wrt（对谁求导）这个位置，它一定是**变量**语义——
        例如 p = ∫F dt 的关系里 wrt='s' 指的是时间。
        若让单位表优先，会解析成路程（长度），导致积分变量校验失败、
        整条泛函分支静默消失。这是真实踩过的坑。
        """
        vu = self.find_variable_unit(name)
        if vu is not None:
            u = self.find_unit(vu.unit)
            if u is not None:
                return u.dim
            try:
                _n, res = self.eval_expr(vu.unit)
                return res.dim
            except (P.ParseError, EvalError, DimError):
                pass
        u = self.find_unit(name)
        if u is not None:
            return u.dim
        try:
            _n, res = self.eval_expr(name)
            return res.dim
        except (P.ParseError, EvalError, DimError):
            return None

    def quantities_with_dim(self, dim: Dim) -> list[Quantity]:
        """找出所有量纲相同的物理量——这是「同量纲异构」检测的基础。

        返回顺序按中心度排序：该量纲的「主角」在前（如 M·L²·T⁻² 下
        能量在前、弯矩在后）。界面展示与跳转目标都优先取主角。
        """
        ids = self._by_dim.get(dim, [])
        return self.rank_quantities([self.quantities[i] for i in ids])

    def formulas_touching_dim(self, dim: Dim) -> list[Formula]:
        """找出量纲涉及目标维度的公式，用于建树时优先走公式库。"""
        out: list[Formula] = []
        target = dim
        for f in self.formulas.values():
            if f.lhs_dim == target:
                out.append(f)
                continue
            for e in f.rhs_entries:
                d = e.get("dim") or {}
                if isinstance(d, Mapping):
                    try:
                        ed = Dim.from_mapping(d)  # type: ignore[arg-type]
                    except DimError:
                        continue
                    if ed == target:
                        out.append(f)
                        break
        return out

    def relations_for_dim(self, dim: Dim) -> list[tuple[Formula, Relation]]:
        """收集所有「产出该量纲」的关系边。建树的公式库来源。

        注意匹配方向：对 equivalence，from 和 to 同量纲，匹配 from 或 to 皆可；
        但对 derivative / integral，我们要找的是**结果**等于目标量纲的关系。
        例如目标为力（M·L·T⁻²）时，F = -dU/dx 的 from 是 J（能量），
        只有按 to 匹配才能命中。按 from 匹配会漏掉全部泛函关系。

        所以规则是：等价关系两边都试，泛函关系只认 to。
        """
        out: list[tuple[Formula, Relation]] = []
        target = dim
        for f in self.formulas.values():
            for rel in f.relations:
                if rel.kind == "equivalence":
                    if self._expr_matches_dim(rel.from_, target) or \
                       self._expr_matches_dim(rel.to, target):
                        out.append((f, rel))
                else:
                    if self._expr_matches_dim(rel.to, target):
                        out.append((f, rel))
        return out

    def _expr_matches_dim(self, expr: str, dim: Dim) -> bool:
        """判断一个表达式（单位 id / 单位组合）的量纲是否等于目标。"""
        if not expr:
            return False
        u = self.find_unit(expr)
        if u is not None:
            return u.dim == dim
        try:
            _node, res = self.eval_expr(expr)
        except (P.ParseError, EvalError, DimError):
            return False
        return res.dim == dim

    def eval_expr(self, expr: str, ctx: EvalContext | None = None):
        """便捷入口：解析并求值一个单位表达式。"""
        if self.resolver is None:
            raise RegistryError("注册表尚未初始化 resolver")
        node = P.parse(expr)
        return node, evaluate(node, self.resolver, ctx)

    # -------- 符号表 --------

    def _build_canonicality(self) -> None:
        """给每个物理量打「中心度」分，用于同量纲组内的排序与跳转选择。

        依据是**公式库的真实结构**，而不是字母序（曾按 id 排序，导致
        `E_k` 跳到「弯矩」而「能量」被跳过——那是取巧排序的后果）。

        打分规则：
          +1  该量是某条公式的 lhs（结果量）——它由公式定义出来
          +0.5 该量在公式的 rhs 里出现（参与关系式）
          +0.2 它被 relations 的 from 字段引用（是某条关系的源头）

        得分高的就是该量纲的「主角」：能量强于功、成本强于弯矩，
        因为它们更多出现在公式左侧。
        """
        score: dict[str, float] = {}
        for qid in self.quantities:
            score[qid] = 0.0
        for f in self.formulas.values():
            lhs_hit = self._quantity_matching(f.lhs_symbol, f.lhs_dim)
            if lhs_hit:
                score[lhs_hit] = score.get(lhs_hit, 0.0) + 1.0
            for e in f.rhs_entries:
                raw = e.get("dim") or {}
                if not isinstance(raw, Mapping):
                    continue
                try:
                    d = Dim.from_mapping(raw)  # type: ignore[arg-type]
                except DimError:
                    continue
                hit = self._quantity_matching(str(e.get("symbol", "")), d)
                if hit:
                    score[hit] = score.get(hit, 0.0) + 0.5
        self.canonicality = score

    def _quantity_matching(self, symbol: str, dim: Dim) -> str:
        """找出 (符号, 量纲) 对应的物理量 id，没有则返回空串。

        符号匹配同时看 `symbol` 与 `aliases`——因为同一个物理量在不同
        公式里写法不同（能量既写 E_k 也写 E_p/W，力矩既写 M 也写 M_torque）。
        别名是真实数据，不是启发式：不认别名会导致中心度打分全部为 0，
        进而让同量纲组按字母序排名（`E_k` 跳到「弯矩」就是这么来的）。
        """
        if not symbol:
            return ""
        for q in self.quantities.values():
            if q.dim != dim:
                continue
            if q.symbol == symbol or symbol in q.aliases:
                return q.id
        return ""

    def rank_quantities(self, qs: list[Quantity]) -> list[Quantity]:
        """按中心度降序、id 升序排稳定序。

        中心度高的排前面：同量纲的 [能量, 功, 力矩, 弯矩] 里
        能量得分最高，所以它会被当作该量纲的代表。
        """
        return sorted(
            qs,
            key=lambda q: (-self.canonicality.get(q.id, 0.0), q.id),
        )

    def _build_symbol_glossary(self, raw: list[Mapping[str, Any]]) -> None:
        """把 symbol_glossary.json 的原始条目建成以 (符号, 量纲) 为键的索引。

        键必须是复合的：`U`（势能/电势）、`L`（角动量/电感）、
        `p`（动量/压强）、`I`（转动惯量/电流）四个符号同名不同义，
        只用符号做键会张冠李戴。这一条是被自检逼出来的——
        初版用单键存储，自检立刻报「U 命中 2 种量纲」。

        dim 允许省略：若该符号在全部公式签名里只出现过一种量纲，
        就自动采用它。出现多种时必须显式写，不猜。
        """
        inferred = self._infer_symbol_dims()
        out: dict[tuple[str, Dim], SymbolEntry] = {}

        for r in raw:
            sym = str(r.get("symbol", "")).strip()
            if not sym:
                raise RegistryError(f"符号表条目缺少 symbol：{r!r}")

            dim_raw = r.get("dim")
            if isinstance(dim_raw, Mapping) and dim_raw:
                try:
                    dim = Dim.from_mapping(dim_raw)
                except DimError as exc:
                    raise RegistryError(
                        f"符号表 {sym} 的 dim 非法：{exc}"
                    ) from exc
            else:
                dims = inferred.get(sym, set())
                if len(dims) != 1:
                    raise RegistryError(
                        f"符号表 {sym!r} 未写 dim，且无法从公式签名唯一推断"
                        f"（命中 {len(dims)} 种量纲）。请在 "
                        f"data/symbol_glossary.json 里显式写明 dim。"
                    )
                dim = next(iter(dims))

            key = (sym, dim)
            if key in out:
                raise RegistryError(
                    f"符号表里 (符号, 量纲) 重复：{sym} / {dim.format_si()}"
                )

            unit_sym, unit_zh = self._describe_dim(dim)
            jump, jump_zh = self._jump_target(sym, dim)
            out[key] = SymbolEntry(
                symbol=sym,
                zh=str(r.get("zh", "")),
                meaning=str(r.get("meaning", "")),
                unit_sym=unit_sym,
                unit_zh=unit_zh,
                dim=dim,
                jump=jump,
                jump_zh=jump_zh,
                resolved_by="glossary",
            )
        self.symbol_glossary = out

    def _infer_symbol_dims(self) -> dict[str, set[Dim]]:
        """收集每个符号在全部公式签名里出现过的量纲。"""
        out: dict[str, set[Dim]] = {}
        for f in self.formulas.values():
            entries: list[Mapping[str, Any]] = [{"symbol": f.lhs_symbol,
                                                 "dim": f.lhs_dim.to_json()}]
            entries.extend(f.rhs_entries)
            for e in entries:
                sym = str(e.get("symbol", ""))
                raw = e.get("dim") or {}
                if not sym or not isinstance(raw, Mapping):
                    continue
                try:
                    out.setdefault(sym, set()).add(Dim.from_mapping(raw))  # type: ignore[arg-type]
                except DimError:
                    continue
        return out

    def _describe_dim(self, dim: Dim) -> tuple[str, str]:
        """给一个量纲找出教科书里惯用的单位写法与中文名。

        返回 (单位写法, 中文名)。单位写法可能是多个并列（用空格分隔），
        例如力的量纲同时给 `N` 与 `kg·m/s^2`——前者是导出单位，
        后者是基本单位展开，教学上两个都有用。

        选择规则（顺序即优先级）：
          1. SI 导出单位（N、J、Pa、W…）——教科书就是这么写的
          2. SI 基本单位，但**排除带词头的**：质量的基准是 kg 不是 g，
             `g` 只因为符号短就会赢过 `kg`，那是错的
          3. 都没有时退回基本单位组合（M·L^-1·T^-2 之类）
        """
        # 无因次量（如角位移 θ）不显示单位，只说明它是纯数。
        # 曾错误地显示成 `sr`（球面度）——那是立体角单位，与角位移无关。
        if dim.is_dimensionless:
            return "", "无因次（纯数）"

        derived = sorted(
            (u for u in self.units.values()
             if u.dim == dim and u.system == "si-derived" and u.zh),
            key=lambda u: (len(u.symbol), u.id),
        )
        base = sorted(
            (u for u in self.units.values()
             if u.dim == dim and u.system == "si-base" and u.zh
             and not u.prefixable),
            key=lambda u: (len(u.symbol), u.id),
        )

        if derived and base:
            # 同时给导出单位与基本单位展开：N 与 kg·m/s²
            forms = f"{derived[0].symbol}　{base[0].symbol}"
            return forms, derived[0].zh
        if derived:
            return derived[0].symbol, derived[0].zh
        if base:
            return base[0].symbol, base[0].zh

        # 没有单一命名单位时，依次尝试：
        #   1. 手工表里的教科书惯用写法（N/m、Pa·s、J/(kg·K)…）
        #   2. 基本单位表达式（m/s^2、kg/m^3…）
        #   3. 量纲式（最后的兜底）
        # 两式相同时去重，否则会出现 `m/s^2　m/s^2` 这种重复显示。
        preferred = self._preferred_unit_expr(dim)
        base_expr = self._base_unit_expr(dim)
        if preferred and base_expr and preferred != base_expr:
            return f"{preferred}　{base_expr}", "常用组合"
        if preferred:
            return preferred, "常用组合"
        if base_expr:
            return base_expr, "基本单位组合"
        return dim.format_si(superscript=False), "基本单位组合"

    # 基本维度 → 该维度的 SI 基本单位符号。
    # 刻意排除带词头的单位：质量的基准是 kg 而不是 g。
    _BASE_UNIT_BY_DIM = {
        "M": "kg", "L": "m", "T": "s", "I": "A",
        "TH": "K", "N": "mol", "J": "cd",
    }

    # 常见量纲 → 教科书惯用的单位写法。穷举不可能，所以只覆盖
    # 「没有单一名命单位、但教学上确实有惯用写法」的那些。
    # 键用 dim.to_json() 的元组形式，便于序列化比较。
    # 这份表是数据性的，不是为了性能——放代码里是因为它太短，
    # 且与 _BASE_UNIT_BY_DIM 配套阅读。
    _PREFERRED_UNITS: tuple[tuple[dict[str, int], str], ...] = (
        ({"M": 1, "T": -2}, "N/m"),              # 弹簧刚度、表面张力
        ({"M": 1, "L": -1, "T": -1}, "Pa·s"),    # 动力黏度
        ({"L": 2, "T": -2, "TH": -1}, "J/(kg·K)"),  # 比热容
        ({"L": 2, "T": -1}, "m^2/s"),            # 运动黏度、扩散系数
        ({"M": 1, "L": -3}, "kg/m^3"),           # 密度
        ({"L": 1, "T": -1}, "m/s"),              # 速度
        ({"L": 1, "T": -2}, "m/s^2"),            # 加速度
        ({"T": -1}, "Hz"),                       # 频率
        ({"T": -2}, "rad/s^2"),                  # 角加速度（工程写法带 rad）
        ({"M": 1, "L": 1, "T": -1}, "kg·m/s"),   # 动量
        ({"I": 1, "L": -2}, "A/m^2"),            # 电流密度
        ({"M": 1, "L": 2, "T": -2, "I": -1}, "Wb"),  # 磁通量
    )

    def _preferred_unit_expr(self, dim: Dim) -> str:
        """查手工表的惯用单位写法，没有则返回空串。"""
        powers = dim.to_json()
        for want, expr in self._PREFERRED_UNITS:
            if want == powers:
                return expr
        return ""

    def _base_unit_expr(self, dim: Dim) -> str:
        """把量纲投影成基本单位表达式，如 L·T⁻² → m/s^2。

        分子放正幂、分母放负幂；分母多项加括号。
        这样输出的字符串可以直接喂给 parser（先做上标→^ 的替换）。
        """
        numer: list[str] = []
        denom: list[str] = []
        for key in ("M", "L", "T", "I", "TH", "N", "J"):
            p = dim.power_of(key)
            if not p:
                continue
            sym = self._BASE_UNIT_BY_DIM.get(key)
            if not sym:
                continue
            if p > 0:
                numer.append(sym if p == 1 else f"{sym}^{p}")
            else:
                q = -p
                denom.append(sym if q == 1 else f"{sym}^{q}")
        if not numer:
            numer = ["1"]
        expr = "·".join(numer)
        if denom:
            d = "·".join(denom)
            expr += f"/{d}" if len(denom) == 1 else f"/({d})"
        return expr

    def _jump_target(self, symbol: str, dim: Dim) -> tuple[str, str]:
        """找出该符号可跳转的物理量，三跳（逐步放宽）：

          第一跳 (符号, 量纲) 同时命中 —— 最精确，如 F→力、E_k→动能
          第二跳 仅量纲命中 —— 符号名对不上但量纲确定时使用，
                 如 `t` 虽不是任何物理量的符号，但它的量纲就是时间
          第三跳 都没中则不给跳转

        第一跳可能命中多个：例如 `U` 既是势能也是功的别名，两者量纲
        还相同（都是 M·L²·T⁻²），此时按中心度取主角。

        注意 `U` 在 F = -dU/dx 里语义上就是势能，但全局排序会让「功」
        抢先（work 与 torque 因作为公式 lhs 最多而排前）。这类同名
        不同义的消歧在数据层用显式符号处理（如压强从 p 改为 p_press），
        而不是在这里堆特例。
        """
        by_symbol = [
            q for q in self.quantities.values()
            if q.dim == dim and (q.symbol == symbol or symbol in q.aliases)
        ]
        if by_symbol:
            hit = self.rank_quantities(by_symbol)[0]
            return hit.id, hit.zh

        by_dim = self.quantities_with_dim(dim)
        if by_dim:
            return by_dim[0].id, by_dim[0].zh

        return "", ""

    def resolve_symbol(self, symbol: str, dim: Dim) -> SymbolEntry:
        """解析一个公式符号的含义。两跳：

          第一跳  symbol_glossary.json 里的显式定义（更精确的中文名，
                  以及同名不同义时的消歧说明）
          第二跳  (符号, 量纲) 在物理量表里唯一匹配

        两跳都不中时返回一个「未登记」条目而不是报错——符号表是
        辅助信息，不该因为它缺失就让整个查询失败。
        """
        g = self.symbol_glossary.get((symbol, dim))
        if g is not None:
            return g

        hits = [
            q for q in self.quantities.values()
            if q.symbol == symbol and q.dim == dim
        ]
        if not hits:
            hits = [
                q for q in self.quantities.values()
                if q.dim == dim and symbol in q.aliases
            ]
        if len(hits) == 1:
            q = hits[0]
            unit_sym, unit_zh = self._describe_dim(dim)
            return SymbolEntry(
                symbol=symbol,
                zh=q.zh,
                meaning=q.note or f"{q.zh}（{q.category}）",
                unit_sym=unit_sym,
                unit_zh=unit_zh,
                dim=dim,
                jump=q.id,
                jump_zh=q.zh,
                resolved_by="lookup",
            )

        # 同符号同量纲命中多个：不猜，展示为「同名多义」
        if len(hits) > 1:
            unit_sym, unit_zh = self._describe_dim(dim)
            return SymbolEntry(
                symbol=symbol,
                zh="、".join(q.zh for q in hits),
                meaning="该符号对应多个物理量，按所在公式判断",
                unit_sym=unit_sym,
                unit_zh=unit_zh,
                dim=dim,
                resolved_by="ambiguous",
            )

        # 该符号在字典里有登记，但登记的是别的量纲：
        # 说明这是「同名不同义」，借用它的中文名并提示按公式判断
        others = [e for (s, _d), e in self.symbol_glossary.items() if s == symbol]
        if others:
            unit_sym, unit_zh = self._describe_dim(dim)
            return SymbolEntry(
                symbol=symbol,
                zh=others[0].zh,
                meaning="该符号在不同公式里含义不同，按所在公式判断",
                unit_sym=unit_sym,
                unit_zh=unit_zh,
                dim=dim,
                resolved_by="glossary-other-dim",
            )

        unit_sym, unit_zh = self._describe_dim(dim)
        return SymbolEntry(
            symbol=symbol,
            zh="（未登记）",
            meaning="该符号尚未登记中文名与物理意义",
            unit_sym=unit_sym,
            unit_zh=unit_zh,
            dim=dim,
            resolved_by="missing",
        )

    def formula_glossary(self, formula: Formula) -> list[SymbolEntry]:
        """一条公式的完整符号表（按签名出现顺序）。"""
        seen: set[tuple[str, Dim]] = set()
        out: list[SymbolEntry] = []
        entries: list[tuple[str, Dim]] = [(formula.lhs_symbol, formula.lhs_dim)]
        for e in formula.rhs_entries:
            raw = e.get("dim") or {}
            if not isinstance(raw, Mapping):
                continue
            try:
                entries.append((str(e.get("symbol", "")), Dim.from_mapping(raw)))  # type: ignore[arg-type]
            except DimError:
                continue
        for sym, dim in entries:
            if not sym:
                continue
            key = (sym, dim)
            if key in seen:
                continue
            seen.add(key)
            out.append(self.resolve_symbol(sym, dim))
        return out

    # -------- 自检 --------

    def selfcheck(self) -> list[str]:
        """全面自检，返回问题列表（空列表 = 全部通过）。

        设计上刻意不 fail-fast，而是收集全部问题一次性报出——
        否则修一条数据要跑一次，迭代太慢。
        """
        problems: list[str] = []
        problems += self._check_algebra_rules()
        problems += self._check_domains()
        problems += self._check_units()
        problems += self._check_quantities()
        problems += self._check_formulas()
        problems += self._check_relations()
        problems += self._check_symbol_glossary()
        return problems

    def _check_domains(self) -> list[str]:
        """分类体系自检。

        分类是首页浏览的骨架，一旦有条目漏分类就从首页消失（搜不到也点不到），
        所以这里把「必须能分类」当硬约束：
          - 每个物理量必须有 domain，且该 domain 必须存在于 domains.json
          - domain 的 order 不能重复（否则首页顺序不确定）
          - 空领域要提示（不是错误，但要在数据里可见）
        """
        out: list[str] = []

        orders: dict[int, list[str]] = {}
        for d in self.domains.values():
            orders.setdefault(d.order, []).append(d.id)
        for order, ids in sorted(orders.items()):
            if len(ids) > 1:
                out.append(
                    f"[领域] order={order} 被多个领域占用：{sorted(ids)}，"
                    f"首页排序会不确定"
                )

        for qid, q in sorted(self.quantities.items()):
            if not q.domain:
                out.append(
                    f"[领域] 物理量 {qid}（{q.zh}）没有 domain 字段，"
                    f"它将无法从首页浏览到"
                )
                continue
            if q.domain not in self.domains:
                out.append(
                    f"[领域] 物理量 {qid} 的 domain={q.domain!r} "
                    f"不在 domains.json 里；合法值：{sorted(self.domains)}"
                )

        # 空领域提示（不算错误，但明确列出来，避免以为漏了数据）
        for d in self.domains_ordered():
            n = len(self.quantities_in_domain(d.id))
            if n == 0:
                out.append(
                    f"[领域] 领域「{d.zh}」({d.id}) 下没有任何物理量——"
                    f"首页会显示为空栏。要么补充条目，要么从 domains.json 移除"
                )
        return out

    def _check_symbol_glossary(self) -> list[str]:
        """符号表自检。

        重点是抓「张冠李戴」：一个符号的含义是绑定到量纲的，
        如果某条符号表条目的量纲与它在公式签名里的量纲不符，
        界面上就会显示错误的中文名与物理意义——这类错误看起来
        完全正常，只有靠验算才发现。
        """
        out: list[str] = []
        inferred = self._infer_symbol_dims()

        for (sym, dim), entry in sorted(
            self.symbol_glossary.items(), key=lambda kv: (kv[0][0], kv[0][1].format_si())
        ):
            dims = inferred.get(sym, set())
            if not dims:
                out.append(
                    f"[符号表] {sym!r} 未在任何公式签名里出现，"
                    f"是多余的条目（请删除或修正符号名）"
                )
                continue
            if dim not in dims:
                out.append(
                    f"[符号表] {sym!r} 声明的量纲 {dim.format_si()} "
                    f"与它在公式签名里的量纲不符；"
                    f"签名为 {sorted(d.format_si() for d in dims)}"
                )
            if not entry.zh:
                out.append(f"[符号表] {sym!r}（{dim.format_si()}）缺少中文名 zh")
            if not entry.meaning:
                out.append(
                    f"[符号表] {sym!r}（{dim.format_si()}）缺少物理意义 meaning"
                )

        # 反向检查：公式签名里的每个符号都应能被解析出含义
        for f in sorted(self.formulas.values(), key=lambda x: x.id):
            for entry in self.formula_glossary(f):
                if entry.resolved_by == "missing":
                    out.append(
                        f"[符号表] 公式 {f.id} 的符号 "
                        f"{entry.symbol!r}（{entry.dim.format_si()}）"
                        f"两跳解析都没命中，请在 symbol_glossary.json 里补上"
                    )
        return out

    def _check_algebra_rules(self) -> list[str]:
        out: list[str] = []
        for op, rule in sorted(self.algebra_rules.items()):
            if rule.transform not in algebra.RULE_IMPLEMENTATIONS:
                out.append(
                    f"[代数规则] {op} 声明了未实现的变换 "
                    f"{rule.transform!r}；已实现："
                    f"{sorted(algebra.RULE_IMPLEMENTATIONS)}"
                )
        # 反向检查：物理公式用到的 op 必须在规则表里声明
        for f in self.formulas.values():
            for op in f.ops:
                if op not in self.algebra_rules:
                    out.append(
                        f"[代数规则] 公式 {f.id} 使用了未声明的运算 {op!r}"
                    )
        # 变量名映射表引用的单位必须存在，否则 wrt 会静默解析失败
        for sym, vu in sorted(self.variable_units.items()):
            try:
                self.eval_expr(vu.unit)
            except (P.ParseError, EvalError, DimError) as exc:
                out.append(
                    f"[变量映射] {sym} 指向的单位 {vu.unit!r} 无法求值：{exc}"
                )
        # 公式里出现的 wrt 必须能解析出量纲，否则求导/积分分支会静默消失
        for f in self.formulas.values():
            for rel in f.relations:
                if rel.kind in ("derivative", "integral") and rel.wrt:
                    if self.var_dim(rel.wrt) is None:
                        out.append(
                            f"[公式] {f.id} 的 {rel.kind} 关系里 wrt={rel.wrt!r} "
                            f"无法解析出量纲（既不在 variable_units.json，"
                            f"也不是单位符号）"
                        )
        return out

    def _check_units(self) -> list[str]:
        out: list[str] = []
        for uid, u in sorted(self.units.items()):
            if u.factor == 0:
                out.append(f"[单位] {uid} 的换算因子为 0，除法会崩")
            if u.system not in ("si-base", "si-derived", "accepted", "mixed", "other"):
                out.append(f"[单位] {uid} 的 system={u.system!r} 不在允许集合内")
        return out

    def _check_quantities(self) -> list[str]:
        out: list[str] = []
        for qid, q in sorted(self.quantities.items()):
            for label, exprs in (
                ("unit_forms", [e for e in q.unit_forms]),
                ("decompositions", [d.expr for d in q.decompositions]),
            ):
                for expr in exprs:
                    try:
                        _node, res = self.eval_expr(expr)
                    except (P.ParseError, EvalError, DimError) as exc:
                        out.append(
                            f"[物理量] {qid}.{label} 表达式 {expr!r} 无法求值：{exc}"
                        )
                        continue
                    # 注意：力矩与能量量纲相同是物理事实，不是数据错误。
                    # homogeneous_with 正是用来标注这种「同量纲异构」的。
                    if res.dim != q.dim:
                        out.append(
                            f"[物理量] {qid}.{label} 表达式 {expr!r} 量纲为 "
                            f"{res.dim.format_si()}，"
                            f"但条目声明的是 "
                            f"{q.dim.format_si()}"
                        )
            if q.structure not in ("scalar", "vector", "matrix", "tensor"):
                out.append(
                    f"[物理量] {qid} 的 structure={q.structure!r} 非法"
                )
            for other in q.homogeneous_with:
                if other not in self.quantities:
                    out.append(
                        f"[物理量] {qid}.homogeneous_with 引用了不存在的 {other!r}"
                    )
                elif self.quantities[other].dim != q.dim:
                    out.append(
                        f"[物理量] {qid} 声明与 {other} 同量纲，但 "
                        f"{q.dim.format_si()} != "
                        f"{self.quantities[other].dim.format_si()}"
                    )
        return out

    def _check_formulas(self) -> list[str]:
        out: list[str] = []
        for fid, f in sorted(self.formulas.items()):
            # 1) pattern 必须存在
            if self.patterns and f.pattern not in self.patterns:
                out.append(f"[公式] {f.id} 的 pattern={f.pattern!r} 未定义")
            # 2) 齐次性：lhs 与 rhs 按 ops 推导后必须相等
            try:
                derived = _derive_rhs_dim(f)
            except (P.ParseError, EvalError, DimError, algebra.AlgebraError) as exc:
                out.append(f"[公式] {f.id} 的量纲推导失败：{exc}")
                continue
            if derived != f.lhs_dim:
                out.append(
                    f"[公式] {f.id} 量纲不齐次：lhs {f.lhs_symbol} 是 "
                    f"{f.lhs_dim.format_si()}，"
                    f"但 rhs 按 ops={list(f.ops)} 推导得 "
                    f"{derived.format_si()}"
                )
            # 3) 同一签名里，一个符号只能有一个量纲
            #
            # 这条是补出来的：`magnetic_force_lorentz` 的 tex 写的是
            # `F = q·v·B`，`lhs.symbol` 却写成了 `B`。只比量纲的齐次性检查
            # 完全放行（lhs 的量纲确实等于 rhs 推导值），但符号表里因此
            # 多出一行「B = 磁场力」而且没有跳转链接——**看起来完全正常**。
            #
            # 一条公式是一个等式，同一个字母在等式两侧只能指同一个量，
            # 所以「符号 → 量纲」在一条签名内必须是单值函数。
            out.extend(self._check_signature_symbols(f))
        return out

    def _check_signature_symbols(self, f: Formula) -> list[str]:
        out: list[str] = []
        seen: dict[str, tuple[Dim, str]] = {f.lhs_symbol: (f.lhs_dim, "lhs")}
        for entry in f.rhs_entries:
            sym = str(entry.get("symbol", ""))
            if not sym:
                continue
            raw = entry.get("dim") or {}
            if not isinstance(raw, Mapping):
                continue
            try:
                dim = Dim.from_mapping(raw)  # type: ignore[arg-type]
            except DimError:
                continue
            prev = seen.get(sym)
            if prev is not None and prev[0] != dim:
                out.append(
                    f"[公式] {f.id} 的符号 {sym!r} 在 {prev[1]} 处量纲为 "
                    f"{prev[0].format_si()}，在 rhs 处却是 {dim.format_si()}；"
                    f"同一个字母在一条等式里必须指同一个量"
                )
                continue
            seen[sym] = (dim, "rhs")
        return out

    def _check_relations(self) -> list[str]:
        out: list[str] = []
        known_forms = _all_known_forms(self)
        for fid, f in sorted(self.formulas.items()):
            for rel in f.relations:
                if rel.kind not in (
                    "equivalence",
                    "derivative",
                    "integral",
                    "matrix",
                ):
                    out.append(
                        f"[关系] 公式 {f.id} 的 kind={rel.kind!r} 非法"
                    )
                for side, text in (("from", rel.from_), ("to", rel.to)):
                    if not text:
                        out.append(f"[关系] 公式 {f.id} 的 {side} 为空")
                        continue
                    # from/to 可以是单位 id、符号、或单位组合表达式
                    if self.find_unit(text) is not None:
                        continue
                    if text in known_forms:
                        continue
                    try:
                        self.eval_expr(text)
                    except (P.ParseError, EvalError, DimError):
                        out.append(
                            f"[关系] 公式 {f.id} 的 {side}={text!r} 既不是已知单位，"
                            f"也无法作为单位表达式求值"
                        )
        return out

    # -------- 自检失败即拒绝启动 --------

    def selfcheck_or_raise(self) -> None:
        problems = self.selfcheck()
        if problems:
            raise RegistryError(
                f"数据自检未通过，共 {len(problems)} 项问题：\n  - "
                + "\n  - ".join(problems)
            )


# ------------------------------------------------------------------ 构造辅助


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RegistryError(f"缺少数据文件：{path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"无法读取 {path}：{exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError(f"{path} 顶层必须是对象")
    return doc


def _build_unit(raw: Mapping[str, Any]) -> Unit:
    try:
        uid = str(raw["id"])
        dim = Dim.from_mapping(raw.get("dim") or {})
    except (KeyError, DimError) as exc:
        raise RegistryError(f"单位条目非法：{raw!r}（{exc}）") from exc
    return Unit(
        id=uid,
        zh=str(raw.get("zh", uid)),
        symbol=str(raw.get("symbol", uid)),
        aliases=tuple(str(a) for a in raw.get("aliases", [])),
        dim=dim,
        factor=float(raw.get("factor", 1.0)),
        prefixable=bool(raw.get("prefixable", False)),
        system=str(raw.get("system", "other")),
        note=str(raw.get("note", "")),
    )


def _build_quantity(raw: Mapping[str, Any]) -> Quantity:
    try:
        qid = str(raw["id"])
        dim = Dim.from_mapping(raw.get("dim") or {})
    except (KeyError, DimError) as exc:
        raise RegistryError(f"物理量条目非法：{raw!r}（{exc}）") from exc
    decomps = tuple(
        Decomposition(
            expr=str(d["expr"]),
            zh=str(d.get("zh", "")),
            note=str(d.get("note", "")),
        )
        for d in raw.get("decompositions", [])
    )
    return Quantity(
        id=qid,
        zh=str(raw.get("zh", qid)),
        symbol=str(raw.get("symbol", qid)),
        dim=dim,
        category=str(raw.get("category", "未分类")),
        structure=str(raw.get("structure", "scalar")),
        unit_forms=tuple(str(u) for u in raw.get("unit_forms", [])),
        decompositions=decomps,
        aliases=tuple(str(a) for a in raw.get("aliases", [])),
        homogeneous_with=tuple(str(h) for h in raw.get("homogeneous_with", [])),
        note=str(raw.get("note", "")),
        domain=str(raw.get("domain", "")),
        defined_as=str(raw.get("defined_as", "")),
        definition_note=str(raw.get("definition_note", "")),
    )


def _build_formula(raw: Mapping[str, Any]) -> Formula:
    try:
        fid = str(raw["id"])
        sig = raw["signature"]
        lhs = sig["lhs"]
        lhs_dim = Dim.from_mapping(lhs.get("dim") or {})
        rhs_entries = tuple(sig.get("rhs", []))
        ops = tuple(str(o) for o in sig.get("ops", []))
    except (KeyError, DimError) as exc:
        raise RegistryError(f"公式条目非法：{raw!r}（{exc}）") from exc

    relations = tuple(
        Relation(
            kind=str(r.get("kind", "equivalence")),
            from_=str(r.get("from", "")),
            to=str(r.get("to", "")),
            via=str(r.get("via", raw.get("tex", fid))),
            note=str(r.get("note", "")),
            wrt=str(r.get("wrt", "")),
            order=int(r.get("order", 1)),
        )
        for r in raw.get("relations", [])
    )

    return Formula(
        id=fid,
        tex=str(raw.get("tex", "")),
        zh=str(raw.get("zh", fid)),
        category=str(raw.get("category", "未分类")),
        pattern=str(raw.get("pattern", "")),
        ops=ops,
        lhs_symbol=str(lhs.get("symbol", "?")),
        lhs_dim=lhs_dim,
        lhs_structure=str(lhs.get("structure", "scalar")),
        rhs_entries=rhs_entries,
        relations=relations,
        defines=str(raw.get("defines", "")),
    )


def _all_known_forms(reg: Registry) -> set[str]:
    """把「物理量符号 + 单位组合字符串」收集成集合，供关系校验快速命中。"""
    forms: set[str] = set()
    for q in reg.quantities.values():
        forms.add(q.symbol)
        forms.add(q.zh)
        forms.update(q.unit_forms)
        forms.update(d.expr for d in q.decompositions)
    for u in reg.units.values():
        forms.add(u.symbol)
        forms.add(u.zh)
        forms.add(u.id)
        forms.update(u.aliases)
    return forms


def _entry_dim(entry: Mapping[str, Any]) -> Dim:
    d = entry.get("dim") or {}
    if not isinstance(d, Mapping):
        raise RegistryError(f"签名条目的 dim 必须是对象：{entry!r}")
    return Dim.from_mapping(d)  # type: ignore[arg-type]


def _entry_exp(entry: Mapping[str, Any]) -> int:
    raw = entry.get("exp", 1)
    value = int(raw)
    if value == 0:
        raise RegistryError(f"签名条目的幂次不能为 0：{entry!r}")
    return value


def _derive_rhs_dim(formula: Formula) -> Dim:
    """按 ops 从签名条目推导 rhs 的量纲。

    这是公式库齐次性检查的核心（设计文档 6.5）。三类条目区别对待：

      普通条目            量纲进入初始累积（乘积型的「各因子相乘」）
      differentiator      只是求导的「自变量」，不参与相乘，由 derivative 消费
      integrator          只是积分的「积分变量」，不参与相乘，由 integral 消费
      divisor             由 div 消费，不进入初始累积

    初始累积 = 普通条目量纲之和，各自按 exp 取幂。

    分母的 exp 也必须生效（`E_v = I_v/r^2` 里 r 是 divisor 且 exp=2，
    不减两次就少了一阶长度）。初版只累加 dim 不看 exp，是个真实的漏洞，
    只是当时数据里没有带幂次的分母才没暴露。

    为什么必须排除 differentiator / integrator：这是个真实踩过的坑。
    F = -dU/dx 里如果把 x 也累加进去，累积值先加了 L，求导时又减去 L，
    等于白算——量纲检查会静默通过但推导结果是错的。
    判据来自运算语义，不是来自数据形态。
    """
    entries = list(formula.rhs_entries)
    if not entries:
        raise RegistryError(f"公式 {formula.id} 的 rhs 为空")

    var_roles = ("differentiator", "integrator", "divisor")

    acc = Dim({})
    for entry in entries:
        if str(entry.get("role", "")) in var_roles:
            continue
        dim = _entry_dim(entry)
        exp = _entry_exp(entry)
        acc = acc + (dim.scaled(exp) if exp != 1 else dim)

    var_dims = {
        str(e.get("role", "")): _entry_dim(e)
        for e in entries
        if e.get("role") in ("differentiator", "integrator")
    }
    var_orders = {
        str(e.get("role", "")): int(e.get("order", 1))
        for e in entries
        if e.get("role") in ("differentiator", "integrator")
    }

    for op in formula.ops:
        # 普通条目与带 exp 的条目已在初始累积里体现，这里只处理
        # 「改变累积值」的算子型运算。
        if op in ("mul", "pow", "neg", "add"):
            continue

        if op == "div":
            divisors = [
                _entry_dim(e).scaled(_entry_exp(e))
                for e in entries
                if e.get("role") == "divisor"
            ]
            if not divisors:
                raise RegistryError(
                    f"公式 {formula.id} 声明了 div 但没有 role=divisor 的条目"
                )
            for d in divisors:
                acc = acc - d
            continue

        if op in ("derivative", "gradient", "divergence", "curl"):
            vd = var_dims.get("differentiator")
            if vd is None:
                raise RegistryError(
                    f"公式 {formula.id} 声明了 {op} 但没有 role=differentiator 的条目"
                )
            acc = algebra.sub_by_var(acc, vd, var_orders.get("differentiator", 1))
            continue

        if op == "integral":
            vd = var_dims.get("integrator")
            if vd is None:
                raise RegistryError(
                    f"公式 {formula.id} 声明了 integral 但没有 role=integrator 的条目"
                )
            acc = algebra.add_by_var(acc, vd, var_orders.get("integrator", 1))
            continue

        raise RegistryError(f"公式 {formula.id} 使用了自检不支持的运算 {op!r}")

    return acc
