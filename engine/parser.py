"""单位表达式解析器：文本 → AST。

设计文档 2.x 的分层要求：本模块只做语法，不懂量纲。
解析失败（语法错误）与量纲不兼容（求值错误）必须是两类错误，不能混淆。

支持语法：
    3 kg·m^2/s^2        数字 + 单位组合
    N/C                 斜杠除法
    kg/(m·s^2)          括号分组
    m^0.5               分数幂（指数可以是小数）
    rad/s               弧度标记参与运算
    1/s                 纯分母表达式

书写约定：
    · * . 与空格都表示乘法
    ^ 或 ** 表示幂
    / 表示除法（左结合，优先级低于幂）

刻意的限制：不允许隐式「数字跟随数字」，所以 "2 3" 报错而非静默相乘。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


class ParseError(ValueError):
    """语法错误。与量纲错误（DimError / EvalError）严格区分。"""


# ---------------------------------------------------------------- AST 节点


@dataclass(frozen=True)
class Num:
    """数值字面量。系数可以是有理数（如 1/2 写成 0.5）。"""

    value: float

    def render(self) -> str:
        if self.value == int(self.value) and abs(self.value) < 1e15:
            return str(int(self.value))
        return f"{self.value:g}"


@dataclass(frozen=True)
class Ident:
    """标识符：可能是已注册单位（含词头形式），也可能是变量。

    解析阶段不做判定——是否注册由求值阶段结合上下文决定。
    这样同一个 AST 既能表达 "N"（单位），也能表达 "F"（公式里的符号量）。
    """

    name: str


@dataclass(frozen=True)
class Pow:
    base: "Node"
    exp: "Node"


@dataclass(frozen=True)
class Mul:
    factors: tuple["Node", ...]


@dataclass(frozen=True)
class Div:
    numer: "Node"
    denom: "Node"


@dataclass(frozen=True)
class Neg:
    operand: "Node"


@dataclass(frozen=True)
class Add:
    """加法。量纲上要求两侧必须齐次，由求值器检查。

    减法表达为 Add(lhs, Neg(rhs))——保持节点种类最小，
    且「减法就是加上相反数」本身就是量纲分析里正确的说法。
    """

    terms: tuple["Node", ...]


Node = Union[Num, Ident, Pow, Mul, Div, Neg, Add]


# ---------------------------------------------------------------- 词法分析

# 多字符运算符要排在单字符前面
_MULTI_OPS = ("**",)
_SINGLE_OPS = {
    "*": "MUL",
    "·": "MUL",
    ".": "MUL",
    "⋅": "MUL",
    "×": "MUL",
    "/": "DIV",
    "÷": "DIV",
    "^": "POW",
    "(": "LPAREN",
    ")": "RPAREN",
    "+": "PLUS",
    "-": "MINUS",
}

_IDENT_START_EXTRA = "_°"
# 允许出现在标识符内的额外字符（中文单位名、希腊字母由 str.isalpha 覆盖）
_IDENT_BODY_EXTRA = "_°₀₁₂₃₄₅₆₇₈₉"


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch in _IDENT_START_EXTRA or ord(ch) > 0x2FFF


def _is_ident_body(ch: str) -> bool:
    return _is_ident_start(ch) or ch.isdigit() or ch in _IDENT_BODY_EXTRA


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]

        if ch.isspace():
            i += 1
            continue

        # 多字符运算符
        matched = False
        for op in _MULTI_OPS:
            if src.startswith(op, i):
                tokens.append(Token("POW", op, i))
                i += len(op)
                matched = True
                break
        if matched:
            continue

        # 数字（含科学计数法与小数）
        if ch.isdigit():
            j = i
            seen_dot = False
            while j < n and (src[j].isdigit() or (src[j] == "." and not seen_dot)):
                if src[j] == ".":
                    # 小数点后面必须跟数字，否则 "." 是乘号
                    if j + 1 >= n or not src[j + 1].isdigit():
                        break
                    seen_dot = True
                j += 1
            # 科学计数法：e/E 后跟可选符号与数字
            if j < n and src[j] in "eE":
                k = j + 1
                if k < n and src[k] in "+-":
                    k += 1
                if k < n and src[k].isdigit():
                    while k < n and src[k].isdigit():
                        k += 1
                    j = k
            tokens.append(Token("NUMBER", src[i:j], i))
            i = j
            continue

        # 标识符
        if _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_body(src[j]):
                j += 1
            tokens.append(Token("IDENT", src[i:j], i))
            i = j
            continue

        if ch in _SINGLE_OPS:
            tokens.append(Token(_SINGLE_OPS[ch], ch, i))
            i += 1
            continue

        raise ParseError(f"位置 {i}：无法识别的字符 {ch!r}")

    tokens.append(Token("EOF", "", n))
    return tokens


# ---------------------------------------------------------------- 语法分析


class _Parser:
    """递归下降。

    优先级（低 → 高）：
        additive      + -
        multiplicative * · / 以及隐式乘法
        power          ^（右结合）
        unary          - +
        primary        数字 / 标识符 / 括号
    """

    def __init__(self, tokens: list[Token], src: str) -> None:
        self.tokens = tokens
        self.src = src
        self.pos = 0

    # -------- 基础设施 --------

    @property
    def cur(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind: str) -> Token:
        if self.cur.kind != kind:
            raise ParseError(
                f"位置 {self.cur.pos}：期望 {kind}，实际是 "
                f"{self.cur.kind}({self.cur.text!r})"
            )
        return self.advance()

    # -------- 入口 --------

    def parse(self) -> Node:
        node = self.parse_additive()
        if self.cur.kind != "EOF":
            raise ParseError(
                f"位置 {self.cur.pos}：表达式在 {self.cur.text!r} 处意外结束或残留"
            )
        return node

    def parse_additive(self) -> Node:
        terms: list[Node] = [self.parse_multiplicative()]
        while self.cur.kind in ("PLUS", "MINUS"):
            op = self.advance()
            rhs = self.parse_multiplicative()
            terms.append(rhs if op.kind == "PLUS" else Neg(rhs))
        if len(terms) == 1:
            return terms[0]
        return Add(tuple(terms))

    def parse_multiplicative(self) -> Node:
        factors: list[Node] = [self.parse_power()]
        while True:
            if self.cur.kind == "MUL":
                self.advance()
                factors.append(self.parse_power())
                continue
            if self.cur.kind == "DIV":
                self.advance()
                denom = self.parse_power()
                combined = factors[0] if len(factors) == 1 else Mul(tuple(factors))
                factors = [Div(combined, denom)]
                continue
            # 隐式乘法：下一个 token 能开始一个因子，且不是数字
            if self._starts_implicit_factor():
                factors.append(self.parse_power())
                continue
            break
        if len(factors) == 1:
            return factors[0]
        return Mul(tuple(factors))

    def _starts_implicit_factor(self) -> bool:
        """判断是否允许隐式乘法（如 "kg m" 或 "(a)(b)"）。

        刻意不允许「标识符 后跟 数字」的隐式乘法：
        "m 2" 视为语法错误，而不是静默变成 2m。
        """
        return self.cur.kind in ("IDENT", "LPAREN")

    def parse_power(self) -> Node:
        base = self.parse_unary()
        if self.cur.kind == "POW":
            self.advance()
            # 右结合 + 指数侧可带一元负号：m^-2, m^0.5
            exp = self.parse_power()
            return Pow(base, exp)
        return base

    def parse_unary(self) -> Node:
        if self.cur.kind == "MINUS":
            self.advance()
            return Neg(self.parse_unary())
        if self.cur.kind == "PLUS":
            self.advance()
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.cur
        if tok.kind == "NUMBER":
            self.advance()
            return Num(float(tok.text))
        if tok.kind == "IDENT":
            self.advance()
            return Ident(tok.text)
        if tok.kind == "LPAREN":
            self.advance()
            node = self.parse_additive()
            self.expect("RPAREN")
            return node
        raise ParseError(
            f"位置 {tok.pos}：期望数字、单位名或左括号，实际是 "
            f"{tok.kind}({tok.text!r})"
        )


def parse(src: str) -> Node:
    """把单位表达式文本解析成 AST。语法错误抛 ParseError。"""
    if not isinstance(src, str):
        raise ParseError(f"表达式必须是字符串，收到 {type(src).__name__}")
    if not src.strip():
        raise ParseError("表达式为空")
    return _Parser(tokenize(src), src).parse()


# ---------------------------------------------------------------- 渲染


def render(node: Node) -> str:
    """把 AST 还原成规范文本形式（幂用 ^，乘法用 ·）。

    规范化保证：parse → render → parse 幂等（属性测试会验证这一点）。
    """
    return _render(node, parent_prec=0)


_PREC_ADD = 1
_PREC_MUL = 2
_PREC_POW = 3


def _render(node: Node, parent_prec: int) -> str:
    if isinstance(node, Num):
        return node.render()

    if isinstance(node, Ident):
        return node.name

    if isinstance(node, Neg):
        inner = _render(node.operand, _PREC_MUL)
        text = f"-{inner}"
        return f"({text})" if parent_prec > _PREC_MUL else text

    if isinstance(node, Pow):
        base = _render(node.base, _PREC_POW)
        exp_node = node.exp
        # 指数为「简单负值」时（m^-2、s^-1）不需要括号：手工拼负号，
        # 绕开 Neg 节点自身的括号逻辑（它是为乘法上下文设计的）。
        if isinstance(exp_node, Neg) and not isinstance(
            exp_node.operand, (Mul, Div, Add)
        ):
            exp = "-" + _render(exp_node.operand, _PREC_POW)
        else:
            exp_needs_paren = isinstance(exp_node, (Mul, Div, Add))
            exp = _render(exp_node, _PREC_POW)
            if exp_needs_paren:
                exp = f"({exp})"
        return f"{base}^{exp}"

    if isinstance(node, Mul):
        parts = [_render(f, _PREC_MUL + 1) for f in node.factors]
        text = "·".join(parts)
        return f"({text})" if parent_prec > _PREC_MUL else text

    if isinstance(node, Div):
        # 分子不需要括号：a·b/c 的默认结合性就是 (a·b)/c
        numer = _render(node.numer, _PREC_MUL)
        # 分母是复合乘积时需要括号：a/(b·c)；一元负号不需要
        denom_prec = _PREC_MUL if isinstance(node.denom, Neg) else _PREC_MUL + 1
        denom = _render(node.denom, denom_prec)
        text = f"{numer}/{denom}"
        return f"({text})" if parent_prec > _PREC_MUL else text

    if isinstance(node, Add):
        chunks: list[str] = []
        for i, term in enumerate(node.terms):
            if isinstance(term, Neg):
                inner = _render(term.operand, _PREC_ADD + 1)
                # 首项带负号写作 "-a"，后续项写作 "- a"
                chunks.append(f"-{inner}" if i == 0 else f"- {inner}")
            else:
                text = _render(term, _PREC_ADD + 1)
                chunks.append(text if i == 0 else f"+ {text}")
        text = " ".join(chunks)
        return f"({text})" if parent_prec > _PREC_ADD else text

    raise ParseError(f"未知 AST 节点类型 {type(node).__name__}")


# ---------------------------------------------------------------- 规范形


def canonical(node: Node) -> str:
    """把 AST 化成**结构规范形**，用于「这两个写法是不是同一个东西」的比较。

    为什么需要它
    ------------
    `parse → render` 只保证语法幂等，不保证**交换律**归一：
    `N·m` 与 `m·N` 渲染成两个不同字符串。于是去重集合把它们当成两条，
    界面上「常见写法」写着 `N·m`、「算法枚举」又列一遍 `m·N`。

    规范形做了三件事：
      1. 把 Div / 负幂统一成**指数**（`N/m^2` 与 `N·m^-2` 同键）
      2. 乘法的因子按基名排序，并把同名基的指数相加（`m·m` → `m^2`）
      3. 数字系数单独提出，不参与因子排序

    它是**比较键**，不是给人看的文本——展示仍走 render / 单位渲染器。
    """
    if isinstance(node, Add):
        return " + ".join(sorted(canonical(t) for t in node.terms))
    coeff, powers, extras = _split_product(node)
    parts: list[str] = []
    for base in sorted(powers):
        exp = powers[base]
        if exp == 0:
            continue
        parts.append(base if exp == 1 else f"{base}^{_fmt_exp(exp)}")
    parts.extend(sorted(extras))
    if coeff != 1 or not parts:
        parts.insert(0, _fmt_num(coeff))
    return "·".join(parts)


def canonical_expr(expr: str) -> str:
    """文本版规范形。解析失败时退回原文本（去空白），保证总能得到键。"""
    try:
        return canonical(parse(expr))
    except ParseError:
        return " ".join(str(expr).split())


def _fmt_num(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:g}"


def _fmt_exp(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:g}"


def _split_product(
    node: Node,
) -> tuple[float, dict[str, float], list[str]]:
    """把节点拆成（数字系数, {基: 指数}, 无法归并的字面量）。"""
    coeff = 1.0
    powers: dict[str, float] = {}
    extras: list[str] = []

    def add(base: str, exp: float) -> None:
        if exp == 0:
            return
        powers[base] = powers.get(base, 0.0) + exp
        if powers[base] == 0:
            del powers[base]

    def walk(n: Node, sign: float) -> None:
        nonlocal coeff
        if isinstance(n, Num):
            # 分母里的数字（如 1/2 这种系数写法）取倒数。
            coeff *= (n.value ** sign) if sign != 1 else n.value
            return
        if isinstance(n, Mul):
            for f in n.factors:
                walk(f, sign)
            return
        if isinstance(n, Div):
            walk(n.numer, sign)
            walk(n.denom, -sign)
            return
        if isinstance(n, Pow):
            e = _num_value(n.exp)
            if e is not None:
                for base, exp in _as_powers(n.base):
                    add(base, exp * e * sign)
                return
            extras.append(canonical(n))
            return
        if isinstance(n, Neg):
            # 一元负号在量纲上下文里没有意义（量纲非负），
            # 当作恒等处理即可，避免 -m 与 m 被当成两个键。
            walk(n.operand, sign)
            return
        if isinstance(n, Add):
            # 加法只能整体当一个字面量，不能再往下拆（拆了会无限递归）
            extras.append(canonical(n))
            return
        if isinstance(n, Ident):
            add(n.name, sign)
            return
        extras.append(canonical(n))

    walk(node, 1)
    return coeff, powers, extras


def _as_powers(node: Node) -> list[tuple[str, float]]:
    """把一个基节点展开成 (基文本, 指数) 列表。"""
    _c, powers, extras = _split_product(node)
    out = list(powers.items())
    out.extend((e, 1.0) for e in extras)
    return out


def _num_value(node: Node) -> float | None:
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Neg):
        inner = _num_value(node.operand)
        return None if inner is None else -inner
    return None


def iter_idents(node: Node):
    """深度优先产出所有标识符名，供求值器收集未知符号。"""
    if isinstance(node, Ident):
        yield node.name
    elif isinstance(node, Pow):
        yield from iter_idents(node.base)
        yield from iter_idents(node.exp)
    elif isinstance(node, Mul):
        for f in node.factors:
            yield from iter_idents(f)
    elif isinstance(node, Div):
        yield from iter_idents(node.numer)
        yield from iter_idents(node.denom)
    elif isinstance(node, Neg):
        yield from iter_idents(node.operand)
    elif isinstance(node, Add):
        for t in node.terms:
            yield from iter_idents(t)
