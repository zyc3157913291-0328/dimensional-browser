"""求值器：AST → (系数, 量纲向量, 中文写法, 符号写法)。

严格分层：本模块不读文件、不依赖 registry。registry 反向 import 本模块来做自检。
这样「单位数据是否合法」由 evaluator 判定，而 evaluator 不知道数据从哪来。

写这段代码时发现两个设计文档没写透的细节，都在下面标了 ⚠：

⚠ 细节一：显式乘法必须被记录。 "kg·m/s^2" 的中文名应该显示为
   「千克·米每二次方秒」而不是「千克米每秒方」——只有 AST 保留了运算符，
   渲染器才知道该在哪里插入连接词。v1 设计里我以为显示名可以从量纲反推，
   实际不行：量纲丢掉了结构信息。

⚠ 细节二：分数的幂必须区分整数与小数。 指数是小数（m^0.5）时量纲幂次
   不是整数，Dim 的整数约束会直接报错。这逼出一个结论：**SI 量纲分析
   只对整数幂封闭**。分数幂表达式只能作为「无因次化」的中间形式存在，
   不能参与量纲加减。这是数学事实，不是实现缺陷，所以要给出清晰报错。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from .dim import Dim, DimError
from . import parser as P


class EvalError(ValueError):
    """求值错误：单位不认识、量纲不兼容、变量缺量纲等。

    与 ParseError（语法）严格区分：语法错误在 parse 阶段抛出，
    语义/量纲错误在这里抛出。
    """


class UnitLike(Protocol):
    """evaluator 需要的最小单位接口。

    registry.Unit 满足它，测试里可以用轻量替身，避免测 evaluator 时
    被迫构造完整注册表。
    """

    id: str
    zh: str
    symbol: str
    dim: Dim
    factor: float
    prefixable: bool


@dataclass(frozen=True)
class Operand:
    """一个已解析的量：可以是单位实例，也可以是变量（公式里的符号量）。"""

    symbol: str
    zh: str
    dim: Dim
    factor: float = 1.0
    structure: str = "scalar"
    is_variable: bool = False
    prefixable: bool = True


@dataclass
class EvalContext:
    """求值上下文：变量符号 → 量纲 的绑定。

    公式签名里的 m、a、F 等符号通过它注入。名字冲突时 context 优先于
    单位表——因为公式里的 m 是质量，不是米。
    """

    variables: Mapping[str, Operand] = field(default_factory=dict)
    prefer_variables: bool = True


@dataclass(frozen=True)
class Result:
    """求值结果。系数只做乘除换算，不参与量纲。"""

    value: float
    dim: Dim
    zh: str
    sym: str
    structure: str = "scalar"

    def render_zh(self) -> str:
        return self.zh

    def render_sym(self) -> str:
        return self.sym


# ------------------------------------------------------------------ 单位解析


@dataclass(frozen=True)
class PrefixHit:
    prefix_symbol: str
    prefix_zh: str
    prefix_factor: float
    unit_id: str


class UnitResolver:
    """把标识符解析成单位实例，支持词头组合。

    解析顺序（刻意的）：
      1. 精确匹配已注册单位 id / 符号 / 别名
      2. 上下文变量
      3. 词头 + 已注册单位

    第 1 步优先是为了避免歧义：min 是「分」，不能解析成 m+in；
    mol 是「摩尔」，不能解析成 m+ol。
    """

    def __init__(
        self,
        units_by_key: Mapping[str, UnitLike],
        prefixes: Sequence[object],
    ) -> None:
        """units_by_key 必须是「键 → 单位对象」，不是「键 → id」。

        这个界面刻意收窄：求值器需要读单位的 zh / dim / factor，
        如果只给 id 就会在运行期炸出 AttributeError。
        """
        self._by_key = units_by_key
        # 词头按长度降序，保证 "da" 先于 "d" 匹配。
        # 同时兼容 dict（测试用）与 dataclass（registry 用）两种形态。
        self._prefixes = sorted(
            (
                (
                    str(_field(p, "symbol")),
                    str(_field(p, "zh")),
                    float(_field(p, "factor")),
                )
                for p in prefixes
            ),
            key=lambda t: -len(t[0]),
        )

    def resolve(self, name: str, ctx: EvalContext | None = None) -> Operand:
        # 0) 上下文变量优先：公式里的 m 是质量，不是米
        if ctx is not None and name in ctx.variables:
            return ctx.variables[name]

        # 1) 精确匹配已注册单位
        unit = self._by_key.get(name)
        if unit is not None:
            return Operand(
                symbol=name,
                zh=unit.zh,
                dim=unit.dim,
                factor=unit.factor,
                prefixable=getattr(unit, "prefixable", False),
            )

        # 2) 词头组合
        hit = self._try_prefix(name)
        if hit is not None:
            unit = self._by_key[hit.unit_id]
            if not unit.prefixable:
                raise EvalError(
                    f"单位「{unit.zh}」({unit.id}) 不接受词头，"
                    f"因此 {name!r} 不合法"
                )
            return Operand(
                symbol=name,
                zh=f"{hit.prefix_zh}{unit.zh}",
                dim=unit.dim,
                factor=unit.factor * hit.prefix_factor,
            )

        raise EvalError(f"无法识别的单位或符号：{name!r}")

    def _try_prefix(self, name: str) -> PrefixHit | None:
        for psym, pzh, pfac in self._prefixes:
            if not name.startswith(psym) or len(name) == len(psym):
                continue
            rest = name[len(psym) :]
            unit = self._by_key.get(rest)
            if unit is not None and unit.prefixable:
                return PrefixHit(psym, pzh, pfac, unit.id)
        return None


# ------------------------------------------------------------------ 渲染


class Renderer:
    """由 AST 与量的绑定生成中英两种写法。

    ⚠ 细节一 的实现处：中文名不是从量纲反推的，而是沿着 AST 结构
    按运算符逐层拼出来的，因为量纲丢失了「这是除法还是乘法」的信息。
    """

    def __init__(self, operands: Mapping[int, Operand]) -> None:
        self._operands = operands

    def sym(self, node: P.Node) -> str:
        return self._sym(node, 0)

    def zh(self, node: P.Node) -> str:
        return self._zh(node, 0)

    # -------- 符号式 --------

    def _sym(self, node: P.Node, prec: int) -> str:
        if isinstance(node, P.Num):
            return node.render()
        if isinstance(node, P.Ident):
            return self._op(node).symbol
        if isinstance(node, P.Neg):
            inner = self._sym(node.operand, 3)
            text = f"-{inner}"
            return f"({text})" if prec > 3 else text
        if isinstance(node, P.Pow):
            base = self._sym(node.base, 4)
            exp = self._sym(node.exp, 4)
            return f"{base}^{exp}"
        if isinstance(node, P.Mul):
            parts = [self._sym(f, 3) for f in node.factors]
            text = "·".join(parts)
            return f"({text})" if prec > 3 else text
        if isinstance(node, P.Div):
            numer = self._sym(node.numer, 3)
            denom = self._sym(node.denom, 3)
            text = f"{numer}/{denom}"
            return f"({text})" if prec > 3 else text
        if isinstance(node, P.Add):
            chunks: list[str] = []
            for i, term in enumerate(node.terms):
                if isinstance(term, P.Neg):
                    inner = self._sym(term.operand, 2)
                    chunks.append(f"-{inner}" if i == 0 else f"- {inner}")
                else:
                    t = self._sym(term, 2)
                    chunks.append(t if i == 0 else f"+ {t}")
            text = " ".join(chunks)
            return f"({text})" if prec > 2 else text
        raise EvalError(f"渲染时遇到未知节点 {type(node).__name__}")

    # -------- 中文式 --------

    def _zh(self, node: P.Node, prec: int) -> str:
        if isinstance(node, P.Num):
            return node.render()
        if isinstance(node, P.Ident):
            return self._op(node).zh
        if isinstance(node, P.Neg):
            inner = self._zh(node.operand, 3)
            text = f"负{inner}"
            return f"（{text}）" if prec > 3 else text
        if isinstance(node, P.Pow):
            base = self._zh(node.base, 4)
            e = node.exp
            if isinstance(e, P.Num) and float(e.value).is_integer():
                n = int(e.value)
                return f"{base}{_zh_ordinal(n)}"
            exp = self._zh(e, 4)
            return f"{base}的{exp}次方"
        if isinstance(node, P.Mul):
            parts = [self._zh(f, 3) for f in node.factors]
            text = "·".join(parts)
            return f"（{text}）" if prec > 3 else text
        if isinstance(node, P.Div):
            numer = self._zh(node.numer, 3)
            denom = self._zh(node.denom, 3)
            return f"{numer}每{denom}"
        if isinstance(node, P.Add):
            chunks: list[str] = []
            for i, term in enumerate(node.terms):
                if isinstance(term, P.Neg):
                    inner = self._zh(term.operand, 2)
                    chunks.append(f"负{inner}" if i == 0 else f"减{inner}")
                else:
                    t = self._zh(term, 2)
                    chunks.append(t if i == 0 else f"加{t}")
            text = "".join(chunks)
            return f"（{text}）" if prec > 2 else text
        raise EvalError(f"渲染时遇到未知节点 {type(node).__name__}")

    def _op(self, node: P.Ident) -> Operand:
        op = self._operands.get(id(node))
        if op is None:
            raise EvalError(f"标识符 {node.name!r} 未绑定求值结果")
        return op


def _zh_ordinal(n: int) -> str:
    """整数幂的中文说法：2 → 「的二次方」，-2 → 「的负二次方」。"""
    zh_digits = "零一二三四五六七八九"
    def spell(v: int) -> str:
        if v < 0:
            return "负" + spell(-v)
        if v < 10:
            return zh_digits[v]
        if v < 100 and v % 10 == 0:
            return zh_digits[v // 10] + "十"
        if v < 100:
            return zh_digits[v // 10] + "十" + zh_digits[v % 10]
        return str(v)
    return f"的{spell(n)}次方"


# ------------------------------------------------------------------ 求值


def evaluate(
    node: P.Node,
    resolver: UnitResolver,
    ctx: EvalContext | None = None,
) -> Result:
    """对 AST 求值，返回系数、量纲与两种写法。"""
    operands: dict[int, Operand] = {}
    dim, value = _eval(node, resolver, ctx, operands)
    renderer = Renderer(operands)
    return Result(
        value=value,
        dim=dim,
        zh=renderer.zh(node),
        sym=renderer.sym(node),
        structure=_infer_structure(node, operands),
    )


def _eval(
    node: P.Node,
    resolver: UnitResolver,
    ctx: EvalContext | None,
    operands: dict[int, Operand],
) -> tuple[Dim, float]:
    if isinstance(node, P.Num):
        return Dim({}), float(node.value)

    if isinstance(node, P.Ident):
        op = resolver.resolve(node.name, ctx)
        operands[id(node)] = op
        return op.dim, op.factor

    if isinstance(node, P.Neg):
        d, v = _eval(node.operand, resolver, ctx, operands)
        return d, -v

    if isinstance(node, P.Pow):
        base_dim, base_val = _eval(node.base, resolver, ctx, operands)
        exp = _const_exponent(node.exp, resolver, ctx, operands)
        if not float(exp).is_integer():
            raise EvalError(
                f"指数 {exp!r} 不是整数：SI 量纲只对整数幂封闭。"
                f"形如 m^0.5 的分数幂不能参与量纲运算（数学事实，非实现限制）"
            )
        n = int(exp)
        try:
            new_dim = base_dim.scaled(n)
        except DimError as exc:
            raise EvalError(str(exc)) from exc
        return new_dim, base_val**n

    if isinstance(node, P.Mul):
        out = Dim({})
        val = 1.0
        for f in node.factors:
            d, v = _eval(f, resolver, ctx, operands)
            out = out + d
            val *= v
        return out, val

    if isinstance(node, P.Div):
        nd, nv = _eval(node.numer, resolver, ctx, operands)
        dd, dv = _eval(node.denom, resolver, ctx, operands)
        if dv == 0:
            raise EvalError("除数的换算系数为零")
        return nd - dd, nv / dv

    if isinstance(node, P.Add):
        # 量纲齐次性检查：只有量纲相同的项才能相加减。
        # 这是整个量纲分析的核心约束，也是「检查公式是否写错」的理论依据。
        first_dim, total = _eval(node.terms[0], resolver, ctx, operands)
        for term in node.terms[1:]:
            d, v = _eval(term, resolver, ctx, operands)
            if d != first_dim:
                raise EvalError(
                    f"量纲不齐次：{P.render(node.terms[0])} 是 "
                    f"{first_dim.format_si()}，但 {P.render(term)} 是 "
                    f"{d.format_si()}——量纲不同的量不能相加"
                )
            total += v
        return first_dim, total

    raise EvalError(f"求值时遇到未知节点 {type(node).__name__}")


def _const_exponent(
    node: P.Node,
    resolver: UnitResolver,
    ctx: EvalContext | None,
    operands: dict[int, Operand],
) -> float:
    """指数必须是纯数：可以带负号，可以是小数，但不能带量纲。"""
    d, v = _eval(node, resolver, ctx, operands)
    if not d.is_dimensionless:
        raise EvalError(
            f"指数携带量纲 {d.format_si()}：指数必须是无量纲纯数"
        )
    return v


_STRUCTURE_RANK = {"scalar": 0, "vector": 1, "matrix": 2, "tensor": 3}


def _infer_structure(node: P.Node, operands: Mapping[int, Operand]) -> str:
    """从表达式推断结构。

    规则（保守）：标量乘向量得向量；矩阵乘向量得向量；一切有疑问的
    情况退回标量，绝不猜测。这是设计文档 2.4「结构无量纲」的落地：
    结构只用于展示与代数规则分发，推错也不会污染量纲。
    """
    if isinstance(node, P.Ident):
        op = operands.get(id(node))
        return op.structure if op else "scalar"
    if isinstance(node, P.Num):
        return "scalar"
    if isinstance(node, P.Pow):
        base = _infer_structure(node.base, operands)
        n = getattr(node.exp, "value", 1)
        return base if isinstance(n, float) and n == 1 else "scalar"
    if isinstance(node, P.Neg):
        return _infer_structure(node.operand, operands)
    if isinstance(node, P.Mul):
        parts = [_infer_structure(f, operands) for f in node.factors]
        non_scalar = [p for p in parts if p != "scalar"]
        if not non_scalar:
            return "scalar"
        if len(non_scalar) == 1:
            return non_scalar[0]
        # 向量点积得标量，矩阵乘向量得向量——无上下文无法确定，保守退回标量
        return "scalar"
    if isinstance(node, P.Div):
        numer = _infer_structure(node.numer, operands)
        denom = _infer_structure(node.denom, operands)
        if denom == "scalar":
            return numer
        return "scalar"
    if isinstance(node, P.Add):
        # 加法各分量纲相同，结构取最高的那个
        ranks = [_infer_structure(t, operands) for t in node.terms]
        if not ranks:
            return "scalar"
        return max(ranks, key=lambda s: _STRUCTURE_RANK.get(s, 0))
    return "scalar"


def variables_from_signature(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, Operand]:
    """把公式签名里的条目列表转成变量绑定表。"""
    out: dict[str, Operand] = {}
    for e in entries:
        sym = str(e["symbol"])
        dim_map = e.get("dim") or {}
        if not isinstance(dim_map, Mapping):
            raise EvalError(f"变量 {sym} 的 dim 必须是对象")
        out[sym] = Operand(
            symbol=sym,
            zh=str(e.get("zh") or sym),
            dim=Dim.from_mapping(dim_map),  # type: ignore[arg-type]
            factor=1.0,
            structure=str(e.get("structure") or "scalar"),
            is_variable=True,
        )
    return out


def check_same_dimension(a: Result, b: Result, what: str = "两侧") -> None:
    """量纲齐次性检查。用于公式自检与加法规则。"""
    if a.dim != b.dim:
        raise EvalError(
            f"{what}量纲不齐次：{a.render_sym()} 是 {a.dim.format_si()}，"
            f"{b.render_sym()} 是 {b.dim.format_si()}"
        )


def significant_equal(a: float, b: float, rel_tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=1e-12)


def _field(obj: object, name: str) -> object:
    """从 dict 或对象上按名取字段。用于兼容测试替身与真实 dataclass。"""
    if isinstance(obj, Mapping):
        return obj[name]
    return getattr(obj, name)
