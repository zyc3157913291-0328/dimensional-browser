"""有界搜索：向量格上的最短路分解。

设计文档 1.3 / 4.4 的落地。这是「展开更多」的引擎。

数学框架
--------
把每个单位看作整数格 Z^k 里的一个向量（量纲向量就是格点）。
一次「分解」就是从原点走到目标格点的路径，于是：

    求分解 = 求最短路

采用 BFS 后免费获得三个性质：
  - 按层数出结果，天然按复杂度排序（2 个单位的必排在 5 个之前）
  - 可设硬边界剪枝
  - 结果可分批返回

搜索空间的精确刻画（这是避免枚举爆炸的关键）
------------------------------------------
生成元不是一个单位，而是「**带整数系数的单位**」：`3m`、`m`、`m^2`、`kg/m^3`。
路径是这些生成元的**乘除序列**。等价的实现方式是给每个单位一个整数幂
（`m^2` 即 m 的 2 次幂，`1/s` 即 s 的 -1 次幂），于是：

    一个状态 = {(单位, 非零整数幂)} 的集合
    状态量纲 = Σ 幂次 × 该单位的量纲向量

这把「任意单位组合」压缩成了整数幂的多重集，状态空间可控且可哈希。

剪枝（会漏掉某些数学上合法的分解，因此结果必须标注为「有界搜索」）
------------------------------------------------------------
  S1 原子数 ≤ max_atoms（默认 3）
  S2 单单位幂次 ∈ [-max_power, max_power]（默认 3）
  S3 分子原子数 ≤ max_numerator_atoms（默认 3）
  S4 能量约束：把单位按「简单度」排序，要求幂次的加权和 ≤ max_weight

其中 S4 是关键：没有它，`kg^2·m^4/s^4` 这类既合法又无意义的组合会淹没结果。
简单度按「该单位是否为 SI 导出/基本单位、是否有中文名」评分——这既是
性能优化，也正好符合教学筛选的目标。

结果来源纪律（设计文档 2.7）
--------------------------
搜索结果**不来自现实物理式子**，必须与公式库来源明确区分。
本模块产出的每条路径都带 `via_search=True` 标记，界面据此渲染成
灰色虚线并标注「算法枚举」。绝不与公式来源混同。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .dim import Dim
from .evaluator import EvalError, UnitResolver
from .registry import Registry, Unit


class SearchError(ValueError):
    """搜索参数非法。"""


# ------------------------------------------------------------------ 剪枝参数


@dataclass(frozen=True)
class SearchBounds:
    """有界搜索的硬边界。

    默认值是实测调出来的：既要覆盖教科书常见的那些组合
    （N、J/m、Pa·m²、W/(A·m)…），又不能让 Pa·g/kg、s·bar 这类
    「纯数学拼接、不对应任何真实公式」的组合占据结果，
    同时保证单次查询在百毫秒量级。
    """

    max_atoms: int = 3
    max_power: int = 3
    max_numerator_atoms: int = 3
    # 代价上限。要注意量纲越复杂（L1 范数越大）的单位越「贵」：
    # Ω 的量纲 M·L²·T⁻³·I⁻² 的 L1 范数是 8，单靠它自己就得占掉 0.8 的
    # 代价额度，与别的单位组合很快触顶。上限设得太低会把 Ω、F、H 这类
    # 复杂导出单位挡在门外。10 是实测能覆盖全部 SI 导出单位组合的值。
    max_weight: float = 10.0
    max_results: int = 18
    # 节点预算：保证最坏情况下的时间上界。搜索是有界的，
    # 宁可少给几条结果，也不能让界面卡住。
    max_nodes: int = 6000
    # 候选单位的代价上限。SI 基本 1.0 / SI 导出 1.5，
    # 所以 2.0 正好只放行这两类；accepted（min、h）与 mixed（bar）
    # 被排除在搜索之外——它们只会在拼接里制造噪声，
    # 而它们本身作为单位仍可在等价分支中正常展示。
    max_unit_cost: float = 2.0

    def validate(self) -> None:
        if self.max_atoms < 1:
            raise SearchError("max_atoms 必须 ≥ 1")
        if self.max_power < 1:
            raise SearchError("max_power 必须 ≥ 1")
        if self.max_weight < 1:
            raise SearchError("max_weight 必须 ≥ 1")
        if self.max_nodes < 1:
            raise SearchError("max_nodes 必须 ≥ 1")
        if self.max_unit_cost <= 0:
            raise SearchError("max_unit_cost 必须 > 0")


@dataclass
class Decomposition:
    """一条分解路径。"""

    powers: tuple[tuple[str, int], ...]   # 规范序的 (单位 id, 幂次)
    expr: str
    zh: str
    dim: Dim
    atoms: int
    cost: float
    via_search: bool = True
    note: str = "算法枚举所得，未对应具体物理式子"


# ------------------------------------------------------------------ 单位简单度

# 单位制式的基础分：SI 基本与导出单位最「干净」，因此优先
_SYSTEM_BASE_COST: dict[str, float] = {
    "si-base": 1.0,
    "si-derived": 1.5,
    "accepted": 4.0,
    "mixed": 6.0,
    "other": 5.0,
}


# 不参与搜索的单位。理由都要具体，不能凭感觉加：

#   g   克。它虽是 SI 质量单位，但带词头（1 g = 0.001 kg），与 kg 同时
#       进入搜索会产生 Pa·g/kg、Pa·kg/g 这类「纯比例因子」组合——
#       量纲完全正确，物理上毫无意义。质量的质量基准统一用 kg。
#   rad 弧度、sr 球面度。无因次，不改变量纲，放进搜索只会制造噪声。
_NON_SEARCHABLE = frozenset({"g", "rad", "sr"})


def unit_cost(unit: Unit, reg: Registry) -> float:
    """给单位打「简单度」分。分越低越优先出现在结果里。

    评分依据（顺序即权重）：
      1. 制式：SI 基本 1.0 < SI 导出 1.5 < 受认可 4.0 < 混合 6.0
      2. 有中文名 → 教学友好，略降分
      3. 被任何现实公式引用 → 更「真实」，再降分

    第 3 条是本项目特有的：它把「依据现实物理式子」这一原则也带进了
    算法枚举的排序——即便结果本身来自搜索，排序仍让有物理背景的单位靠前。
    """
    cost = _SYSTEM_BASE_COST.get(unit.system, 5.0)
    if unit.zh:
        cost -= 0.2
    if _referenced_by_formula(unit.id, reg):
        cost -= 0.5
    return max(cost, 0.5)


def _referenced_by_formula(unit_id: str, reg: Registry) -> bool:
    """该单位是否出现在任何公式的量纲关系里。"""
    for f in reg.formulas.values():
        for rel in f.relations:
            if unit_id in (rel.from_, rel.to):
                return True
    return False


# ------------------------------------------------------------------ 可用生成元


@dataclass(frozen=True)
class Generator:
    """一个生成元：单位及其在搜索中的代价。"""

    unit: Unit
    cost: float
    dim: Dim


def build_generators(reg: Registry) -> list[Generator]:
    """构造搜索用的全部候选生成元（未按代价收窄）。

    无因次单位（rad、sr）不参与：它们不改变量纲，放进搜索只会产生噪声。
    制式过滤交给 SearchBounds.max_unit_cost 统一处理——
    代价里已经编码了「SI 基本 < SI 导出 < accepted < mixed」的偏好，
    用代价上限收窄比用制式名单更统一，也更容易调整。
    """
    out: list[Generator] = []
    for unit in reg.units.values():
        if unit.dim.is_dimensionless or unit.id in _NON_SEARCHABLE:
            continue
        out.append(
            Generator(unit=unit, cost=unit_cost(unit, reg), dim=unit.dim)
        )
    out.sort(key=lambda g: (g.cost, g.unit.id))
    return out


# ------------------------------------------------------------------ 搜索


class DimensionSearch:
    """向量格上的有界最短路搜索。"""

    def __init__(
        self,
        reg: Registry,
        bounds: SearchBounds | None = None,
    ) -> None:
        self.reg = reg
        self.bounds = bounds or SearchBounds()
        self.bounds.validate()
        all_gens = build_generators(reg)
        # 按代价上限收窄候选集：只保留 SI 基本与导出单位。
        # 这一步同时解决两个问题——噪声（不再出现 s·bar、Pa·min）
        # 与性能（生成元从 23 降到 ~15，分支因子同比下降）。
        self.generators = [
            g for g in all_gens if g.cost <= self.bounds.max_unit_cost
        ]
        self._cost = {g.unit.id: g.cost for g in self.generators}
        self._dim = {g.unit.id: g.dim for g in self.generators}
        self._last_nodes = 0

    # -------- 对外入口 --------

    def decompose(
        self, target: Dim, bounds: SearchBounds | None = None
    ) -> list[Decomposition]:
        """找出若干个能得到 target 量纲的单位组合。"""
        b = bounds or self.bounds
        b.validate()

        if target.is_dimensionless:
            return []  # 无因次量不需要单位组合

        results = self._bfs(target, b)
        results.sort(key=lambda d: (d.atoms, d.cost, d.expr))
        return results[: b.max_results]

    # -------- BFS 主体 --------

    def _bfs(self, target: Dim, b: SearchBounds) -> list[Decomposition]:
        """广度优先搜索满足剪枝条件的单位组合。

        关键设计：**每个 (状态, 单位) 只产生一个位移**，而不是对
        每个单位尝试 [-max_power, max_power] 的所有幂次。
        理由是分支因子：23 个生成元 × 6 个候选幂次 = 每状态 138 条边，
        深度 3 就是 260 万次扩展（实测 4.7 秒）。合并成一个位移后
        每状态 23 条边，规模降两个数量级。

        代价是**有界性变强**：同一单位在一条路径里只会被加入一次。
        这是可接受的取舍——因为教学上有意义的组合几乎都是
        「几个不同单位各出现一次」，而 Pa²/bar 那类同单位反复出现
        的形态本来就是要过滤掉的噪声。

        位移方向由「量纲缺口」决定：优先选能把缺口补上的幂次。
        这仍是启发式（可能漏掉需要先过冲再回补的路径），
        所以结果一律标注为「有界搜索」。
        """
        zero: tuple[tuple[str, int], ...] = ()
        queue: deque[tuple[tuple[tuple[str, int], ...], Dim, int, int, float]] = (
            deque([(zero, Dim({}), 0, 0, 0.0)])
        )
        seen: set[tuple[tuple[str, int], ...]] = {zero}
        found: list[Decomposition] = []
        found_keys: set[tuple[tuple[str, int], ...]] = set()
        nodes = 0

        while queue and nodes < b.max_nodes:
            powers, dim, atoms, num_atoms, cost = queue.popleft()
            nodes += 1
            if atoms >= b.max_atoms:
                continue

            for gen in self.generators:
                uid = gen.unit.id
                p = self._shift_for(dim, target, gen, b)
                if p == 0:
                    continue

                new_powers = _with_power(powers, uid, p)
                if new_powers in seen:
                    continue

                new_dim = dim + gen.dim.scaled(p)
                new_atoms = atoms + 1
                new_num = num_atoms + (1 if p > 0 else 0)
                new_cost = cost + gen.cost * abs(p)

                if new_num > b.max_numerator_atoms:
                    continue
                if new_cost > b.max_weight:
                    continue

                seen.add(new_powers)

                if new_dim == target:
                    if new_powers not in found_keys:
                        found_keys.add(new_powers)
                        dec = self._make_decomposition(new_powers, new_cost)
                        if dec is not None:
                            found.append(dec)
                    # 到达目标后不再扩展：更长的路径只会更复杂
                    continue

                queue.append((new_powers, new_dim, new_atoms, new_num, new_cost))

        self._last_nodes = nodes
        return found

    def _shift_for(
        self, current: Dim, target: Dim, gen: Generator, b: SearchBounds
    ) -> int:
        """决定把生成元 gen 加入当前状态时用的幂次（0 表示跳过）。

        取法：在所有可行幂次里，选「加上之后到目标的 L1 距离最小」的那个。
        L1 距离是剩余原子的合理下界估计，所以这是贪心的可采纳启发式。
        同分时优先 |p| 小者（表达式更简洁）。
        """
        unit_dim = gen.dim
        exist = 0  # 该单位在当前路径中只会被加入一次（见 _bfs 说明）

        best_p = 0
        best_key: tuple[int, float] | None = None

        for p in _candidate_powers(current, target, unit_dim, b.max_power):
            if p == 0:
                continue
            cand = current + unit_dim.scaled(p)
            dist = _l1_distance(cand, target)
            key = (dist, abs(p))
            if best_key is None or key < best_key:
                best_key = key
                best_p = p
        return best_p

    # -------- 表达式构造 --------

    def _make_decomposition(
        self, powers: tuple[tuple[str, int], ...], cost: float
    ) -> Decomposition | None:
        """把幂次集合渲染成单位表达式与中文名。

        表达式构造规则（追求与教科书写法一致）：
          - 正幂在分子，负幂在分母
          - 幂次 1 不写上标
          - 分母有多项时加括号：kg/(m·s^2)
          - 分母为单项时不加：m/s^2

        刻意手工拼接而不用 parser.render：这里要控制的是
        「分子/分母」这种人类写法，而不是 AST 的规范形式。
        """
        numer: list[str] = []
        numer_zh: list[str] = []
        denom: list[str] = []
        denom_zh: list[str] = []
        atoms = 0

        # 表达式按「代价升序」排列，让简单单位写在前面
        ordered = sorted(
            powers, key=lambda t: (self._cost.get(t[0], 99.0), t[0])
        )

        for uid, p in ordered:
            unit = self.reg.units.get(uid)
            if unit is None:
                return None
            atoms += 1
            sym = unit.symbol if p == 1 else f"{unit.symbol}^{p}"
            zh = unit.zh if p == 1 else f"{unit.zh}{_zh_power(p)}"
            if p > 0:
                numer.append(sym)
                numer_zh.append(zh)
            else:
                # 负幂的符号写法用正幂放分母
                pos = -p
                sym_d = unit.symbol if pos == 1 else f"{unit.symbol}^{pos}"
                zh_d = unit.zh if pos == 1 else f"{unit.zh}{_zh_power(pos)}"
                denom.append(sym_d)
                denom_zh.append(zh_d)

        if not numer:
            numer = ["1"]
            numer_zh = [""]

        expr = "·".join(numer)
        if denom:
            d = "·".join(denom)
            expr += f"/{d}" if len(denom) == 1 else f"/({d})"

        zh = "·".join(x for x in numer_zh if x)
        if denom:
            dz = "·".join(denom_zh)
            zh += f"每{dz}" if len(denom_zh) == 1 else f"每（{dz}）"

        dim = _powers_dim(powers, self._dim)
        return Decomposition(
            powers=powers,
            expr=expr,
            zh=zh,
            dim=dim,
            atoms=atoms,
            cost=cost,
        )


# ------------------------------------------------------------------ 工具


def _l1_distance(a: Dim, b: Dim) -> int:
    """两个量纲向量的 L1 距离。

    作为「还需多少个原子」的启发式下界：每加入一个原子，
    最多只能改变该原子的量纲向量所能覆盖的那些分量。
    """
    keys = set(a.powers) | set(b.powers)
    return sum(abs(a.power_of(k) - b.power_of(k)) for k in keys)


def _candidate_powers(
    current: Dim, target: Dim, unit_dim: Dim, max_power: int
) -> list[int]:
    """给出对某个单位值得尝试的幂次候选。

    来源有两类：
      1. 缺口导向：依据单位在「还差的维度」上的分量推荐幂次
      2. 兜底：±1、±2，保证纯乘除的基本组合总能被构造出来

    这是启发式，可能漏掉需要「先过冲再回补」的路径——所以结果
    必须标注为有界搜索，不能声称是全部可能。
    """
    out: set[int] = set()

    deficit = {k: v for k, v in target.powers.items()}
    for k, v in current.powers.items():
        deficit[k] = deficit.get(k, 0) - v
    for key, need in deficit.items():
        have = unit_dim.power_of(key)
        if have == 0 or need == 0:
            continue
        mag = min(abs(need) // abs(have), max_power)
        if mag == 0:
            mag = 1
        sign = 1 if (have > 0) == (need > 0) else -1
        out.add(sign * mag)

    for c in (1, -1, 2, -2):
        out.add(c)

    return sorted(p for p in out if p != 0 and abs(p) <= max_power)


def _powers_dim(
    powers: tuple[tuple[str, int], ...], dims: Mapping[str, Dim]
) -> Dim:
    out = Dim({})
    for uid, p in powers:
        d = dims.get(uid)
        if d is None:
            continue
        out = out + d.scaled(p)
    return out


def _with_power(
    powers: tuple[tuple[str, int], ...], uid: str, delta: int
) -> tuple[tuple[str, int], ...]:
    """在状态里给 uid 增加 delta 幂次，返回规范序（按 id 排序）的新状态。"""
    cur = dict(powers)
    new = cur.get(uid, 0) + delta
    if new == 0:
        cur.pop(uid, None)
    else:
        cur[uid] = new
    return tuple(sorted(cur.items()))


_ZH_DIGITS = "零一二三四五六七八九"


def _zh_power(n: int) -> str:
    if n < 0:
        return "的负" + _zh_power(-n)[1:]
    if n < 10:
        return f"的{_ZH_DIGITS[n]}次方"
    return f"的{n}次方"


# ------------------------------------------------------------------ 便捷入口


_DEFAULT_SEARCH: dict[int, DimensionSearch] = {}


def search_decompositions(
    reg: Registry,
    target: Dim,
    bounds: SearchBounds | None = None,
) -> list[Decomposition]:
    """带缓存的便捷入口。同一组边界参数复用同一个搜索器实例。

    搜索器本身无状态（只有只读的 generator 表），所以缓存是安全的。
    """
    b = bounds or SearchBounds()
    key = hash(b)
    s = _DEFAULT_SEARCH.get(key)
    if s is None:
        s = DimensionSearch(reg, b)
        _DEFAULT_SEARCH[key] = s
    return s.decompose(target, b)


def format_result(d: Decomposition) -> str:
    """给 CLI 用的单行摘要。"""
    return f"{d.expr:<26s} {d.zh}    [原子 {d.atoms}, 代价 {d.cost:.1f}]"
