"""量纲向量：整个系统最底层的唯一依赖源头。

设计要点（见设计文档 2.1）：

量纲是一个 *向量空间* 中的向量：基本维度的幂次构成整数向量。
加减（乘除）、取负（取倒数）、整数缩放（幂运算）都定义良好。

关于弧度的重要设计变更
----------------------
v2 设计文档 2.9 曾规定用 `RAD` 伪维度区分力矩与能量。实现过程中发现
那条路走不通，原因是一条代数事实：

    推导 W = M·θ（转动功 = 力矩 × 角位移）
    若 dim(θ) = {RAD: 1}，则 dim(M) 必须为 {RAD: -1}；
    而 rad·rad 在向量空间里是 {RAD: 2}，不是 0。
    「弧度相乘应相消」是模 2 运算（XOR），不是加法——
    而模 2 结构不属于向量空间，无法与量纲的加减代数共存。

放宽到「M·θ 时 rad 相消」的朴素直觉也不行：那只是特例，不是代数。

因此**放弃把弧度作为量纲维度**。后果是物理事实的直接呈现：

    dim(力矩)   == dim(能量)    == M·L²·T⁻²
    dim(角速度) == dim(频率)    == T⁻¹

这不是缺陷，而是量纲分析的核心一课：**量纲相同不足以判定物理量相同**。
工具通过公式库的上下文（r×F 还是 F·s）来区分它们，而不是靠造假维度。
rad 仍在单位表里作为无因次换算单位存在，但不再影响量纲。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

# SI 七个基本维度，键名用于序列化与展示
BASE_DIMENSIONS: tuple[str, ...] = ("M", "L", "T", "I", "TH", "N", "J")

# 各基本维度的中文名（展示用）
DIMENSION_ZH: dict[str, str] = {
    "M": "质量",
    "L": "长度",
    "T": "时间",
    "I": "电流",
    "TH": "温度",
    "N": "物质的量",
    "J": "发光强度",
}

# 各基本维度的符号（展示用）
DIMENSION_SYM: dict[str, str] = {
    "M": "M",
    "L": "L",
    "T": "T",
    "I": "I",
    "TH": "Θ",
    "N": "N",
    "J": "J",
}

_DISPLAY_ORDER: tuple[str, ...] = BASE_DIMENSIONS
_KNOWN_KEYS = frozenset(_DISPLAY_ORDER)


class DimError(ValueError):
    """量纲运算的非法操作。"""


@dataclass(frozen=True)
class Dim:
    """不可变的量纲向量。

    内部只保存非零分量，因此 Dim({}) 表示无因次量（纯数）。
    frozen=True 使其可哈希，可直接做 dict 键——这是 registry 按键索引的基础。
    """

    powers: Mapping[str, int]

    # ---------- 构造 ----------

    @staticmethod
    def of(**kwargs: int) -> "Dim":
        """Dim.of(M=1, L=1, T=-2) —— 供测试与数据代码书写。"""
        return Dim(kwargs)

    @staticmethod
    def from_mapping(m: Mapping[str, int]) -> "Dim":
        """从 JSON 读取的 dict 构造，并校验键名合法。"""
        if not isinstance(m, Mapping):
            raise DimError(f"量纲必须是对象，收到 {type(m).__name__}")
        bad = set(m) - _KNOWN_KEYS
        if bad:
            raise DimError(
                f"未知量纲键 {sorted(bad)}；合法键为 {sorted(_KNOWN_KEYS)}"
            )
        for k, v in m.items():
            if not isinstance(v, int) or isinstance(v, bool):
                raise DimError(f"量纲指数必须是整数，{k}={v!r}")
        return Dim(m)

    def __post_init__(self) -> None:
        # 剔除零分量，保证「同一向量只有一种表示」——否则 dict 键会失效
        cleaned = {k: int(v) for k, v in self.powers.items() if v != 0}
        bad = set(cleaned) - _KNOWN_KEYS
        if bad:
            raise DimError(f"未知量纲键 {sorted(bad)}")
        object.__setattr__(self, "powers", cleaned)

    # ---------- 向量空间运算 ----------

    def __add__(self, other: "Dim") -> "Dim":
        """量纲相乘：幂次相加。"""
        if not isinstance(other, Dim):
            return NotImplemented
        out = dict(self.powers)
        for k, v in other.powers.items():
            out[k] = out.get(k, 0) + v
        return Dim(out)

    def __sub__(self, other: "Dim") -> "Dim":
        """量纲相除：幂次相减。"""
        if not isinstance(other, Dim):
            return NotImplemented
        return self + (-other)

    def __neg__(self) -> "Dim":
        """取倒数：幂次取负。"""
        return Dim({k: -v for k, v in self.powers.items()})

    def scaled(self, factor: int) -> "Dim":
        """幂运算：所有幂次乘以 factor。factor 必须是整数。"""
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise DimError(f"量纲缩放因子必须是整数，收到 {factor!r}")
        if factor == 0:
            return Dim({})
        return Dim({k: v * factor for k, v in self.powers.items()})

    # ---------- 查询 ----------

    @property
    def is_dimensionless(self) -> bool:
        """是否无因次。"""
        return not self.powers

    def power_of(self, key: str) -> int:
        return self.powers.get(key, 0)

    # ---------- 展示 ----------

    def format_si(self, include_rad: bool = False, superscript: bool = True) -> str:
        """渲染成 SI 量纲式，如 "M·L·T⁻²"。

        include_rad 参数为兼容旧调用保留，当前无弧度维度可显示，故被忽略。

        superscript=True 用 Unicode 上标（M·L·T⁻²）；
        superscript=False 用 ^ 记号（M·L·T^-2），便于纯文本环境与测试断言。
        """
        keys = [k for k in _DISPLAY_ORDER if self.powers.get(k)]
        if not keys:
            return "1"  # 无因次

        parts: list[str] = []
        for k in keys:
            p = self.powers[k]
            sym = DIMENSION_SYM.get(k, k)
            if p == 1:
                parts.append(sym)
            elif superscript:
                parts.append(f"{sym}{_to_superscript(p)}")
            else:
                parts.append(f"{sym}^{p}")
        return "·".join(parts)

    def describe_zh(self) -> str:
        """中文量纲描述，如「质量·长度·时间⁻²」。教学展示用。"""
        keys = [k for k in _DISPLAY_ORDER if self.powers.get(k)]
        if not keys:
            return "无因次"
        parts: list[str] = []
        for k in keys:
            p = self.powers[k]
            name = DIMENSION_ZH.get(k, k)
            if p == 1:
                parts.append(name)
            else:
                parts.append(f"{name}{_to_superscript(p)}")
        return "·".join(parts)

    def to_json(self) -> dict[str, int]:
        """按 _DISPLAY_ORDER 稳定序列化，便于测试与数据往返。"""
        return {k: self.powers[k] for k in _DISPLAY_ORDER if self.powers.get(k)}

    # ---------- 协议 ----------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Dim):
            return NotImplemented
        return dict(self.powers) == dict(other.powers)

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.powers.items())))

    def __repr__(self) -> str:
        return f"Dim({self.format_si(superscript=False)})"


_SUPERSCRIPT_MAP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _to_superscript(n: int) -> str:
    return str(n).translate(_SUPERSCRIPT_MAP)


def combine(dims: Iterable[Dim]) -> Dim:
    """把一组量纲相乘合并。空序列返回无因次量。"""
    out = Dim({})
    for d in dims:
        out = out + d
    return out


# 常用量纲常量，供测试与数据代码直接引用
DIMENSIONLESS = Dim({})
LENGTH = Dim.of(L=1)
MASS = Dim.of(M=1)
TIME = Dim.of(T=1)
CURRENT = Dim.of(I=1)
TEMPERATURE = Dim.of(TH=1)

# 力矩与能量、角速度与频率在同量纲组内的对照，便于教学展示与测试
ENERGY_DIM = Dim.of(M=1, L=2, T=-2)
