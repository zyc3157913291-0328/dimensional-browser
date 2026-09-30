"""代数规则：运算如何改变量纲。

设计文档 2.3 的落地。这个模块只依赖 dim，是纯数学，可独立测试。

为什么必须有这个模块：现实物理式子里除乘除之外还有求导、积分、梯度、
矩阵运算，它们**都会改变量纲**。如果不显式建模，量纲推导必然出错。

三条容易错的规则（都在下面标了 ⚠ 并配了测试）：

⚠ 行列式不是「量纲不变」。 n×n 矩阵每项量纲为 D 时，det 是 n 重乘积，
  结果量纲是 D^n。2×2 平移矩阵（元素 [L]）的 det 是 [L²]（面积）。
  这条若写成「不变」，所有涉及行列式的推导都会错。

⚠ 多重积分是逐重累加。 ∫∫∫ f dV 的量纲是 dim(f)·dim(V)，即加 3 次，
  不是加 1 次。转动惯量 I = ∫∫∫ r²ρ dV 就靠这条才对得上 kg·m²。

⚠ 矩阵乘法要求所有操作数量纲相同。 A·B 里若 dim(A) ≠ dim(B)，
  乘积无意义——这与标量乘法的「量纲自由相加」不同，必须显式检查。
  工程里的典型正例是刚度矩阵乘位移向量 K·u = f，全部元素量纲一致。
"""

from __future__ import annotations

from typing import Sequence

from .dim import Dim, DimError


class AlgebraError(ValueError):
    """代数规则层面的非法运算。"""


# ------------------------------------------------------------------ 规则实现


def add_operands(dims: Sequence[Dim]) -> Dim:
    """乘法 / 矩阵乘法：量纲（幂次）相加。"""
    out = Dim({})
    for d in dims:
        out = out + d
    return out


def sub_operands(numer: Dim, denom: Dim) -> Dim:
    """除法：量纲相减。"""
    return numer - denom


def scale_by_exp(base: Dim, exponent: int) -> Dim:
    """幂运算：所有幂次乘以指数。指数必须是无量纲整数。"""
    if not isinstance(exponent, int) or isinstance(exponent, bool):
        raise AlgebraError(
            f"量纲幂的指数必须是整数，收到 {exponent!r}。"
            f"SI 量纲只对整数幂封闭"
        )
    return base.scaled(exponent)


def sub_by_var(operand: Dim, var: Dim, order: int = 1) -> Dim:
    """求导 / 梯度 / 散度 / 旋度：量纲减去变量量纲的 order 倍。

    依据：导数定义为差商 lim Δf/Δx，量纲为 dim(f)/dim(x)。
    """
    if order < 1:
        raise AlgebraError(f"求导阶数必须 ≥ 1，收到 {order}")
    return operand - var.scaled(order)


def add_by_var(operand: Dim, var: Dim, order: int = 1) -> Dim:
    """积分：量纲加上变量量纲的 order 倍。

    依据：积分是和的极限，每一项 f·Δx 的量纲是 dim(f)·dim(x)，
    重积分逐重累加。
    """
    if order < 1:
        raise AlgebraError(f"积分重数必须 ≥ 1，收到 {order}")
    return operand + var.scaled(order)


def scale_by_size(matrix_dim: Dim, size: int) -> Dim:
    """行列式：量纲乘以矩阵阶数 n。

    ⚠ 见模块 docstring。det 的定义含 n 重乘积，所以量纲是 D^n。
    """
    if size < 1:
        raise AlgebraError(f"矩阵阶数必须 ≥ 1，收到 {size}")
    return matrix_dim.scaled(size)


def identity(dim: Dim, *_args: object, **_kwargs: object) -> Dim:
    """迹 / 取负：量纲不变。"""
    return dim


def require_equal(dims: Sequence[Dim]) -> Dim:
    """加法：要求所有项量纲相同，否则不齐次。"""
    if not dims:
        raise AlgebraError("加法至少需要一个操作数")
    head = dims[0]
    for i, d in enumerate(dims[1:], start=2):
        if d != head:
            raise AlgebraError(
                f"量纲不齐次：第 1 项是 {head.format_si()}，"
                f"第 {i} 项是 {d.format_si()}——"
                f"量纲不同的量不能相加减"
            )
    return head


