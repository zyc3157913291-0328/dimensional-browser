"""引擎单元测试。

零依赖：不引入 pytest，直接用 assert + 简单 runner，因为整个项目
坚持零第三方依赖（设计文档 5.1）。用例量大到需要 fixture 时再考虑换。

覆盖优先级的依据是「哪里的错误最隐蔽」：
  代数规则 > 数据自检 > 解析器 > 求值器
代数规则错了会让整棵树都错，而且是看起来对的错。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import algebra, parser as P  # noqa: E402
from engine.dim import Dim, DimError  # noqa: E402
from engine.evaluator import EvalError, UnitResolver, evaluate  # noqa: E402
from engine.registry import Registry, RegistryError, _derive_rhs_dim  # noqa: E402
from engine.search import DimensionSearch, SearchBounds, SearchError  # noqa: E402
from engine.tree import TreeBuilder  # noqa: E402

_FAILURES: list[str] = []
_PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED
    if condition:
        _PASSED += 1
    else:
        _FAILURES.append(f"{name}{(': ' + detail) if detail else ''}")


def raises(name: str, exc_type, fn, *args, **kwargs) -> None:
    """断言调用抛出指定异常类型。"""
    global _PASSED
    try:
        fn(*args, **kwargs)
    except exc_type:
        _PASSED += 1
    except Exception as exc:  # noqa: BLE001
        _FAILURES.append(
            f"{name}: 期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}"
        )
    else:
        _FAILURES.append(f"{name}: 期望 {exc_type.__name__}，但没有抛出异常")


# ============================================================ 量纲向量


def test_dim_vector_space() -> None:
    length = Dim.of(L=1)
    time = Dim.of(T=1)
    mass = Dim.of(M=1)

    # 乘除 = 幂次加减
    check("dim: 长度/时间", (length - time) == Dim.of(L=1, T=-1))
    check("dim: 取倒数", (-time) == Dim.of(T=-1))
    check("dim: 三因子相乘", (mass + length + length) == Dim.of(M=1, L=2))

    # 幂运算
    check("dim: 平方", Dim.of(L=1).scaled(2) == Dim.of(L=2))
    check("dim: 零次幂得无因次", Dim.of(L=3).scaled(0).is_dimensionless)

    # 零分量必须被剔除，否则同一个向量会有多种表示，dict 键就失效了
    check(
        "dim: 零分量剔除",
        Dim.of(L=1) + Dim.of(L=-1) == Dim.of(),
        f"得到 {(Dim.of(L=1) + Dim.of(L=-1)).powers}",
    )
    check(
        "dim: 可哈希作字典键",
        len({Dim.of(L=1), Dim.of(L=1), Dim.of(L=1, T=0)}) == 1,
    )

    # 非法输入
    raises("dim: 拒绝未知键", DimError, Dim.from_mapping, {"XYZ": 1})
    raises("dim: 拒绝小数指数", DimError, Dim.from_mapping, {"L": 0.5})
    raises("dim: 拒绝非整数缩放", DimError, Dim.of(L=1).scaled, 0.5)

    # 展示
    check("dim: SI 渲染", Dim.of(M=1, L=1, T=-2).format_si() == "M·L·T⁻²")
    check("dim: 无因次渲染", Dim.of().format_si() == "1")
    check(
        "dim: ^ 记号渲染",
        Dim.of(M=1, L=1, T=-2).format_si(superscript=False) == "M·L·T^-2",
    )
    check("dim: 中文描述", "质量" in Dim.of(M=1).describe_zh())


# ============================================================ 代数规则


def test_algebra_multiplication_division() -> None:
    check(
        "algebra: 乘法量纲相加",
        algebra.add_operands([Dim.of(M=1), Dim.of(L=1, T=-2)]) == Dim.of(M=1, L=1, T=-2),
    )
    check(
        "algebra: 除法量纲相减",
        algebra.sub_operands(Dim.of(L=1), Dim.of(T=1)) == Dim.of(L=1, T=-1),
    )
    raises(
        "algebra: 幂指数必须整数",
        algebra.AlgebraError,
        algebra.scale_by_exp,
        Dim.of(L=1),
        0.5,
    )


def test_algebra_derivative_integral() -> None:
    """求导 / 积分：设计文档 2.3 的核心两条规则。"""
    potential = Dim.of(M=1, L=2, T=-2)  # 能量 [J]
    x = Dim.of(L=1)

    # F = -dU/dx ：能量对长度求导得力
    check(
        "algebra: 能量对长度求导 = 力的量纲",
        algebra.sub_by_var(potential, x, 1) == Dim.of(M=1, L=1, T=-2),
    )

    # 二阶导数：位移对时间两次求导得加速度
    check(
        "algebra: 位移对时间二阶导 = 加速度量纲",
        algebra.sub_by_var(Dim.of(L=1), Dim.of(T=1), 2) == Dim.of(L=1, T=-2),
    )

    # p = ∫F dt ：力对时间积分得动量
    force = Dim.of(M=1, L=1, T=-2)
    check(
        "algebra: 力对时间积分 = 动量量纲",
        algebra.add_by_var(force, Dim.of(T=1), 1) == Dim.of(M=1, L=1, T=-1),
    )

    # 互逆性：积分与求导必须抵消。这是最重要的一条不变式。
    for base in (potential, force, Dim.of(M=1, L=2, T=-1)):
        for var in (Dim.of(L=1), Dim.of(T=1)):
            back = algebra.add_by_var(algebra.sub_by_var(base, var, 1), var, 1)
            check(
                f"algebra: 求导积分互逆 {base.format_si()} wrt {var.format_si()}",
                back == base,
            )

    raises("algebra: 求导阶数必须 ≥1", algebra.AlgebraError, algebra.sub_by_var,
           potential, x, 0)
    raises("algebra: 积分重数必须 ≥1", algebra.AlgebraError, algebra.add_by_var,
           potential, x, 0)


def test_algebra_determinant() -> None:
    """⚠ 最容易写错的一条：行列式不是「量纲不变」。

    n×n 矩阵每项量纲为 D 时 det 得 D^n。这条若做错，
    所有涉及行列式的推导（雅可比行列式、度规张量）都会静默错。
    """
    # 2×2 平移矩阵，元素量纲 [L]，det 是面积 [L²]
    check(
        "algebra: det 2×2 得平方量纲",
        algebra.scale_by_size(Dim.of(L=1), 2) == Dim.of(L=2),
    )
    # 3×3：体积
    check(
        "algebra: det 3×3 得三次方量纲",
        algebra.scale_by_size(Dim.of(L=1), 3) == Dim.of(L=3),
    )
    # 反对称性验证：det 与「对角线逐项相乘」结果一致
    diag = Dim.of(M=1, L=1, T=-2)
    check(
        "algebra: det 等于对角元逐项相乘",
        algebra.scale_by_size(diag, 3) == algebra.add_operands([diag] * 3),
    )
    raises("algebra: 阶数必须 ≥1", algebra.AlgebraError, algebra.scale_by_size,
           Dim.of(L=1), 0)


def test_algebra_trace_and_matmul() -> None:
    # 迹：对角元之和，量纲不变
    stress = Dim.of(M=1, L=-1, T=-2)
    check("algebra: 迹量纲不变", algebra.identity(stress) == stress)

    # 矩阵乘向量：量纲相加（刚度矩阵 K·位移 u = 力 f 的量纲关系）
    k = Dim.of(M=1, T=-2)  # N/m
    u = Dim.of(L=1)
    check(
        "algebra: matmul 量纲相加",
        algebra.add_operands([k, u]) == Dim.of(M=1, L=1, T=-2),
    )


def test_algebra_require_equal() -> None:
    """加法要求齐次——这是量纲分析的核心约束。"""
    check(
        "algebra: 同量纲可相加",
        algebra.require_equal([Dim.of(L=1), Dim.of(L=1)]) == Dim.of(L=1),
    )
    raises(
        "algebra: 不同量纲不可相加",
        algebra.AlgebraError,
        algebra.require_equal,
        [Dim.of(L=1), Dim.of(T=1)],
    )


def test_algebra_rules_table_consistency() -> None:
    """代数规则表声明的每个 transform 都必须有实现。"""
    reg = Registry.load()
    for op, rule in reg.algebra_rules.items():
        check(
            f"algebra: 规则 {op} 的 transform 已实现",
            rule.transform in algebra.RULE_IMPLEMENTATIONS,
            f"{rule.transform} 未实现",
        )
    # 反向：实现的规则都应该在表里被声明（避免实现被遗忘但无人使用）
    check(
        "algebra: 规则表非空",
        len(reg.algebra_rules) >= 10,
        f"只有 {len(reg.algebra_rules)} 条",
    )


# ============================================================ 解析器


def test_parser_basics() -> None:
    cases = [
        ("m", "m"),
        ("kg·m/s^2", "kg·m/s^2"),
        ("kg*m/s**2", "kg·m/s^2"),
        ("N/C", "N/C"),
        ("kg/(m·s^2)", "kg/(m·s^2)"),
        ("m^2", "m^2"),
        ("m^-2", "m^-2"),
        ("1/s", "1/s"),
        ("2 kg", "2·kg"),
        ("kg m", "kg·m"),
    ]
    for src, expected in cases:
        got = P.render(P.parse(src))
        check(f"parser: {src!r} → {expected!r}", got == expected, f"得到 {got!r}")


def test_parser_errors() -> None:
    """语法错误必须是 ParseError，不能是别的异常类型泄漏出来。"""
    for bad in ("", "   ", "m/", "(m", "m)", "m//s", "2 3", "@", "m $ s"):
        raises(f"parser: {bad!r} 应报错", P.ParseError, P.parse, bad)


def test_parser_roundtrip() -> None:
    """parse → render → parse 必须幂等。"""
    srcs = [
        "m",
        "kg·m/s^2",
        "kg/(m·s^2)",
        "N·m/rad",
        "m^0.5",
        "1/(s·A)",
        "J/(kg·K)",
        "m^3/(kg·s^2)",
    ]
    for src in srcs:
        once = P.render(P.parse(src))
        twice = P.render(P.parse(once))
        check(f"parser: 幂等 {src!r}", once == twice, f"{once!r} vs {twice!r}")


def test_parser_addition() -> None:
    """加法必须被支持（公式里有 m1·a + m2·a 这类式子）。"""
    node = P.parse("m1·a + m2·a")
    check("parser: 加法可解析", isinstance(node, P.Add), f"得到 {type(node).__name__}")
    check("parser: 加法可渲染", P.render(node) == "m1·a + m2·a", P.render(node))
    node2 = P.parse("a - b")
    check("parser: 减法化为加负数", isinstance(node2, P.Add))


# ============================================================ 求值器


def _make_resolver() -> UnitResolver:
    reg = Registry.load()
    assert reg.resolver is not None
    return reg.resolver


def test_evaluator_dimensions() -> None:
    reg = Registry.load()

    cases = [
        ("N", "M·L·T⁻²"),
        ("J", "M·L²·T⁻²"),
        ("W", "M·L²·T⁻³"),
        ("Pa", "M·L⁻¹·T⁻²"),
        ("V", "M·L²·T⁻³·I⁻¹"),
        ("N/C", "M·L·T⁻³·I⁻¹"),
        ("V/m", "M·L·T⁻³·I⁻¹"),
        ("kg·m/s^2", "M·L·T⁻²"),
    ]
    for expr, expected in cases:
        _node, res = reg.eval_expr(expr)
        got = res.dim.format_si()
        check(f"evaluator: {expr} → {expected}", got == expected, f"得到 {got}")


def test_evaluator_n_c_equals_v_per_m() -> None:
    """教学核心断言：N/C 与 V/m 必须完全同量纲。

    这正是「电场强度的两种单位写法其实是同一个量」的引擎级证据。
    """
    reg = Registry.load()
    _n1, a = reg.eval_expr("N/C")
    _n2, b = reg.eval_expr("V/m")
    check(
        "evaluator: N/C == V/m",
        a.dim == b.dim,
        f"N/C={a.dim.format_si()} 但 V/m={b.dim.format_si()}",
    )


def test_evaluator_torque_energy_same_dim() -> None:
    """力矩与能量量纲相同——这是物理事实，必须有测试固化它。

    如果哪天有人想「修好」这个「问题」，这条测试会拦住他。
    """
    reg = Registry.load()
    _n1, torque = reg.eval_expr("N·m")
    _n2, energy = reg.eval_expr("J")
    check("evaluator: N·m 与 J 同量纲", torque.dim == energy.dim)


def test_evaluator_prefix() -> None:
    reg = Registry.load()

    _n, km = reg.eval_expr("km")
    check("evaluator: km 系数为 1000", km.value == 1000.0, f"得到 {km.value}")
    check("evaluator: km 中文名为千米", km.zh == "千米", km.zh)

    _n, ms = reg.eval_expr("ms")
    check("evaluator: ms 系数为 0.001", abs(ms.value - 0.001) < 1e-15, f"{ms.value}")

    _n, mm = reg.eval_expr("mm")
    check("evaluator: mm 系数为 0.001", abs(mm.value - 0.001) < 1e-15)

    # N 是可加词头的导出单位
    _n, kN = reg.eval_expr("kN")
    check("evaluator: kN 系数为 1000", kN.value == 1000.0)

    # kg 已经含词头，必须拒绝再加前缀
    raises("evaluator: mkg 非法", EvalError, reg.eval_expr, "mkg")
    # 未注册单位
    raises("evaluator: 未注册单位报错", EvalError, reg.eval_expr, "furlong")


def test_evaluator_chinese_names() -> None:
    """中文名不是从符号反译的，是沿 AST 结构拼出来的。"""
    reg = Registry.load()

    _n, j = reg.eval_expr("J")
    check("evaluator: J 中文名为焦", j.zh == "焦", j.zh)

    _n, jm = reg.eval_expr("J/m")
    check("evaluator: J/m 中文为焦每米", jm.zh == "焦每米", jm.zh)

    _n, jms = reg.eval_expr("kg·m/s^2")
    check(
        "evaluator: 复合式中文名保留结构",
        "每" in jms.zh and "千克" in jms.zh,
        jms.zh,
    )


def test_evaluator_addition_requires_homogeneous() -> None:
    """量纲不齐次的加法必须报错，且错误信息要指明是哪两项。"""
    reg = Registry.load()
    raises("evaluator: m + s 报错", EvalError, reg.eval_expr, "m + s")
    # 同量纲相加应当通过
    _n, ok = reg.eval_expr("m + m")
    check("evaluator: m + m 通过", ok.dim == Dim.of(L=1))


def test_evaluator_fractional_power_rejected() -> None:
    """分数幂必须给出清晰错误——这是数学事实，不是实现缺陷。"""
    reg = Registry.load()
    raises("evaluator: m^0.5 报错", EvalError, reg.eval_expr, "m^0.5")


# ============================================================ 公式库自检


def test_formula_homogeneity_all() -> None:
    """每条公式的签名都必须齐次。

    这是公式库的自动化校对：比人工检查可靠，且新增公式时自动生效。
    """
    reg = Registry.load()
    for fid, f in sorted(reg.formulas.items()):
        derived = _derive_rhs_dim(f)
        check(
            f"formula: {fid} 齐次",
            derived == f.lhs_dim,
            f"lhs={f.lhs_dim.format_si()} rhs={derived.format_si()}",
        )


def test_formula_derivation_specific() -> None:
    """逐条核对几个关键公式的推导结果，防止算法改动导致静默偏移。"""
    reg = Registry.load()

    # F = -dU/dx
    f = reg.formulas["force_from_potential_gradient"]
    check(
        "formula: F=-dU/dx 推导为力的量纲",
        _derive_rhs_dim(f) == Dim.of(M=1, L=1, T=-2),
    )

    # I = ∫∫∫ r²ρ dV —— 积分重数是 1（dV 已含 L³）
    f = reg.formulas["moment_of_inertia_integral"]
    check(
        "formula: 转动惯量推导为 M·L²",
        _derive_rhs_dim(f) == Dim.of(M=1, L=2),
        _derive_rhs_dim(f).format_si(),
    )

    # p = ∫F dt
    f = reg.formulas["impulse_momentum_theorem"]
    check(
        "formula: 冲量定理推导为动量量纲",
        _derive_rhs_dim(f) == Dim.of(M=1, L=1, T=-1),
    )


# ============================================================ 建树

def test_var_dim_resolution() -> None:
    """变量名解析必须按变量语义优先，而不是单位符号语义。

    这是踩过的坑：`s` 既是「秒」的单位符号，又是物理里的时间变量；
    `m` 既是「米」，又是质量。在 wrt（对谁求导）位置它一定是变量。
    若让单位表优先，p = ∫F dt 的积分变量会被解析成路程（长度），
    导致整条泛函分支静默消失。
    """
    reg = Registry.load()

    check("var_dim: t → 时间", reg.var_dim("t") == Dim.of(T=1))
    check("var_dim: s → 时间（不是长度！）", reg.var_dim("s") == Dim.of(T=1),
          str(reg.var_dim("s")))
    check("var_dim: x → 长度", reg.var_dim("x") == Dim.of(L=1))
    check("var_dim: m → 质量（不是长度！）", reg.var_dim("m") == Dim.of(M=1),
          str(reg.var_dim("m")))
    check("var_dim: T → 温度（不是磁感应强度！）", reg.var_dim("T") == Dim.of(TH=1),
          str(reg.var_dim("T")))
    check("var_dim: V → 体积（不是电压！）", reg.var_dim("V") == Dim.of(L=3),
          str(reg.var_dim("V")))
    check("var_dim: 未知变量返回 None", reg.var_dim("zzz") is None)


def test_tree_generated_branches() -> None:
    """泛函分支必须来自正确的公式。

    这组断言锁住了一段曾经反复出错的逻辑（改到第五版才收敛）：
      - 不能漏：F = -dU/dx 应出现在「力」下
      - 不能串：P = dW/dt 不应出现在「力」下（它的 from 是能量，
        而能量求时间导数是功率，与力无关）
    """
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)

    force = tb.lookup("牛", max_depth=1).entities[0]
    ids = {b.source.formula_id for b in force.generated}
    check("tree: 力应有 F=-dU/dx 分支", "force_from_potential_gradient" in ids,
          str(sorted(ids)))
    check("tree: 力不应有 P=dW/dt 分支", "power_from_work" not in ids,
          str(sorted(ids)))

    momentum = tb.lookup("动量", max_depth=1).entities[0]
    mids = {b.source.formula_id for b in momentum.generated}
    check("tree: 动量应有积分分支 p=∫F dt",
          "impulse_momentum_theorem" in mids, str(sorted(mids)))

    efield = tb.lookup("电场强度", max_depth=1).entities[0]
    eids = {b.source.formula_id for b in efield.generated}
    check("tree: 电场强度应有 E=-dU/dx 分支",
          "electric_field_from_potential" in eids, str(sorted(eids)))


def test_tree_equivalence_branches() -> None:
    """等价分支必须覆盖条目自身的 decompositions，且不重复。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)

    force = tb.lookup("牛", max_depth=1).entities[0]
    exprs = [b.expr for b in force.equivalent]
    check("tree: 力含 J/m", "J/m" in exprs, str(exprs))
    check("tree: 力含 Pa·m^2", "Pa·m^2" in exprs, str(exprs))
    check("tree: 等价分支无重复", len(exprs) == len(set(exprs)), str(exprs))


