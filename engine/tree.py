"""建树：把量纲关系组织成可展示的树。

设计文档 2.6 的核心：**边的来源优先级是公式库 → 分解表 → 搜索**。
本模块实现前两级（搜索在 search.py，本切片未做）。

树的两类分支刻意分开呈现（设计文档 2.2）：
  ① 等价重写  N → kg·m/s²        「同一个量，换一种写法」
  ② 泛函生成  J → dJ/dx → N      「求导后量纲降低一阶，恰好是力的量纲」
混在一起会让学生以为两者是同一回事，所以边带 kind、界面用不同标记。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from . import parser as P
from .dim import Dim, DimError
from .evaluator import EvalError, evaluate, Operand, UnitResolver
from .registry import Formula, Quantity, Registry, Relation, Unit
from .search import search_decompositions
from .submissions import STATUS_DISPUTED, STATUS_VERIFIED, SubmissionStore

# 边类型的展示信息。界面直接读这张表，避免 UI 里散落魔法字符串。
EDGE_KINDS: dict[str, dict[str, str]] = {
    "equivalence": {
        "zh": "等价",
        "label": "等价重写",
        "explain": "同一个量的另一种写法，量纲完全相同。",
    },
    "derivative": {
        "zh": "求导",
        "label": "求导生成",
        "explain": "对某个量求导后量纲降低一阶，其量纲恰好是这个量。",
    },
    "integral": {
        "zh": "积分",
        "label": "积分生成",
        "explain": "对某个量积分后量纲增加，其量纲恰好是这个量。",
    },
    "matrix": {
        "zh": "矩阵",
        "label": "矩阵运算",
        "explain": "矩阵运算（迹、行列式、矩阵乘法）后的元素量纲。",
    },
    "search": {
        "zh": "枚举",
        "label": "算法枚举",
        "explain": "算法枚举所得，暂未找到对应的现实物理式子。",
    },
    "submitted": {
        "zh": "补录",
        "label": "用户补录",
        "explain": "由使用者提交并经核实为真实存在的物理式子。"
                   "这是「工具找不到、但现实中确实有」的那一类组合。",
    },
    "disputed": {
        "zh": "存疑",
        "label": "存疑补录",
        "explain": "使用者提交过，但核实未通过或尚无定论。保留原文以供参考，"
                   "不计入「依据现实物理式子」的关系。",
    },
}


@dataclass
class Source:
    """一条边的来源——这是「依据现实物理式子」的可见证据。"""

    formula_id: str = ""
    tex: str = ""
    formula_zh: str = ""
    note: str = ""
    pattern: str = ""
    pattern_zh: str = ""
    # 用户补录来源（仅 submitted / disputed 分支有值）
    submitted_id: str = ""
    submitted_status: str = ""
    submitted_source: str = ""
    # 该边是不是把公式库里的关系**反读**得到的。
    #
    # 例：公式库写的是 F = -dU/dx（derivative J→N）。微积分基本定理保证
    # U = -∫F dx 同样成立，所以「能量」页面上的「力对 x 积分」这条边，
    # 依据与「力」页面上的「能量对 x 求导」是**同一条公式**，不是新造的。
    # 标出来是为了让界面能说清「这条是反读来的」，而不是假装另有一条式子。
    inverse: bool = False
    inverse_of: str = ""   # 原方向的表达式，用于提示「等同于 X → Y」
    # 补录种类（仅 submitted / disputed 分支有值），界面据此显示它补的是哪个槽位
    submitted_kind: str = ""

    @property
    def is_from_formula(self) -> bool:
        return bool(self.formula_id)

    @property
    def is_from_submission(self) -> bool:
        return bool(self.submitted_id)


@dataclass
class Branch:
    """树上的一个分支。"""

    kind: str
    label: str
    short_label: str
    explain: str
    expr: str
    zh: str
    dim: Dim
    source: Source
    # 该分支指向的已知物理量（同量纲的条目），供界面做「这也正是…」提示。
    # 存 dict 而不是字符串：界面需要 id 才能做跳转链接。
    points_to: list[dict[str, str]] = field(default_factory=list)
    children: list["Branch"] = field(default_factory=list)

    def walk(self) -> Iterable["Branch"]:
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class EntityView:
    """一个物理量/单位的完整视图。同量纲异构会产出多个 EntityView。"""

    id: str
    zh: str
    symbol: str
    category: str
    structure: str
    dim: Dim
    dim_zh: str
    unit_forms: list[str]
    # 常见写法的逐条展开：{expr, zh, note}。
    # zh 由求值器沿 AST 拼出（「千克·米每二次方秒」这种读法），
    # note 是数据里手工写的补充说明（可能为空）。
    # 存在的理由：用户报告「有些量纲的常见写法下方有描述，有些没有」——
    # 原先只有裸字符串列表，描述只能靠 note 字段，于是页面结构随数据漂移。
    unit_form_details: list[dict[str, str]] = field(default_factory=list)
    note: str = ""
    # 「没有定义式」是不是正确答案（si-base / umbrella / 空），见页面规范 §3
    defined_as: str = ""
    definition_note: str = ""
    domain: str = ""
    domain_zh: str = ""
    equivalent: list[Branch] = field(default_factory=list)
    generated: list[Branch] = field(default_factory=list)
    # 算法枚举分支：默认不计算（有成本），由 with_search 按需填充
    searched: list[Branch] = field(default_factory=list)
    search_ran: bool = False
    # 用户补录：已核实的进 submitted（参与「依据现实式子」的展示），
    # 未定论的进 disputed（只展示，带存疑标记）
    submitted: list[Branch] = field(default_factory=list)
    disputed: list[Branch] = field(default_factory=list)
    homogeneous: list[dict[str, str]] = field(default_factory=list)
    formulas: list[dict[str, object]] = field(default_factory=list)
    # 定义式：把「这个量怎么被定义出来的」单独列出（用户要求单列，
    # 即使与下方 formulas 重复也保留——定义式是理解一个量的入口，
    # 埋在公式列表里会被忽略）。
    definitions: list[dict[str, object]] = field(default_factory=list)


@dataclass
class LookupResult:
    """查询结果。可能对应多个同量纲条目，所以是列表。"""

    query: str
    matched_kind: str  # quantity | unit | unknown
    entities: list[EntityView] = field(default_factory=list)
    error: str = ""
    # 候选建议：精确匹配失败但有多个可能时给出，让用户自己选。
    # 见 registry.suggest_quantities 的说明——单字母符号天然有歧义，
    # 猜一个不如列出来。
    suggestions: list[dict[str, str]] = field(default_factory=list)


# ------------------------------------------------------------------ 建树


class TreeBuilder:
    """从注册表构建展示树。

    深度限制是必要的：等价关系会形成环（N → J/m → N），
    泛函关系也会回到已知量（dJ/dx → N）。用 visited 集合断环，
    用 max_depth 限制规模，绝不做无界展开。
    """

    def __init__(
        self,
        reg: Registry,
        max_depth: int = 2,
        submissions: "SubmissionStore | None" = None,
    ) -> None:
        if reg.resolver is None:
            raise RuntimeError("注册表未初始化 resolver")
        self.reg = reg
        self.resolver: UnitResolver = reg.resolver
        self.max_depth = max_depth
        # 提交存储是可选的：没传就没有补录分支，其余功能不受影响。
        # 这样测试与 CLI 不必为了建树而准备用户数据。
        self.submissions = submissions

    # -------- 入口 --------

    def lookup(self, query: str, max_depth: int | None = None) -> LookupResult:
        """主入口：接受中文名、符号、别名、或单位组合表达式。"""
        depth = self.max_depth if max_depth is None else max_depth
        text = (query or "").strip()
        if not text:
            return LookupResult(query=query, matched_kind="unknown", error="请输入名称")

        # 1) 物理量（先试精确匹配）
        q = self.reg.find_quantity(text)
        if q is not None:
            # 精确命中的同时，如果还有别的候选，一并给出——用户可以
            # 直接跳到相关条目，不必再输一次。
            # 例：输入「势」精确命中「势能」，但「电势能/化学势能/重力势能」
            # 也是用户可能想找的，列出来比只给一个更有用。
            others = [
                s for s in self.reg.suggest_quantities(text)
                if s.id != q.id
            ]
            return LookupResult(
                query=text,
                matched_kind="quantity",
                entities=self._entities_for_dim(q.dim, depth, focus=q),
                suggestions=[
                    {
                        "id": s.id, "zh": s.zh, "symbol": s.symbol,
                        "category": s.category,
                        "dim_si": s.dim.format_si(),
                        "unit_forms": "、".join(s.unit_forms[:3]),
                        "note": (s.note or "")[:80],
                    }
                    for s in others
                ],
            )

        # 2) 单位注册表
        u = self.reg.find_unit(text)
        if u is not None:
            return LookupResult(
                query=text,
                matched_kind="unit",
                entities=self._entities_for_dim(u.dim, depth),
            )

        # 3) 当作单位表达式尝试求值
        try:
            _node, res = self.reg.eval_expr(text)
        except (P.ParseError, EvalError) as exc:
            # 4) 都失败后给出候选建议，而不是只报「找不到」。
            #    这是修「检索 I 无结果」的方式：I 同时是电流、冲量、
            #    转动惯量的符号，列出候选让用户选比猜一个更诚实。
            sugs = self.reg.suggest_quantities(text)
            if sugs:
                return LookupResult(
                    query=text,
                    matched_kind="suggestion",
                    error="",
                    suggestions=[
                        {
                            "id": q.id,
                            "zh": q.zh,
                            "symbol": q.symbol,
                            "category": q.category,
                            "dim_si": q.dim.format_si(),
                            "unit_forms": "、".join(q.unit_forms[:3]),
                            "note": (q.note or "")[:80],
                        }
                        for q in sugs
                    ],
                )
            return LookupResult(
                query=text,
                matched_kind="unknown",
                error=f"无法识别「{text}」：{exc}",
            )
        return LookupResult(
            query=text,
            matched_kind="unit",
            entities=self._entities_for_dim(res.dim, depth),
        )

    # -------- 实体视图 --------

    def _entities_for_dim(
        self, dim: Dim, depth: int, focus: Quantity | None = None
    ) -> list[EntityView]:
        """按量纲找出所有条目，每个条目一个视图。

        focus 是用户直接输入的那个条目——把它排在最前面，
        因为用户最想看的就是它，同量纲的其他条目是补充信息。
        """
        qs = self.reg.quantities_with_dim(dim)
        if focus is not None:
            qs.sort(key=lambda x: (x.id != focus.id, x.id))
        if not qs:
            # 没有对应物理量条目：仍然给出一个匿名视图，展示单位组合
            return [self._anonymous_view(dim, depth)]

        views: list[EntityView] = []
        for q in qs:
            views.append(self._view_for_quantity(q, depth))
        return views

    def _anonymous_view(self, dim: Dim, depth: int) -> EntityView:
        view = EntityView(
            id="",
            zh="（未登记的物理量）",
            symbol="",
            category="",
            structure="scalar",
            dim=dim,
            dim_zh=dim.describe_zh(),
            unit_forms=[],
            unit_form_details=[],
            note="该量纲组合暂未在物理量表里登记，以下分支仅由公式库给出。",
        )
        view.equivalent = self._equivalence_branches("", dim, depth, set())
        view.generated = self._generated_branches(dim, depth, set())
        return view

    def _view_for_quantity(self, q: Quantity, depth: int) -> EntityView:
        same = self.reg.quantities_with_dim(q.dim)
        view = EntityView(
            id=q.id,
            zh=q.zh,
            symbol=q.symbol,
            category=q.category,
            structure=q.structure,
            dim=q.dim,
            dim_zh=q.dim.describe_zh(),
            unit_forms=list(q.unit_forms),
            unit_form_details=self._unit_form_details(q),
            note=q.note,
            defined_as=q.defined_as,
            definition_note=q.definition_note,
            domain=q.domain,
            domain_zh=q.domain_zh,
            homogeneous=[
                {
                    "id": o.id,
                    "zh": o.zh,
                    "symbol": o.symbol,
                    "category": o.category,
                    "domain_zh": o.domain_zh,
                }
                for o in same
                if o.id != q.id
            ],
        )

        visited: set[str] = {q.id}
        view.equivalent = self._equivalence_branches(q.id, q.dim, depth, visited)
        view.generated = self._generated_branches(q.dim, depth, visited)
        view.formulas = self._formulas_for(q)
        view.definitions = self._definitions_for(q)
        self._attach_submissions(view)
        return view

    # -------- 常见写法 --------

    def _unit_form_details(self, q: Quantity) -> list[dict[str, str]]:
        """把 unit_forms 展开成「写法 + 中文读音 + 补充说明」。

        中文读音**由引擎生成**而不是写在数据里：`kg·m/s^2` 读作
        「千克·米每次方秒」这类读法完全可以由 AST 拼出来（求值器的
        Renderer 就是干这个的）。生成的读法对每一条都存在，于是
        「有些写法有描述、有些没有」这个问题从根上消失了——
        剩下的只是可选的补充说明。

        decompositions 里带 note 的写法按 expr 合并到同一行：
        同一个写法的读法与说明本来就是一回事，分两处显示反而是噪声。
        """
        notes: dict[str, str] = {}
        for d in q.decompositions:
            if d.note:
                notes.setdefault(_normalize_expr(d.expr), d.note)

        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for expr in q.unit_forms:
            key = _normalize_expr(expr)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "expr": expr,
                "zh": self._zh_of(expr),
                "note": notes.get(key, ""),
            })
        return out

    def _zh_of(self, expr: str) -> str:
        """表达式的中文读法，解析不出来时返回空串（界面留空占位）。"""
        try:
            _n, res = self.reg.eval_expr(expr)
        except (P.ParseError, EvalError, DimError):
            return ""
        return res.zh

    # -------- 定义式 --------

    def _definitions_for(self, q: Quantity) -> list[dict[str, object]]:
        """找出定义该物理量的公式。

        判据是公式数据里的 `defines` 字段（指向被定义的物理量 id），
        而不是量纲推断。为什么不用推断：曾试过「新量是否引入新基本量纲」
        做启发式，结果把 F = m·a（牛顿第二定律）和 F = G·m₁m₂/r²
        （万有引力定律）都判成了定义式——定律与定义式的区别是物理知识，
        不是量纲能算出来的，所以必须显式标注。
        """
        out: list[dict[str, object]] = []
        for f in self.reg.formulas.values():
            if getattr(f, "defines", "") != q.id:
                continue
            payload = self._formula_payload(f)
            payload["definition_note"] = self._definition_note(f, q)
            out.append(payload)
        return out

    def _definition_note(self, f: Formula, q: Quantity) -> str:
        """给定义式配一句「它在说什么」的说明。"""
        pattern_zh = ""
        pat = self.reg.patterns.get(f.pattern)
        if pat:
            pattern_zh = pat.zh
        base = f"{f.zh}。这是「{q.zh}」的定义式（{pattern_zh}）"
        if q.structure in ("vector", "matrix", "tensor"):
            return base + f"，它是一个{q.structure}量。"
        return base + "。"

    # -------- 用户补录 --------

    def _attach_submissions(self, view: EntityView) -> None:
        """把用户提交挂到视图上。

        按**种类**分流（页面规范 §5）：
          formula     已核实 → submitted（参与「依据现实式子」的展示）
                      ——它补的是「这个量纲组合出现在哪个式子里」
          definition  已核实 → definitions（补定义式）
          unit_form   已核实 → unit_forms（补惯用写法）
          note        已核实 → note（补一句话描述）
          disputed    任何种类 → disputed，只展示、不进槽位

        未通过核实的一律只展示原文：核实结论与用户贡献是两件事，
        不能因为核实不通过就把用户写的东西抹掉（既有约定）。

        已核实的 formula 类若与等价分支里已有的表达式重复则跳过——
        用户提交的价值在于「工具原本没有的」。
        """
        if self.submissions is None:
            return
        existing = {_normalize_expr(b.expr) for b in view.equivalent}
        existing |= {_normalize_expr(b.expr) for b in view.generated}

        dim_si = view.dim.format_si()
        for s in self.submissions.all():
            kind = getattr(s, "kind", "formula") or "formula"
            if kind == "formula":
                # formula 类按量纲匹配
                if s.target_dim != dim_si:
                    continue
            else:
                # 其余种类按物理量匹配
                if not view.id or s.target_qid != view.id:
                    continue

            if s.status != STATUS_VERIFIED:
                view.disputed.append(self._submission_branch(view, s, "disputed"))
                continue

            if kind == "definition":
                view.definitions.append({
                    "id": s.id,
                    "tex": s.expr,
                    "zh": s.zh or "用户补录的定义式",
                    "category": view.category,
                    "pattern": "",
                    "pattern_zh": "",
                    "pattern_explain": "",
                    "defines": view.id,
                    "symbols": [],
                    "definition_note": s.note or s.source or "用户补录，已核实。",
                    "from_submission": s.id,
                })
                continue
            if kind == "unit_form":
                if s.expr not in view.unit_forms:
                    view.unit_forms.append(s.expr)
                view.submitted.append(self._submission_branch(view, s, "submitted"))
                continue
            if kind == "note":
                if not view.note:
                    view.note = s.note or s.zh
                view.submitted.append(self._submission_branch(view, s, "submitted"))
                continue

            # kind == "formula"
            key = _normalize_expr(s.expr)
            if key in existing:
                continue
            view.submitted.append(self._submission_branch(view, s, "submitted"))

    def _submission_branch(
        self, view: EntityView, s: Any, kind: str
    ) -> Branch:
        """把一条补录渲染成分支（两种状态共用）。"""
        meta = EDGE_KINDS[kind]
        return Branch(
            kind=kind,
            label=meta["label"],
            short_label=meta["zh"],
            explain=meta["explain"],
            expr=s.expr or "（说明类补录，无表达式）",
            zh=s.zh or (s.note[:40] if s.note else ""),
            dim=view.dim,
            source=Source(
                note=(s.note or s.source or "").strip(),
                formula_zh=s.source,
                submitted_id=s.id,
                submitted_status=s.status,
                submitted_source=s.source,
                submitted_kind=getattr(s, "kind", "formula") or "formula",
            ),
            points_to=[],
        )

    def with_search(self, view: EntityView) -> EntityView:
        """给视图补上算法枚举分支。

        设计成独立步骤而非默认行为，理由有两条：
          1. 有成本（实测 200–300 ms），不该让每次查询都付
          2. 界面上默认不显示——它是「限定之外的补充」，
             而不是主体内容。用户主动点「展开更多」才该看到。
        """
        if view.search_ran:
            return view
        shown: list[str] = []
        for b in view.equivalent:
            shown.append(b.expr)
        for b in view.generated:
            shown.append(b.expr)
        view.searched = self.search_branches(view.dim, shown)
        view.search_ran = True
        return view

    # -------- 算法枚举分支：来自 search.py 的有界搜索 --------

    def search_branches(
        self, dim: Dim, already_shown: Iterable[str] = ()
    ) -> list[Branch]:
        """跑有界搜索，产出标注为「算法枚举」的分支。

        与公式来源的分支严格分开（设计文档 2.7）：
          - kind = "search"，界面渲染成灰色虚线 + 「算法枚举」标记
          - source.is_from_formula = False，source.note 明确写出
            「未对应具体物理式子」

        这样做是因为教学诚实性：搜索结果是**数学上量纲正确**的组合，
        但没有现实物理式子使用它。不标注会让学生误以为它们有物理来源。

        already_shown 传入已经展示过的表达式（等价分支 + 公式分支），
        避免同一条写法在界面上出现两次。
        """
        skip = {_normalize_expr(e) for e in already_shown}
        out: list[Branch] = []
        seen: set[str] = set()
        try:
            decomps = search_decompositions(self.reg, dim)
        except Exception:  # noqa: BLE001
            # 搜索失败不应影响主功能：公式来源的分支已经足够说明问题
            return []

        for d in decomps:
            key = _normalize_expr(d.expr)
            if key in seen or key in skip:
                continue
            seen.add(key)
            out.append(
                Branch(
                    kind="search",
                    label=EDGE_KINDS["search"]["label"],
                    short_label=EDGE_KINDS["search"]["zh"],
                    explain=EDGE_KINDS["search"]["explain"],
                    expr=d.expr,
                    zh=d.zh,
                    dim=d.dim,
                    source=Source(note=d.note),
                    points_to=[],
                )
            )
        return out

    # -------- 等价分支：来自条目自身的 decompositions --------

    def _equivalence_branches(
        self, qid: str, dim: Dim, depth: int, visited: set[str]
    ) -> list[Branch]:
        if depth <= 0:
            return []
        out: list[Branch] = []
        seen: set[str] = set()

        q = self.reg.quantities.get(qid) if qid else None
        exprs: list[tuple[str, str]] = []
        if q is not None:
            exprs = [(d.expr, d.note) for d in q.decompositions]
        # 单位注册表里的同量纲单位也作为等价写法（如 N 与 kg·m/s² 之外的单位）
        for uid, u in sorted(self.reg.units.items()):
            if u.dim == dim and uid not in (qid,):
                exprs.append((uid, ""))

        for expr, note in exprs:
            key = _normalize_expr(expr)
            if key in seen:
                continue
            seen.add(key)
            branch = self._make_branch(
                kind="equivalence",
                expr=expr,
                dim=dim,
                source=Source(note=note),
                depth=depth,
                visited=visited,
            )
            if branch is not None:
                out.append(branch)
        return out

    # -------- 泛函分支：来自公式库的 relations --------

    #: 求导与积分互为逆运算，所以每条泛函关系都能**双向**读。
    #: 键是数据里声明的 kind，值是把方向掉过来之后的 kind。
    _INVERSE_KIND = {"derivative": "integral", "integral": "derivative"}

    def _generated_branches(
        self, dim: Dim, depth: int, visited: set[str]
    ) -> list[Branch]:
        """收集产出该量纲的泛函关系（求导 / 积分 / 矩阵）。

        候选集直接来自 registry.relations_for_dim(dim)——它按 Relation.to
        匹配（泛函关系里 from 是源量、to 是结果，见该方法说明）。

        这个函数改到第五版才收敛，把踩过的坑记下来避免重犯：

        坑一：不要手工遍历 formulas × relations 再判断量纲是否相等。
              量纲相等是弱条件，会把无关分支收进来——P = dW/dt 的
              from=J 是能量，J/s 恰好是功率，于是查「力」时也被误命中。
              用 relations_for_dim 才是语义正确的候选集。

        坑二：关系虽然写在公式对象里，但 rel.wrt 与该公式**实际**的
              求导/积分变量可以不一致（P = dW/dt 公式里写 symbol='t'，
              其 relations 却写 wrt='s'）。必须按量纲校验两者一致，
              否则会拿着错误的自变量求导，算出串味的分支。

        坑三：别把「正向」和「反向」写成两个互斥条件。曾写成
              「方向一：求导结果==目标」+「方向二：源==目标」，
              两者不可能同时成立，结果全部漏项。
        """
        if depth <= 0:
            return []
        out: list[Branch] = []
        seen: set[str] = set()

        def key_of(kind: str, src_expr: str, wrt: str, order: int) -> str:
            """边的**语义键**：同一条边不管由哪条公式、哪个方向写出来，键相同。

            这一版把 formula.id 从键里去掉了，换来两件事：
              1. 两条公式描述同一条关系时（冲量定理写 integral、牛顿第二定律
                 写 derivative，其实是同一件事）不会在页面上出现两遍；
              2. 反读出来的对偶边与数据里显式声明的边自动合并——
                 显式的先处理，所以显示的是数据里那条，符合「以现实式子为准」。
            """
            return f"{kind}|{_normalize_expr(src_expr)}|{wrt}|{order}"

        def emit(
            formula: Formula,
            rel: Relation,
            kind: str,
            src_expr: str,
            src_dim: Dim,
            inverse: bool,
            inverse_of: str = "",
        ) -> None:
            k = key_of(kind, src_expr, rel.wrt, rel.order)
            if k in seen:
                return
            derived, op_text = self._apply_op(src_dim, kind, rel.wrt, rel.order)
            if derived is None or derived != dim:
                return
            seen.add(k)
            note = rel.note
            if inverse:
                note = (
                    f"由 {formula.tex} 反读得到（微积分基本定理："
                    f"若 B = dA/dx，则 A = ∫B dx，量纲关系完全一样）"
                )
            branch = self._make_branch(
                kind=kind,
                expr=src_expr,
                dim=dim,
                source=Source(
                    formula_id=formula.id,
                    tex=formula.tex,
                    formula_zh=formula.zh,
                    note=note,
                    pattern=formula.pattern,
                    pattern_zh=self.reg.patterns[formula.pattern].zh
                    if formula.pattern in self.reg.patterns
                    else "",
                    inverse=inverse,
                    inverse_of=inverse_of,
                ),
                depth=depth,
                visited=visited,
                extra_label=op_text,
            )
            if branch is not None:
                out.append(branch)

        for formula, rel in self.reg.relations_for_dim(dim):
            if rel.kind == "equivalence":
                continue  # 等价关系由 decompositions 负责，避免重复
            if rel.kind == "matrix":
                self._add_matrix_branch(formula, dim, out, seen)
                continue

            # 校验这条关系确实属于这条公式（变量量纲一致），
            # 否则会把别处的过程算到本公式头上。
            if not self._wrt_matches_formula_variable(formula, rel):
                continue

            src_dim = self._eval_dim(rel.from_)
            if src_dim is None:
                continue
            emit(formula, rel, rel.kind, rel.from_, src_dim, inverse=False)

        # 第二遍：把每条泛函关系**掉头**读一遍。
        #
        # 用户报告「没有由积分生成的量纲，按理该与求导成对出现」——这不是
        # 数据缺失，是建模漏了一维：数据里 derivative 关系有 9 条而 integral
        # 只有 3 条，看起来严重不对称；但求导与积分互为逆运算，
        # 每条 derivative 都同时断言了一条 integral（反之亦然）。
        #
        # 掉头后的边是「源 = rel.to、结果 = rel.from_」，所以源量纲取
        # dim(rel.to)、运算取 _INVERSE_KIND[kind]。**是否属于当前页面
        # 一律交给 emit 里的 derived == dim 判定**，这里不加前置比较——
        # 曾经写成 `src_dim != dim` 就 continue，那是把「源的量纲」当成
        # 「结果的量纲」比了，结果一条对偶边都出不来（而且看起来完全合理）。
        for formula in self.reg.formulas.values():
            for rel in formula.relations:
                inv_kind = self._INVERSE_KIND.get(rel.kind)
                if inv_kind is None:
                    continue
                if not self._wrt_matches_formula_variable(formula, rel):
                    continue
                src_dim = self._eval_dim(rel.to)
                if src_dim is None:
                    continue
                emit(formula, rel, inv_kind, rel.to, src_dim,
                     inverse=True, inverse_of=rel.from_)

        return out

    def _wrt_matches_formula_variable(
        self, formula: Formula, rel: Relation
    ) -> bool:
        """rel.wrt 是否与该公式实际求导/积分的变量一致（按量纲比较）。

        这条收窄来自一个真实且隐蔽的 bug：关系与公式写在同一个对象里，
        但两者的变量写法可以不同——P = dW/dt 的公式写 symbol='t'，
        而它的 relations 里却写了 wrt='s'。仅凭「公式有 differentiator」
        放行，会让这条关系拿着 wrt='s' 去对能量求时间导数，
        算出的量纲又与目标不符，于是要么漏项要么串到别的分支上。

        判据：把 rel.wrt 解析成量纲，与**公式里那个 differentiator /
        integrator 条目的量纲**比较。按量纲而非按符号名比较，
        这样 x 与 m、t 与 s 之类的同义写法都能正确匹配。
        """
        want_role = "integrator" if rel.kind == "integral" else "differentiator"
        if rel.kind not in ("derivative", "integral", "gradient",
                            "divergence", "curl"):
            return True

        wrt_dim = self.reg.var_dim(rel.wrt) if rel.wrt else None
        if wrt_dim is None:
            return False

        for entry in formula.rhs_entries:
            if str(entry.get("role", "")) != want_role:
                continue
            raw = entry.get("dim") or {}
            if not isinstance(raw, Mapping):
                continue
            try:
                if Dim.from_mapping(raw) == wrt_dim:  # type: ignore[arg-type]
                    return True
            except DimError:
                continue
        return False

    def _eval_dim(self, expr: str) -> Dim | None:
        """求表达式的量纲，失败返回 None。"""
        try:
            _n, res = self.reg.eval_expr(expr)
        except (P.ParseError, EvalError, DimError):
            return None
        return res.dim

    def _rhs_contains(self, formula: Formula, dim: Dim) -> bool:
        """公式的 rhs 里是否有量纲等于 dim 的项。"""
        for entry in formula.rhs_entries:
            raw = entry.get("dim") or {}
            if not isinstance(raw, Mapping):
                continue
            try:
                if Dim.from_mapping(raw) == dim:  # type: ignore[arg-type]
                    return True
            except DimError:
                continue
        return False

    def _add_matrix_branch(
        self,
        formula: Formula,
        dim: Dim,
        out: list[Branch],
        seen: set[str],
    ) -> None:
        key = f"matrix|{formula.id}"
        if key in seen:
            return
        seen.add(key)
        # 矩阵关系的源用 lhs 表达式（如转动惯量 I = ∫∫∫ r²ρ dV）
        branch = self._make_branch(
            kind="matrix",
            expr=formula.tex.split("=")[0].strip() or formula.id,
            dim=dim,
            source=Source(
                formula_id=formula.id,
                tex=formula.tex,
                formula_zh=formula.zh,
                note="矩阵运算：元素量纲与标量同规则，但结构不同",
                pattern=formula.pattern,
                pattern_zh=self.reg.patterns[formula.pattern].zh
                if formula.pattern in self.reg.patterns
                else "",
            ),
            depth=0,
            visited=set(),
        )
        if branch is not None:
            out.append(branch)

    def _apply_op(
        self, src: Dim, kind: str, wrt: str, order: int
    ) -> tuple[Dim | None, str]:
        """从源量纲正向应用运算，返回 (结果量纲, 运算说明)。

        说明文本按 kind 生成，所以反读出来的对偶边也自带正确的「对 x 积分」
        字样——它由 kind 决定，不由原关系的方向决定。
        """
        if kind == "derivative":
            var = self._var_dim(wrt)
            if var is None:
                return None, ""
            return src - var.scaled(order), f"对 {wrt} 求导"
        if kind == "integral":
            var = self._var_dim(wrt)
            if var is None:
                return None, ""
            return src + var.scaled(order), f"对 {wrt} 积分"
        return src, ""

    def _var_dim(self, symbol: str) -> Dim | None:
        """求导/积分变量的量纲。

        wrt 字段里写的是物理惯例的变量名（x、t、V），不是单位符号，
        所以必须经 registry.var_dim 查变量名映射表，不能直接查单位表。
        """
        return self.reg.var_dim(symbol)

    # -------- 构造单个分支 --------

    def _make_branch(
        self,
        kind: str,
        expr: str,
        dim: Dim,
        source: Source,
        depth: int,
        visited: set[str],
        extra_label: str = "",
    ) -> Branch | None:
        try:
            node = P.parse(expr)
            res = evaluate(node, self.resolver)
        except (P.ParseError, EvalError):
            return None

        # 等价分支用表达式的量纲；泛函分支用运算后的量纲
        branch_dim = res.dim if kind == "equivalence" else dim
        meta = EDGE_KINDS.get(kind, EDGE_KINDS["equivalence"])

        # 该分支指向哪些已知物理量（同量纲条目）——界面用「这也正是」提示。
        # 携带 id 而非纯文本，界面才能把每一项做成可跳转的链接。
        points = [
            {
                "id": o.id,
                "zh": o.zh,
                "symbol": o.symbol,
                "domain_zh": o.domain_zh,
                "category": o.category,
            }
            for o in self.reg.quantities_with_dim(branch_dim)
            if o.id not in visited
        ]

        branch = Branch(
            kind=kind,
            label=meta["label"],
            short_label=meta["zh"],
            explain=meta["explain"],
            expr=expr,
            zh=res.zh,
            dim=branch_dim,
            source=source,
            points_to=points,
        )

        # 不在等价分支下再递归展开：所有等价写法都是同一个量的写法，
        # 递归只会把兄弟分支在每一个分支下重复列一遍（真实的噪声 bug）。
        # 需要"某一写法还有哪些写法"时，用户直接查那个写法即可。
        # 这里保留 children 字段供后续 search 阶段分批复用。
        return branch

    # -------- 相关公式 --------

    def _formulas_for(self, q: Quantity) -> list[dict[str, object]]:
        """列出与该物理量相关的公式（lhs 或 rhs 命中该量纲），附符号表。

        符号表挂在这里而不是另开端点：公式与它的符号是同一份内容的
        两个层次，分开取会让前端要发两轮请求才能渲染一个折叠块。
        """
        out: list[dict[str, object]] = []
        seen: set[str] = set()
        for f in self.reg.formulas_touching_dim(q.dim):
            if f.id in seen:
                continue
            seen.add(f.id)
            out.append(self._formula_payload(f))
        return out

    def _formula_payload(self, f: Formula) -> dict[str, object]:
        """公式的展示负载，含每个符号的名称与物理意义。"""
        pattern = self.reg.patterns.get(f.pattern)
        return {
            "id": f.id,
            "tex": f.tex,
            "zh": f.zh,
            "category": f.category,
            "pattern": f.pattern,
            "pattern_zh": pattern.zh if pattern else "",
            "pattern_explain": pattern.explain if pattern else "",
            "defines": f.defines,
            "symbols": [
                {
                    "symbol": e.symbol,
                    "zh": e.zh,
                    "meaning": e.meaning,
                    "unit_sym": e.unit_sym,
                    "unit_zh": e.unit_zh,
                    "dim_si": e.dim.format_si(),
                    "dim_zh": e.dim.describe_zh(),
                    "jump": e.jump,
                    "jump_zh": e.jump_zh,
                }
                for e in self.reg.formula_glossary(f)
            ],
        }


def _normalize_expr(expr: str) -> str:
    """把表达式规范化成可比较的键，用于去重。

    用 parser.canonical_expr 而不是 parse→render：后者只归一**语法**，
    不归一交换律，`N·m` 与 `m·N` 会得到两个键，于是同一个组合在
    「常见写法」和「算法枚举」里各显示一遍（用户报告的第 1 条问题）。
    """
    return P.canonical_expr(expr)