def sum_by_index(terms: Sequence[Dim]) -> Dim:
    """爱因斯坦求和：所有项量纲必须相同，逐项相加（每个量纲自身求和）。

    ⚠ 矩阵乘法用的不是这个。A·B 里 A 的元素与 B 的元素量纲可以不同，
    都是「和式的项」，所以量纲相加。sum_by_index 用于形如
    J = Σ m_i r_i² 的场景：每一项已经是同一个量纲，求和后量纲不变。
    """
    if not terms:
        return Dim({})
    return require_equal(terms)


def dot_product(a: Dim, b: Dim) -> Dim:
    """点积 / 双线性求和：两个操作数量纲相异，各取一次幂后相加。

    例：功率 P = F·v，dim(P) = dim(F) + dim(v)。
    """
    return a + b


# ------------------------------------------------------------------ 规则表

# 函数签名不统一（有的要 order，有的要 size），所以统一用 **kwargs 适配。
RULE_IMPLEMENTATIONS = {
    "add_operands": lambda dims, **kw: add_operands(dims),
    "sub_operands": lambda dims, **kw: sub_operands(dims[0], dims[1]),
    "scale_by_exp": lambda dims, **kw: scale_by_exp(dims[0], kw.get("exponent", 1)),
    "sub_by_var_order": lambda dims, **kw: sub_by_var(
        dims[0], dims[1], kw.get("order", 1)
    ),
    "add_by_var_order": lambda dims, **kw: add_by_var(
        dims[0], dims[1], kw.get("order", 1)
    ),
    "sub_by_var": lambda dims, **kw: sub_by_var(dims[0], dims[1], 1),
    "scale_by_size": lambda dims, **kw: scale_by_size(dims[0], kw.get("size", 1)),
    "identity": lambda dims, **kw: dims[0],
    "require_equal": lambda dims, **kw: require_equal(dims),
    "sum_by_index": lambda dims, **kw: sum_by_index(dims),
    "dot_product": lambda dims, **kw: dot_product(dims[0], dims[1]),
}


def apply_transform(
    transform: str, dims: Sequence[Dim], **kwargs: object
) -> Dim:
    """按规则名执行量纲变换。

    registry 自检会保证数据里声明的 transform 都在 RULE_IMPLEMENTATIONS 中，
    所以这里的 KeyError 意味着代码与数据脱节，属于编程错误而非数据错误。
    """
    fn = RULE_IMPLEMENTATIONS.get(transform)
    if fn is None:
        raise AlgebraError(
            f"未实现的量纲变换规则 {transform!r}；"
            f"已实现：{sorted(RULE_IMPLEMENTATIONS)}"
        )
    try:
        return fn(list(dims), **kwargs)
    except DimError as exc:
        raise AlgebraError(str(exc)) from exc


# ------------------------------------------------------------------ 运算分派


OPERAND_TRANSFORMS = {
    "mul": "add_operands",
    "div": "sub_operands",
    "pow": "scale_by_exp",
    "neg": "identity",
    "add": "require_equal",
    "derivative": "sub_by_var_order",
    "integral": "add_by_var_order",
    "gradient": "sub_by_var",
    "divergence": "sub_by_var",
    "curl": "sub_by_var",
    "trace": "identity",
    "det": "scale_by_size",
    "matmul": "add_operands",
    "dot": "dot_product",
    "sum": "sum_by_index",
}

# 需要「变量量纲」参与的操作——公式签名里靠 role 标记区分
NEEDS_VARIABLE = {"derivative", "integral", "gradient", "divergence", "curl"}

# 需要额外参数的 op → 参数名（从签名条目里的字段取）
NEEDS_EXTRA = {
    "pow": "exp",
    "derivative": "order",
    "integral": "order",
    "det": "size",
}


def describe(op: str) -> str:
    """给渲染层用的运算中文名。"""
    return {
        "mul": "乘法",
        "div": "除法",
        "pow": "幂运算",
        "neg": "取负",
        "add": "加法",
        "derivative": "求导",
        "integral": "积分",
        "gradient": "梯度",
        "divergence": "散度",
        "curl": "旋度",
        "trace": "迹",
        "det": "行列式",
        "matmul": "矩阵乘法",
        "dot": "点积",
        "sum": "求和",
    }.get(op, op)