def test_tree_homogeneous_detection() -> None:
    """同量纲异构必须被检出——这是教学价值最高的功能。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)

    torque = tb.lookup("力矩", max_depth=1).entities[0]
    others = {h["id"] for h in torque.homogeneous}
    check("tree: 力矩的同量纲组含能量", "energy" in others, str(sorted(others)))
    check("tree: 力矩的同量纲组含弯矩", "bending_moment" in others,
          str(sorted(others)))


def test_tree_lookup_flexibility() -> None:
    """同一入口的多种写法都要命中：中文名、符号、别名、组合式。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)

    for q in ("牛", "牛顿", "N"):
        r = tb.lookup(q, max_depth=1)
        check(f"tree: {q!r} 能查到", bool(r.entities) and not r.error,
              r.error)

    # 组合表达式
    r = tb.lookup("N/C", max_depth=1)
    check("tree: 组合式 N/C 能查到", bool(r.entities), r.error)
    # N/C 与 V/m 必须落到同一量纲
    r2 = tb.lookup("V/m", max_depth=1)
    check(
        "tree: N/C 与 V/m 落到同一量纲",
        r.entities[0].dim == r2.entities[0].dim,
    )

    # 不存在的名称要给出错误而不是抛异常
    r3 = tb.lookup("弯沉xyz", max_depth=1)
    check("tree: 未知名称给出错误", bool(r3.error), "应报错但没有")


def test_tree_has_no_cycle() -> None:
    """建树不能递归失控。等价关系会成环（N→J/m→N），必须被断掉。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=3)
    for q in ("牛", "力矩", "能量", "电场强度", "转动惯量"):
        r = tb.lookup(q, max_depth=3)
        for e in r.entities:
            total = 0
            for b in e.equivalent + e.generated:
                total += sum(1 for _ in b.walk())
            check(f"tree: {q} 分支数量有界（{total}）", total < 500,
                  f"得到 {total}")


# ============================================================ 有界搜索

def test_search_finds_canonical_decompositions() -> None:
    """搜索必须能找到教科书里那些常见分解。"""
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())

    expectations = {
        Dim.of(M=1, L=1, T=-2): {"N", "J/m", "m^2·Pa"},          # 力
        Dim.of(M=1, L=2, T=-2): {"J", "W·s"},                     # 能量
        Dim.of(M=1, L=-1, T=-2): {"Pa", "N/m^2", "J/m^3"},        # 压强
        Dim.of(M=1, L=1, T=-3, I=-1): {"V/m", "N/C", "W/(A·m)"},  # 电场强度
        # 电阻：注意搜索输出用单位符号（Ω）而不是 id（Ohm）
        Dim.of(M=1, L=2, T=-3, I=-2): {"Ω", "V/A", "W/A^2"},
    }
    for target, wanted in expectations.items():
        got = {d.expr for d in s.decompose(target)}
        missing = wanted - got
        check(
            f"search: {target.format_si()} 找到 {sorted(wanted)}",
            not missing,
            f"缺少 {sorted(missing)}；实得 {sorted(got)[:10]}",
        )


def test_search_results_have_correct_dimension() -> None:
    """搜索结果的量纲必须等于目标——这是搜索正确性的根本。"""
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())

    for target in (
        Dim.of(M=1, L=1, T=-2),
        Dim.of(M=1, L=-1, T=-1),
        Dim.of(M=1, L=-3),
        Dim.of(M=1, L=2, T=-2, TH=-1),
    ):
        for d in s.decompose(target):
            if d.dim != target:
                check(
                    f"search: {d.expr} 量纲错误",
                    False,
                    f"{d.dim.format_si()} != {target.format_si()}",
                )
                break
        else:
            check(f"search: {target.format_si()} 全部结果量纲正确", True)


def test_search_excludes_noise_units() -> None:
    """噪声单位不得进入搜索结果。

    `bar`（mixed 制式）、`min`/`h`（accepted）、`g`（带词头的质量单位）
    都只能制造「纯数学拼接」的组合，教学生没有任何价值：
      - s·bar、Pa·min —— 单位制混搭
      - Pa·g/kg       —— 纯比例因子（1 g = 0.001 kg），量纲对但无意义
    """
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())

    gen_ids = {g.unit.id for g in s.generators}
    for banned in ("bar", "min", "h", "g", "rad", "sr"):
        check(f"search: 候选集不含 {banned}", banned not in gen_ids,
              f"实得 {sorted(gen_ids)}")

    # 压力维度下不应出现 bar
    pressure = {d.expr for d in s.decompose(Dim.of(M=1, L=-1, T=-2))}
    check("search: 压强结果不含 bar", "bar" not in pressure, str(sorted(pressure)))

    # 黏度维度下不应出现 min/h/bar
    visc = {d.expr for d in s.decompose(Dim.of(M=1, L=-1, T=-1))}
    bad = [e for e in visc if any(u in e for u in ("bar", "min", " h", "·h"))]
    check("search: 黏度结果不含 bar/min/h", not bad, f"命中 {bad}")


def test_search_bounds_are_respected() -> None:
    """剪枝边界必须真的生效，而不是写在文档里好看。"""
    reg = Registry.load()

    # 收紧到 1 个原子：只能得到「单个单位」的分解
    tight = DimensionSearch(reg, SearchBounds(max_atoms=1, max_results=10))
    res = tight.decompose(Dim.of(M=1, L=1, T=-2))
    for d in res:
        check(
            f"search: max_atoms=1 时 {d.expr} 只含 1 个原子",
            d.atoms == 1,
            f"实得 {d.atoms}",
        )
    exprs = {d.expr for d in res}
    check("search: max_atoms=1 时只得 N", exprs == {"N"}, str(sorted(exprs)))

    # max_results 必须被遵守
    small = DimensionSearch(reg, SearchBounds(max_results=3))
    check(
        "search: max_results=3 生效",
        len(small.decompose(Dim.of(M=1, L=2, T=-2))) <= 3,
    )

    # 非法参数要报错，而不是静默用默认值。
    # 校验发生在 DimensionSearch 构造时（它在建索引前先验参数），
    # 所以这里要对构造过程断言，而不是对 dataclass 本身。
    raises("search: max_atoms=0 报错", SearchError,
           DimensionSearch, reg, SearchBounds(max_atoms=0))
    raises("search: max_power=0 报错", SearchError,
           DimensionSearch, reg, SearchBounds(max_power=0))
    raises("search: max_unit_cost=0 报错", SearchError,
           DimensionSearch, reg, SearchBounds(max_unit_cost=0))


def test_search_is_bounded_and_fast() -> None:
    """有界搜索必须有可接受的时间上界。

    这条测试的意义不只是性能：它验证「有界」这个承诺是真的。
    节点预算一旦失效，某个罕见量纲就会让界面卡住。
    """
    import time
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())

    targets = [
        Dim.of(M=1, L=1, T=-2), Dim.of(M=1, L=2, T=-2),
        Dim.of(M=1, L=-1, T=-2), Dim.of(M=1, L=1, T=-3, I=-1),
        Dim.of(M=1, L=2, T=-3, I=-2), Dim.of(M=1, L=-3),
        Dim.of(M=1, L=2, T=-2, TH=-1), Dim.of(M=-1, L=-3, T=4, I=2),
    ]
    worst = 0.0
    for t in targets:
        t0 = time.perf_counter()
        s.decompose(t)
        dt = time.perf_counter() - t0
        worst = max(worst, dt)
        check(f"search: {t.format_si()} 单次 ≤ 2s", dt < 2.0, f"{dt:.2f}s")
    check("search: 最慢单次 ≤ 2s", worst < 2.0, f"{worst:.2f}s")


def test_search_deterministic() -> None:
    """同一目标两次搜索必须给出相同顺序的结果（可复现）。"""
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())
    a = [d.expr for d in s.decompose(Dim.of(M=1, L=1, T=-2))]
    b = [d.expr for d in s.decompose(Dim.of(M=1, L=1, T=-2))]
    check("search: 结果可复现", a == b, f"{a[:4]} vs {b[:4]}")


def test_search_dimensionless_target() -> None:
    """无因次目标不需要单位组合，应返回空列表而不是报错。"""
    reg = Registry.load()
    s = DimensionSearch(reg, SearchBounds())
    check("search: 无因次目标返回空", s.decompose(Dim({})) == [])


def test_tree_search_branches_are_labeled() -> None:
    """搜索结果必须以 kind=search 标注，且不得来自公式。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)
    view = tb.lookup("牛", max_depth=1).entities[0]
    tb.with_search(view)

    check("tree: with_search 后 search_ran=True", view.search_ran)
    check("tree: 搜索分支非空", len(view.searched) > 0,
          f"实得 {len(view.searched)}")

    for b in view.searched:
        check(f"tree: {b.expr} kind=search", b.kind == "search", b.kind)
        check(f"tree: {b.expr} 不来自公式", not b.source.is_from_formula)
        check(f"tree: {b.expr} 带算法枚举说明", "算法枚举" in b.source.note,
              b.source.note)

    # 不得与等价分支重复
    eq = {b.expr for b in view.equivalent}
    dup = eq & {b.expr for b in view.searched}
    check("tree: 搜索分支不与等价分支重复", not dup, f"重复 {sorted(dup)}")


def test_tree_search_not_run_by_default() -> None:
    """默认查询不得跑搜索——它有成本，且设计上不属于主体内容。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1)
    view = tb.lookup("牛", max_depth=1).entities[0]
    check("tree: 默认不跑搜索", not view.search_ran)
    check("tree: 默认搜索分支为空", view.searched == [])


# ============================================================ 符号表

def test_symbol_glossary_two_hop() -> None:
    """符号解析的两跳都必须工作：例外表命中，或 (符号,量纲) 唯一匹配。"""
    reg = Registry.load()

    # 第一跳：例外表里有显式定义
    e = reg.resolve_symbol("E_k", Dim.of(M=1, L=2, T=-2))
    check("符号表: E_k 走例外表", e.resolved_by == "glossary", e.resolved_by)
    check("符号表: E_k 中文名为动能", e.zh == "动能", e.zh)
    check("符号表: E_k 有物理意义", bool(e.meaning))

    # 第二跳：靠 (符号, 量纲) 匹配物理量。
    # 注意 m 现在也进了符号表例外表（为了给出「质量（力学）」这个更精确
    # 的说明），所以它走的是第一跳。这里改用一个只在物理量表里的符号。
    e2 = reg.resolve_symbol("s", Dim.of(L=1))
    check("符号表: s 走自动匹配或例外表", e2.resolved_by in ("lookup", "glossary"),
          e2.resolved_by)
    check("符号表: s 中文名为位移", "位移" in e2.zh, e2.zh)
    check("符号表: s 可跳转到 length", e2.jump == "length", e2.jump)

    v = reg.resolve_symbol("v", Dim.of(L=1, T=-1))
    check("符号表: v 可跳转到 velocity", v.jump == "velocity", v.jump)

    # m 仍然必须解析成质量（无论走哪一跳）
    m = reg.resolve_symbol("m", Dim.of(M=1))
    check("符号表: m 中文名为质量", m.zh == "质量", m.zh)
    check("符号表: m 可跳转到 mass", m.jump == "mass", m.jump)


def test_symbol_glossary_no_mixup() -> None:
    """同名不同义必须按量纲分开——这是符号表最容易出的错。

    U 既是势能（力学）又是电势（电磁学）；L 既是角动量又是电感；
    p 既是动量又是压强。若只用符号做键，必然张冠李戴。
    """
    reg = Registry.load()

    u_energy = reg.resolve_symbol("U", Dim.of(M=1, L=2, T=-2))
    u_potential = reg.resolve_symbol("U", Dim.of(M=1, L=2, T=-3, I=-1))
    check("符号表: U(能量) 是势能", u_energy.zh == "势能", u_energy.zh)
    check("符号表: U(电势) 是电势", u_potential.zh == "电势", u_potential.zh)
    check("符号表: 两个 U 含义不同", u_energy.meaning != u_potential.meaning)

    l_ang = reg.resolve_symbol("L", Dim.of(M=1, L=2, T=-1))
    l_ind = reg.resolve_symbol("L", Dim.of(M=1, L=2, T=-2, I=-2))
    check("符号表: L(角动量)", l_ang.zh == "角动量", l_ang.zh)
    check("符号表: L(电感)", l_ind.zh == "电感", l_ind.zh)

    p_mom = reg.resolve_symbol("p", Dim.of(M=1, L=1, T=-1))
    # 压强现在用显式符号 p_press，所以裸 `p` 只指动量。
    # 这是修「p 同时被当成压强」时的结果：让歧义在符号层面就分开。
    p_press = reg.resolve_symbol("p_press", Dim.of(M=1, L=-1, T=-2))
    check("符号表: p(动量)", p_mom.zh == "动量", p_mom.zh)
    check("符号表: p_press(压强)", "压强" in p_press.zh, p_press.zh)


def test_symbol_glossary_all_formulas_resolved() -> None:
    """每条公式的每个符号都应能解析出含义（不允许「未登记」）。"""
    reg = Registry.load()
    missing: list[str] = []
    for f in reg.formulas.values():
        for e in reg.formula_glossary(f):
            if e.resolved_by == "missing":
                missing.append(f"{f.id}:{e.symbol}")
    check("符号表: 没有未登记的符号", not missing, str(missing[:10]))


def test_symbol_jump_targets() -> None:
    """跳转目标必须指向该量纲的「主角」，而不是字母序第一。

    这里修过一个真实缺陷：同量纲组按 id 字母序取第一个，导致
    `E_k`（动能）跳到「弯矩」——因为 bending_moment 排在 energy 前面。
    正确做法是用公式库结构算出的中心度排序。

    注意 `E_k` 现在跳「动能」而不是笼统的「能量」——因为动能已有自己的
    条目，而这是更精确的结果（公式里 E_k 本来就是动能）。
    """
    reg = Registry.load()

    cases = [
        ("kinetic_energy", "E_k", "kinetic_energy"),
        ("gravitational_potential_energy", "E_p", "potential_energy"),
        ("spring_potential_energy", "E_s", "elastic_potential_energy"),
        ("power_from_work", "W", "work"),
        ("work_from_force", "W_work", "work"),
        ("electric_potential_definition", "U", "electric_potential"),        ("moment_of_inertia_integral", "I_rot", "moment_of_inertia"),
        ("newton_second_law", "m", "mass"),
        ("power_from_work", "t", "time"),
        ("resistance_definition", "R", "resistance"),
        ("inductance_self", "L_ind", "inductance"),
    ]
    for fid, sym, want in cases:
        got = ""
        for e in reg.formula_glossary(reg.formulas[fid]):
            if e.symbol == sym:
                got = e.jump
        check(f"跳转: {fid}:{sym} → {want}", got == want, f"得到 {got!r}")

    # `U` 在力学里既可指势能也可指功，两者量纲同为 M·L²·T⁻²、
    # 也都收 U 作别名——这是真实歧义，跳其中任何一个都不算错。
    # 这里只要求它落在能量量纲的条目里，不强行指定唯一答案。
    u_hits = {"potential_energy", "work", "energy", "torque", "bending_moment"}
    for fid in ("force_from_potential_gradient",):
        for e in reg.formula_glossary(reg.formulas[fid]):
            if e.symbol == "U":
                check(
                    f"跳转: {fid}:U 落在能量量纲条目内",
                    e.jump in u_hits,
                    f"得到 {e.jump!r}",
                )


def test_quantity_aliases_resolve() -> None:
    """公式里的同义符号必须能通过别名找到物理量。

    能量在公式里写作 E_k / E_p / E_s / W；力矩写作 M / M_torque。
    不认别名会让中心度打分全部为 0，进而退化成字母序。

    注意 E_k 现在指向 kinetic_energy 而不是笼统的 energy —— 因为
    动能/势能/弹性势能已有各自条目，这是修「没有势能动能页面」后的结果。
    """
    reg = Registry.load()
    for sym, dim, want in (
        ("E_k", Dim.of(M=1, L=2, T=-2), "kinetic_energy"),
        ("E_p", Dim.of(M=1, L=2, T=-2), "potential_energy"),
        ("E_s", Dim.of(M=1, L=2, T=-2), "elastic_potential_energy"),
        ("W", Dim.of(M=1, L=2, T=-2), "work"),
        ("M", Dim.of(M=1, L=2, T=-2), "torque"),
        ("p", Dim.of(M=1, L=1, T=-1), "momentum"),
        ("F", Dim.of(M=1, L=1, T=-2), "force"),
        ("I", Dim.of(M=1, L=2), "moment_of_inertia"),
    ):
        got = reg._quantity_matching(sym, dim)  # noqa: SLF001
        check(f"别名: {sym} → {want}", got == want, f"得到 {got!r}")


def test_canonicality_ranks_main_quantity_first() -> None:
    """同量纲组里，主角（更多公式的结果量）必须排前面。

    能量量纲现在有很多成员（动能/势能/功/力矩/各种势能），
    「功」与「力矩」因作为公式 lhs 最多而排前 —— 这就是中心度该有的效果。

    弯矩曾经得分为 0（它在公式库里只作为 rhs 的提示出现），
    补上 `M_bending = F·l` 这条定义式之后它有了作为 lhs 的分数。
    这里改成断言**相对次序**而不是具体分值：分值会随数据增补而变，
    相对次序才是这条测试真正要守的性质。
    """
    reg = Registry.load()
    energy_dim = Dim.of(M=1, L=2, T=-2)
    order = [q.id for q in reg.quantities_with_dim(energy_dim)]
    check("中心度: 功排在弯矩前",
          order.index("work") < order.index("bending_moment"), str(order))
    check("中心度: 弯矩得分低于功",
          reg.canonicality.get("bending_moment", 0)
          < reg.canonicality.get("work", 0),
          f"弯矩 {reg.canonicality.get('bending_moment')} vs "
          f"功 {reg.canonicality.get('work')}")
    check("中心度: 同量纲组含动能与势能",
          {"kinetic_energy", "potential_energy"} <= set(order), str(order))


def test_symbol_unit_forms_are_units_not_dims() -> None:
    """符号表的单位列必须是**单位**，不能是量纲式。

    这里也修过缺陷：`a`（加速度）曾显示成 `L·T^-2`（量纲），
    应当显示 `m/s^2`（单位）。质量的基准单位也曾错成 `g`（克）——
    只因为 `g` 的符号比 `kg` 短就被选中了。
    """
    reg = Registry.load()
    for symbol, dim, want in (
        ("m", Dim.of(M=1), "kg"),          # 质量基准是 kg，不是 g
        ("t", Dim.of(T=1), "s"),
        ("x", Dim.of(L=1), "m"),
        ("a", Dim.of(L=1, T=-2), "m/s^2"),
        ("v", Dim.of(L=1, T=-1), "m/s"),
        ("rho", Dim.of(M=1, L=-3), "kg/m^3"),
        ("F", Dim.of(M=1, L=1, T=-2), "N"),
        ("E_k", Dim.of(M=1, L=2, T=-2), "J"),
    ):
        e = reg.resolve_symbol(symbol, dim)
        check(f"单位列: {symbol} 含 {want}", want in e.unit_sym,
              f"得到 {e.unit_sym!r}")

    # 无因次量（角位移 θ）不该显示任何单位
    theta = reg.resolve_symbol("theta", Dim.of())
    check("单位列: theta 无单位显示", theta.unit_sym == "", repr(theta.unit_sym))


def test_symbol_glossary_selfcheck_catches_error() -> None:
    """反向验证：故意写错符号表的量纲，自检必须抓到。"""
    reg = Registry.load()
    from engine.registry import SymbolEntry

    # 造一个量纲错误的条目：把 E_k 的量纲写成力
    bad = SymbolEntry(
        symbol="E_k", zh="动能", meaning="故意写错",
        unit_sym="", unit_zh="", dim=Dim.of(M=1, L=1, T=-2),
    )
    reg.symbol_glossary[("E_k", Dim.of(M=1, L=1, T=-2))] = bad
    problems = reg.selfcheck()
    hit = [p for p in problems if "E_k" in p]
    check("符号表: 自检能抓到量纲不符", bool(hit), str(problems[:5]))

    # 再造一个「符号根本不存在于任何公式」的条目
    reg2 = Registry.load()
    reg2.symbol_glossary[("zzz_nope", Dim.of(L=1))] = SymbolEntry(
        symbol="zzz_nope", zh="不存在", meaning="不存在",
        unit_sym="", unit_zh="", dim=Dim.of(L=1),
    )
    probs2 = reg2.selfcheck()
    hit2 = [p for p in probs2 if "zzz_nope" in p]
    check("符号表: 自检能抓到多余符号", bool(hit2), str(probs2[:5]))


# ============================================================ 公式补录

def _temp_store():
    """测试用临时提交存储，绝不碰工作区里的真实 submissions.json。"""
    import tempfile
    from engine.submissions import SubmissionStore
    path = Path(tempfile.mkdtemp()) / "subs.json"
    st = SubmissionStore(path)
    st.load()
    return st


def test_submission_create_and_validate() -> None:
    st = _temp_store()
    s = st.create(
        query="牛", target_dim="M·L·T⁻²", expr="m^2·Pa",
        zh="平方米·帕", note="梁的弯曲刚度相关", source="《材料力学》第 5 章",
    )
    check("提交: 创建成功", s.id.startswith("sub-"), s.id)
    check("提交: 初始状态为 pending", s.status == "pending", s.status)
    check("提交: 已落盘", st.path.is_file())

    # 既无说明也无出处 → 无法核实，必须拒绝
    raises("提交: 缺说明与出处被拒", Exception, st.create,
           query="牛", target_dim="M·L·T⁻²", expr="x")
    # 空表达式
    raises("提交: 空表达式被拒", Exception, st.create,
           query="牛", target_dim="M·L·T⁻²", expr="", note="有说明")
    # 超长字段
    raises("提交: 超长说明被拒", Exception, st.create,
           query="牛", target_dim="M·L·T⁻²", expr="y", note="字" * 3000)


def test_submission_state_machine() -> None:
    """状态机必须约束转换，且存疑不删除用户原文。"""
    from engine.submissions import SubmissionError

    st = _temp_store()
    s = st.create(query="牛", target_dim="M·L·T⁻²", expr="A·m·T",
                  note="在某教材见过", source="某教材")

    got = st.promote(s.id, reason="找到出处", urls=["https://example.org"])
    check("状态机: promote 后为 verified", got.status == "verified", got.status)
    check("状态机: verify 记录了理由", bool(got.verify and got.verify["reason"]))
    check("状态机: verify 记录了链接",
          got.verify and got.verify["urls"] == ["https://example.org"])
    check("状态机: history 有记录", len(got.history) == 1, str(got.history))

    # 非法转换：verified 不能回到 pending
    raises("状态机: 非法回退被拦", SubmissionError, st._transition,
           s.id, "pending", "", [], "test")

    # 翻案：verified → disputed，用户原文必须保留
    original_note = got.note
    got2 = st.dispute(s.id, reason="再核对发现记错章节")
    check("状态机: 可翻案为 disputed", got2.status == "disputed", got2.status)
    check("状态机: 存疑后保留用户原文", got2.note == original_note, got2.note)
    check("状态机: 翻案后 history 累积", len(got2.history) == 2, str(got2.history))

    # 未知 id
    raises("状态机: 未知 id 报错", SubmissionError, st.promote, "nope")


def test_submission_expr_matching() -> None:
    """不同写法必须能互相匹配——这里修过一个真实 bug。"""
    from engine.submissions import _norm_variants

    st = _temp_store()
    st.create(query="牛", target_dim="M·L·T⁻²", expr="m^2·Pa",
              note="出处 A", source="A")

    # 这是修复前查不到的写法（用户习惯写 m2*Pa）
    for probe in ("m^2·Pa", "m2*Pa", "m^2 Pa", "m²Pa".replace("²", "2")):
        hits = st.for_expr(probe, "M·L·T⁻²")
        check(f"匹配: {probe!r} 能查到", len(hits) == 1, f"{len(hits)} 条")

    # 量纲不同则不匹配，避免同名表达式串味
    check("匹配: 量纲不符不匹配", st.for_expr("m^2·Pa", "L") == [])

    # 变体集合至少包含宽松形式（保证任何输入都能算出键）
    v = _norm_variants("随便什么 xyz")
    check("匹配: 任意输入都有变体", bool(v), str(v))


def test_submission_verified_only_in_tree() -> None:
    """只有 verified 进 submitted 分支；pending/disputed 进 disputed。"""
    st = _temp_store()
    a = st.create(query="牛", target_dim="M·L·T⁻²", expr="A·m·T",
                  note="出处甲", source="甲")
    b = st.create(query="牛", target_dim="M·L·T⁻²", expr="C·V/m",
                  note="出处乙", source="乙")
    st.promote(a.id, reason="核实通过")
    st.dispute(b.id, reason="没找到")

    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=1, submissions=st)
    view = tb.lookup("牛", max_depth=1).entities[0]

    sub_exprs = {x.expr for x in view.submitted}
    dis_exprs = {x.expr for x in view.disputed}
    check("建树: verified 进 submitted", "A·m·T" in sub_exprs, str(sub_exprs))
    check("建树: disputed 进 disputed", "C·V/m" in dis_exprs, str(dis_exprs))

    for br in view.submitted:
        check(f"建树: {br.expr} kind=submitted", br.kind == "submitted", br.kind)
        check(f"建树: {br.expr} 带提交 id", bool(br.source.submitted_id))
    for br in view.disputed:
        check(f"建树: {br.expr} 标注存疑", br.kind == "disputed", br.kind)

    # 未传 submissions 时不应有补录分支
    tb2 = TreeBuilder(reg, max_depth=1)
    v2 = tb2.lookup("牛", max_depth=1).entities[0]
    check("建树: 无提交存储时补录为空",
          v2.submitted == [] and v2.disputed == [])


# ============================================================ 领域分类

def test_domains_all_quantities_classified() -> None:
    """每个物理量都必须归入一个存在的领域。

    这是首页「不搜索也能到达每个词条」的前提：漏分类的条目会从
    首页消失，用户只能靠搜索碰巧找到它。
    """
    reg = Registry.load()
    check("领域: 领域表非空", len(reg.domains) >= 5, str(len(reg.domains)))
    unclassified = [q.id for q in reg.quantities.values() if not q.domain]
    check("领域: 没有未分类的物理量", not unclassified, str(unclassified[:8]))
    bad = [
        q.id for q in reg.quantities.values()
        if q.domain and q.domain not in reg.domains
    ]
    check("领域: 所有 domain 都存在于领域表", not bad, str(bad[:8]))


def test_domains_have_chinese_names() -> None:
    """领域中文名必须回填到物理量上（条目数据里不写，由注册表填入）。"""
    reg = Registry.load()
    for q in list(reg.quantities.values())[:10]:
        if q.domain:
            check(f"领域: {q.id} 有领域中文名", bool(q.domain_zh), q.domain_zh)


def test_domains_browse_completeness() -> None:
    """按领域浏览必须覆盖**全部**物理量——这是核心要求。

    用户明确要求「不搜索也能到达每一个词条」。所以这里断言
    各领域条目数之和等于物理量总数，一个都不能漏。
    """
    reg = Registry.load()
    total = sum(len(reg.quantities_in_domain(d.id)) for d in reg.domains_ordered())
    check(
        "领域: 浏览覆盖全部物理量",
        total == len(reg.quantities),
        f"领域内共 {total} 条，物理量总数 {len(reg.quantities)}",
    )


def test_domains_ordering_stable() -> None:
    """领域顺序必须确定（由 order 决定），且 order 不重复。"""
    reg = Registry.load()
    ordered = reg.domains_ordered()
    orders = [d.order for d in ordered]
    check("领域: order 无重复", len(orders) == len(set(orders)), str(orders))
    check("领域: 已排序", orders == sorted(orders), str(orders))


def test_domains_cover_sound_and_light() -> None:
    """声学与光学必须有条目——它们此前一条都没有。

    用户要求首页能按「力/热/声/光/电」分类，缺了领域就无从浏览。
    """
    reg = Registry.load()
    for did, label in (("acoustics", "声学"), ("optics", "光学"),
                       ("thermo", "热学"), ("electromagnetism", "电磁学"),
                       ("mechanics", "力学"), ("fluids", "流体力学"),
                       ("materials", "材料力学")):
        qs = reg.quantities_in_domain(did)
        check(f"领域: {label} 有条目", len(qs) > 0, f"{len(qs)} 条")
        if qs:
            check(f"领域: {label} 中文名正确", qs[0].domain_zh == label,
                  qs[0].domain_zh)


def test_domains_selfcheck_catches_unclassified() -> None:
    """反向验证：漏分类与非法领域都必须被自检拦下。"""
    from dataclasses import replace

    reg = Registry.load()
    q = reg.quantities["force"]
    reg.quantities["force"] = replace(q, domain="")
    problems = reg.selfcheck()
    hit = [p for p in problems if "force" in p and "domain" in p]
    check("领域: 自检能抓到漏分类", bool(hit), str(problems[:6]))

    reg2 = Registry.load()
    q2 = reg2.quantities["force"]
    reg2.quantities["force"] = replace(q2, domain="nonexistent_domain")
    probs2 = reg2.selfcheck()
    hit2 = [p for p in probs2 if "nonexistent_domain" in p]
    check("领域: 自检能抓到非法领域", bool(hit2), str(probs2[:6]))


# ============================================================ 数据自检


def test_registry_selfcheck_passes() -> None:
    """当前数据必须全部通过自检。"""
    reg = Registry.load()
    problems = reg.selfcheck()
    check(
        "registry: 自检全绿",
        not problems,
        "\n      " + "\n      ".join(problems),
    )


def test_selfcheck_catches_bad_dim() -> None:
    """反向验证：故意注入错误 dim，自检必须抓到。

    这条测试比「自检通过」更重要——它证明了自检本身有效，
    而不是碰巧没有坏数据。
    """
    reg = Registry.load()
    from engine.registry import Quantity

    bad = Quantity(
        id="__injected_bad__",
        zh="注入的坏条目",
        symbol="__bad__",
        # 故意与 unit_forms 不符：声明为力 M·L·T⁻²，但表达式 kg·m^2/s^2
        # 实际是能量 M·L²·T⁻²。自检必须抓到这一处不一致。
        dim=Dim.of(M=1, L=1, T=-2),
        category="测试",
        structure="scalar",
        unit_forms=("kg·m^2/s^2",),
        decompositions=(),
        aliases=(),
        homogeneous_with=(),
    )
    reg.quantities[bad.id] = bad
    reg._index()

    problems = reg.selfcheck()
    hit = [p for p in problems if "__injected_bad__" in p]
    check(
        "registry: 自检能抓到错误 dim",
        bool(hit),
        f"未抓到；全部问题：{problems}",
    )


def test_selfcheck_catches_inhomogeneous_formula() -> None:
    """反向验证：故意注入不齐次的公式，自检必须抓到。"""
    reg = Registry.load()
    from engine.registry import Formula

    bad = Formula(
        id="__injected_formula__",
        tex="F = m·v",
        zh="故意写错的公式",
        category="测试",
        pattern="product",
        ops=("mul",),
        lhs_symbol="F",
        lhs_dim=Dim.of(M=1, L=1, T=-2),
        lhs_structure="vector",
        rhs_entries=(
            {"symbol": "m", "dim": {"M": 1}},
            {"symbol": "v", "dim": {"L": 1, "T": -1}},
        ),
        relations=(),
    )
    reg.formulas[bad.id] = bad

    problems = reg.selfcheck()
    hit = [p for p in problems if "__injected_formula__" in p]
    check(
        "registry: 自检能抓到不齐次公式",
        bool(hit),
        f"未抓到；全部问题：{problems}",
    )


def test_registry_lookup() -> None:
    reg = Registry.load()

    check("registry: 按中文名找单位", reg.find_unit("牛") is not None)
    check("registry: 按符号找单位", reg.find_unit("N") is not None)
    check("registry: 按别名找单位", reg.find_unit("牛顿") is not None)
    check("registry: 找不到返回 None", reg.find_unit("不存在的东西") is None)

    # 同量纲异构：力矩 / 能量 / 功 / 弯矩
    same = reg.quantities_with_dim(Dim.of(M=1, L=2, T=-2))
    ids = {q.id for q in same}
    check(
        "registry: 同量纲组含力矩与能量",
        {"torque", "energy", "work", "bending_moment"} <= ids,
        f"得到 {sorted(ids)}",
    )

    # 角速度与频率同量纲（弧度无因次的直接后果）
    same2 = {q.id for q in reg.quantities_with_dim(Dim.of(T=-1))}
    check(
        "registry: 角速度与频率同量纲",
        {"angular_velocity", "frequency"} <= same2,
        f"得到 {sorted(same2)}",
    )


# ============================================================ 第四轮：规范形
#
# 用户报告「算法枚举时会重复枚举，如 N·m 和 m·N」。根因是去重键用的是
# parse→render，它只归一语法、不归一交换律。下面这组用例锁住规范形。


def test_canonical_form_normalizes_commutativity() -> None:
    same = [
        ("N·m", "m·N"),
        ("Pa·m^3", "m^3·Pa"),
        ("W·s", "s·W"),
        ("N/m^2", "N·m^-2"),
        ("kg·m/s^2", "kg·m·s^-2"),
        ("J/(m·Hz)", "J·Hz^-1·m^-1"),
        ("1/s", "s^-1"),
        ("m·m", "m^2"),
        ("A·C·Ω", "C·Ω·A"),
        ("m^2·Pa·s", "Pa·s·m^2"),
    ]
    for a, b in same:
        ka, kb = P.canonical_expr(a), P.canonical_expr(b)
        check(f"规范形: {a} ≡ {b}", ka == kb, f"{ka!r} != {kb!r}")

    diff = [("N·m", "N/m"), ("kg·m/s^2", "kg·m/s"), ("J", "W"),
            ("m^2", "m^3"), ("Pa", "N")]
    for a, b in diff:
        check(f"规范形: {a} ≢ {b}",
              P.canonical_expr(a) != P.canonical_expr(b))

    # 解析失败不能抛异常——去重键必须总能算出来
    check("规范形: 非法表达式的兜底",
          P.canonical_expr("F = -dU/dx") != "")


def test_enumeration_skips_canonically_equal_forms() -> None:
    """算法枚举出来的组合，不能与已有写法只是「乘法顺序不同」。"""
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=2)
    checked = 0
    for qid in ("energy", "torque", "force", "pressure", "momentum", "velocity"):
        q = reg.quantities[qid]
        view = tb._view_for_quantity(q, 2)  # noqa: SLF001
        tb.with_search(view)
        shown = {P.canonical_expr(b.expr)
                 for b in list(view.equivalent) + list(view.generated)}
        clash = [b.expr for b in view.searched
                 if P.canonical_expr(b.expr) in shown]
        check(f"枚举去重: {qid} 无重复写法", not clash, f"重复 {clash}")
        # 枚举结果自身也不能有规范形重复
        keys = [P.canonical_expr(b.expr) for b in view.searched]
        check(f"枚举去重: {qid} 内部无重复", len(keys) == len(set(keys)),
              f"{len(keys)} 条里有 {len(keys) - len(set(keys))} 条重复")
        checked += 1
    check("枚举去重: 抽查了足够多的条目", checked >= 5)


# ============================================================ 第四轮：求导/积分成对
#
# 用户报告「没有由积分生成的量纲，按理该与求导成对出现」。
# 数据里 derivative 关系 9 条、integral 只有 3 条，看起来不对称；
# 但求导与积分互为逆运算，每条 derivative 都同时断言一条 integral。


def test_derivative_and_integral_edges_come_in_pairs() -> None:
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=2)

    # 1) 对每条 derivative 关系，它**源的量纲**页面上必须有一条积分边。
    #    这条积分边可能来自数据里显式声明的 integral 关系（例如冲量定理
    #    已经写了 p = ∫F dt），也可能是掉头读出来的。两者都算成对——
    #    要求「必须带 inverse 标记」是错的：显式声明的那条更权威，
    #    按语义键去重时它先被处理，反读那条自然被合并掉。
    n_pairs = 0
    for f in reg.formulas.values():
        for rel in f.relations:
            if rel.kind != "derivative":
                continue
            src_dim = tb._eval_dim(rel.from_)  # noqa: SLF001
            if src_dim is None:
                continue
            qs = reg.quantities_with_dim(src_dim)
            if not qs:
                continue
            view = tb._view_for_quantity(qs[0], 2)  # noqa: SLF001
            inv = [b for b in view.generated if b.kind == "integral"]
            check(
                f"成对: {f.id} 的求导边在源量纲上有积分边",
                bool(inv),
                f"量纲 {src_dim.format_si()}（{qs[0].zh}）的生成分支里没有积分",
            )
            if inv:
                n_pairs += 1
    check("成对: 至少凑成了若干对", n_pairs >= 5, f"只凑成 {n_pairs} 对")

    # 2) 反读出来的边必须带标记，界面才知道它不是另一条公式
    energy = reg.find_quantity("能量")
    view = tb._view_for_quantity(energy, 2)  # noqa: SLF001
    integrals = [b for b in view.generated if b.kind == "integral"]
    check("成对: 能量页面有积分生成分支", bool(integrals))
    check("成对: 能量页面的积分边都来自反读",
          all(b.source.inverse for b in integrals),
          str([(b.expr, b.source.inverse) for b in integrals]))
    check("成对: 反读的积分边保留了原方向表达式",
          all(b.source.inverse_of for b in integrals))

    # 3) 正向声明的边不该被标成反读
    forward = [b for b in view.generated if b.kind == "derivative"]
    check("成对: 正向求导边不带 inverse 标记",
          all(not b.source.inverse for b in forward))

    # 4) 全库对称性：两种 kind 的边总数应当接近（积分不该远少于求导）
    kinds = {"derivative": 0, "integral": 0}
    for q in reg.quantities.values():
        v = tb._view_for_quantity(q, 2)  # noqa: SLF001
        for b in v.generated:
            if b.kind in kinds:
                kinds[b.kind] += 1
    check("成对: 积分边数量不再远少于求导边",
          kinds["integral"] >= kinds["derivative"] * 0.8,
          f"求导 {kinds['derivative']} vs 积分 {kinds['integral']}")


# ============================================================ 第四轮：联想匹配


def test_matcher_handles_longer_query() -> None:
    """用户要求的方向：输入比候选长也要能匹配（abc → ab）。"""
    from engine.matcher import match_kind
    check("匹配: abc 命中 ab（候选更短）",
          match_kind("abc", "ab") == "reverse",
          match_kind("abc", "ab"))
    check("匹配: ab 命中 abc（候选更长）",
          match_kind("ab", "abc") == "prefix")
    check("匹配: 完全相同",
          match_kind("牛", "牛") == "exact")
    check("匹配: 单字符候选不做反向命中（防噪声）",
          match_kind("abcd", "a") == "",
          match_kind("abcd", "a"))
    check("匹配: 错字兜底",
          match_kind("电场强渡", "电场强度") == "overlap",
          match_kind("电场强渡", "电场强度"))
    check("匹配: 非同类不给分",
          match_kind("xyz", "电场强度") == "")


def test_matcher_suggest_live_results() -> None:
    from engine.matcher import Matcher
    reg = Registry.load()
    m = Matcher(reg)

    def names(q, limit=6):
        return [it["zh"] for it in m.suggest(q, limit=limit)]

    check("联想: 中文名开头", "电场强度" in names("电场"))
    check("联想: 拼音全拼", "电场强度" in names("dianchang"),
          str(names("dianchang")))
    check("联想: 拼音首字母", "电场强度" in names("dcqd"), str(names("dcqd")))
    check("联想: 符号精确", names("N")[0] == "牛", str(names("N")))
    check("联想: kgm（比候选长）有结果", len(names("kgm")) > 0)
    check("联想: 空输入不返回结果", m.suggest("") == [])
    check("联想: 无意义输入不返回结果", m.suggest("qqqqzzz") == [],
          str(names("qqqqzzz")))
    # 同量纲最多 3 条，避免一屏全是同量纲异构
    hits = m.suggest("能", limit=30)
    dims: dict[str, int] = {}
    for it in hits:
        dims[it["dim_si"]] = dims.get(it["dim_si"], 0) + 1
    check("联想: 同一量纲最多 3 条", all(v <= 3 for v in dims.values()),
          str(dims))


# ============================================================ 第四轮：页面槽位


def test_page_slots_are_covered_or_explained() -> None:
    """页面规范的核心不变量：每个槽位要么有内容，要么有明确的「不适用」理由。

    「缺失」只允许出现在**没有补录入口就不合理**的位置，
    而定义式的缺失必须由 defined_as 解释清楚——不允许留一个说不清的空白。
    """
    reg = Registry.load()

    defs_by_qid: dict[str, int] = {}
    for f in reg.formulas.values():
        if f.defines:
            defs_by_qid[f.defines] = defs_by_qid.get(f.defines, 0) + 1

    unexplained: list[str] = []
    for qid, q in sorted(reg.quantities.items()):
        if defs_by_qid.get(qid):
            continue
        if q.defined_as not in ("si-base", "umbrella"):
            unexplained.append(qid)
        elif not q.definition_note:
            unexplained.append(qid + "（有 defined_as 但没有说明）")
    check("槽位: 无定义式的条目都有明确理由", not unexplained,
          "、".join(unexplained))

    # 结构必需项：这些槽位没有「不适用」这一说。
    # aliases 不在此列——别名是可选的（不是每个量都有别称），
    # 把它当硬约束会逼出一堆虚假别名，反而污染检索索引。
    structural = [
        ("symbol", lambda q: bool(q.symbol)),
        ("category", lambda q: bool(q.category)),
        ("domain", lambda q: bool(q.domain)),
        ("unit_forms", lambda q: bool(q.unit_forms)),
        ("decompositions", lambda q: bool(q.decompositions)),
        ("note", lambda q: bool(q.note)),
    ]
    for label, pred in structural:
        bad = [qid for qid, q in reg.quantities.items() if not pred(q)]
        check(f"槽位: {label} 全覆盖", not bad, "、".join(bad))

    # defined_as 的取值只能是三种之一
    bad_kind = [qid for qid, q in reg.quantities.items()
                if q.defined_as not in ("", "si-base", "umbrella")]
    check("槽位: defined_as 取值合法", not bad_kind, "、".join(bad_kind))

    # SI 七个基本量必须齐全（原先只有 5 个，搜「温度」直接查不到）。
    # 判据看**量纲**而不是 defined_as：电流既标了 si-base 又有定义式
    # i = dq/dt，所以它的 defined_as 是空的，但它的量纲 I 仍然覆盖到了。
    base_dims = [Dim.of(M=1), Dim.of(L=1), Dim.of(T=1), Dim.of(I=1),
                 Dim.of(TH=1), Dim.of(N=1), Dim.of(J=1)]
    missing_dims = [d.format_si() for d in base_dims
                    if not reg.quantities_with_dim(d)]
    check("槽位: SI 七个基本量的量纲都有条目", not missing_dims,
          "缺 " + "、".join(missing_dims))
    base = [q for q in reg.quantities.values() if q.defined_as == "si-base"]
    check("槽位: 显式标注为 SI 基本量的条目合理",
          5 <= len(base) <= 7, f"有 {len(base)} 个：{[q.zh for q in base]}")


def test_unit_form_details_have_chinese_reading() -> None:
    """常见写法必须逐条带上中文读法——这正是「有些有描述有些没有」的修法。

    读法由求值器沿 AST 生成，所以它对每一条都存在；数据里的 note 是
    可选的补充，不该是唯一的信息来源。
    """
    reg = Registry.load()
    tb = TreeBuilder(reg, max_depth=2)
    missing: list[str] = []
    for qid, q in sorted(reg.quantities.items()):
        details = tb._unit_form_details(q)  # noqa: SLF001
        if not details:
            missing.append(qid + "（无常见写法）")
            continue
        for d in details:
            if not d["zh"]:
                missing.append(f"{qid}:{d['expr']}")
    check("常见写法: 每条都有中文读法", not missing, "、".join(missing[:8]))


def test_divisor_exponent_affects_derivation() -> None:
    """分母的幂次必须生效。

    `E_v = I_v/r^2`（照度＝发光强度除以距离平方）里 r 是 divisor 且 exp=2，
    初版只累加 dim 不看 exp，会少减一阶长度、把照度算成 J/L。
    """
    reg = Registry.load()
    f = reg.formulas["illuminance_definition"]
    derived = _derive_rhs_dim(f)
    check("推导: 照度定义式的分母幂次生效",
          derived == Dim.of(L=-2, J=1), derived.format_si())
    check("推导: 与 lhs 一致", derived == f.lhs_dim)


def test_signature_symbols_are_single_valued() -> None:
    """一条签名里，同一个字母只能有一个量纲。

    这条规则是补出来的：洛伦兹力公式的 tex 写的是 `F = q·v·B`，
    `signature.lhs.symbol` 却写成了 `B`。**只比量纲的齐次性检查放行了它**——
    lhs 的量纲确实等于 rhs 推导值，但符号表里多出一行「B = 磁场力」
    而且没有跳转链接（同一符号在 lhs 是力的量纲、在 rhs 是磁感应强度）。
    真实错误，看界面完全看不出。
    """
    reg = Registry.load()
    check("符号: 自检没有报出符号歧义", not [
        p for p in reg.selfcheck() if "必须指同一个量" in p
    ])
    f = reg.formulas["magnetic_force_lorentz"]
    check("符号: 洛伦兹力的 lhs 是 F 而不是 B", f.lhs_symbol == "F",
          f.lhs_symbol)
    check("符号: 洛伦兹力的 rhs 里 B 是磁感应强度",
          any(str(e.get("symbol")) == "B" and e.get("dim") == {"M": 1, "T": -2, "I": -1}
              for e in f.rhs_entries))
    # 反例：人为造一条歧义签名，自检必须报出来
    from dataclasses import replace
    bad = replace(
        f,
        lhs_symbol="B",
        rhs_entries=tuple(f.rhs_entries) + (
            {"symbol": "B", "dim": {"M": 1, "L": 1, "T": -2}},),
    )
    problems = reg._check_signature_symbols(bad)  # noqa: SLF001
    check("符号: 歧义签名会被自检拦下", len(problems) == 1, str(problems))


# ============================================================ 第四轮：补录种类


def test_submission_kinds_fill_their_own_slots() -> None:
    """四种补录各填各的槽位，未核实的只展示不填槽。"""
    import tempfile
    from engine.submissions import SubmissionStore
    from engine.tree import TreeBuilder as TB

    reg = Registry.load()
    with tempfile.TemporaryDirectory() as td:
        store = SubmissionStore(Path(td) / "subs.json")
        store.load()

        d = store.create(query="力", target_dim="M·L·T⁻²", expr="F = m·a",
                         note="牛顿第二定律", kind="definition",
                         target_qid="force")
        u = store.create(query="力", target_dim="M·L·T⁻²", expr="kg·m/s^2",
                         note="惯用写法", kind="unit_form", target_qid="force")
        n = store.create(query="力", target_dim="M·L·T⁻²", expr="",
                         note="改变物体运动状态的原因", kind="note",
                         target_qid="force")
        fm = store.create(query="力", target_dim="M·L·T⁻²", expr="F = k·x",
                          note="胡克定律", kind="formula")

        tb = TB(reg, max_depth=2, submissions=store)
        v = tb._view_for_quantity(reg.quantities["force"], 2)  # noqa: SLF001

        # pending 一律不填槽，只进 disputed
        check("补录: pending 不进定义式槽",
              all(x.get("from_submission") != d.id for x in v.definitions))
        check("补录: pending 进存疑栏", len(v.disputed) == 4,
              f"得到 {len(v.disputed)} 条")

        store.promote(d.id, "教材定义")
        store.promote(u.id, "教材写法")
        store.promote(n.id, "通用描述")
        store.promote(fm.id, "胡克定律")
        # 说明类补录不填 note（数据里已有），只进已核实栏
        v2 = tb._view_for_quantity(reg.quantities["force"], 2)  # noqa: SLF001
        check("补录: 已核实的定义式进入定义式槽",
              any(x.get("from_submission") == d.id for x in v2.definitions),
              str([x.get("tex") for x in v2.definitions]))
        check("补录: 已核实的常见写法进入 unit_forms",
              "kg·m/s^2" in v2.unit_forms, str(v2.unit_forms))
        # 说明类补录**只能填空白**，不能覆盖数据里已有的描述——
        # 用户提交的描述没有经过与既有内容的一致性核对，
        # 直接盖掉会让页面内容在核实之后反而变差。
        check("补录: 已核实的说明不覆盖已有 note",
              v2.note == reg.quantities["force"].note,
              f"note 被改写：{v2.note[:60]!r}")
        check("补录: 已核实的 formula 进入 submitted",
              any(b.expr == "F = k·x" for b in v2.submitted),
              str([b.expr for b in v2.submitted]))
        check("补录: 核实后不再进存疑栏", len(v2.disputed) == 0,
              str([b.expr for b in v2.disputed]))

        # 种类与必填项的约束
        raises("补录: 未知种类被拒", Exception, store.create,
               query="x", target_dim="M", expr="m", note="n", kind="bogus")
        raises("补录: 定义式必须有目标物理量", Exception, store.create,
               query="x", target_dim="M", expr="m", note="n",
               kind="definition")
        raises("补录: 公式类必须有表达式", Exception, store.create,
               query="x", target_dim="M", expr="", note="n", kind="formula")


# ============================================================ runner


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        try:
            fn()
        except Exception:  # noqa: BLE001
            _FAILURES.append(f"{fn.__name__} 抛出未捕获异常：\n{traceback.format_exc()}")

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    print(f"通过 {_PASSED} 项断言，{len(tests)} 个测试函数")
    if _FAILURES:
        print(f"\n失败 {len(_FAILURES)} 项：")
        for f in _FAILURES:
            print(f"  ✗ {f}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
