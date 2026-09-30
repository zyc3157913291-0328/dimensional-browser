# -*- coding: utf-8 -*-
"""第四轮数据补齐（一次性脚本，运行后可删）。

做三件事：
  1. 给 37 个缺 note 的物理量补上一句话描述（页面「常见写法」下方的说明）
  2. 给 SI 基本量 / 总称量打 defined_as 标记——说明它们「没有定义式」是
     正确答案，而不是数据缺失
  3. 新增 21 条**定义式公式**，填掉绝大多数缺失的定义式槽位

写法约定：保持每个条目的原有键顺序，新键追加在后面，这样 diff 可读。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
Q_PATH = ROOT / "data" / "quantities.json"
F_PATH = ROOT / "data" / "formulas.json"
G_PATH = ROOT / "data" / "symbol_glossary.json"

# ------------------------------------------------------------------ 1) note

NOTES = {
    "acceleration": "速度对时间的变化率，a = dv/dt。量纲 L·T⁻²，也可写成 N/kg ——"
                    "引力场强与加速度同量纲不是巧合，而是等效原理的直接体现。",
    "angular_acceleration": "角速度对时间的变化率，量纲 T⁻²。工程上也写作 rad/s²，"
                            "其中 rad 是无因次标记，不改变量纲。",
    "angular_momentum": "位矢与动量的叉积，量纲 M·L²·T⁻¹。它对时间的变化率就是力矩 ——"
                        "角动量与力矩的关系，和动量与力的关系完全平行。",
    "angular_velocity": "角度对时间的变化率，量纲 T⁻¹。因为角度是无因次量，"
                        "它的量纲与频率相同，但物理意义完全不同：一个描述转动的快慢，"
                        "一个描述周期的倒数。",
    "area": "长度的平方，量纲 L²。它是二维的几何测度，与「长度」只差一个幂次。",
    "capacitance": "电容器储存电荷的能力，C = q/U。量纲 M⁻¹·L⁻²·T⁴·I²，"
                   "单位法拉 F。分子上有负质量量纲，这在 SI 导出单位里并不常见。",
    "chemical_potential_energy": "化学反应中每摩尔物质所携带的能量。它的量纲与能量完全相同，"
                                 "是「同量纲异构」在热学里的又一例。",
    "current_density": "单位截面积上通过的电流，j = i/A。量纲 L⁻²·I，单位 A/m²。",
    "density": "单位体积的质量，ρ = m/V。量纲 M·L⁻³，是流体力学里最常用的物性参数之一。",
    "dynamic_viscosity": "流体层间单位速度梯度所需的切应力，量纲 M·L⁻¹·T⁻¹，"
                         "惯用写法 Pa·s。它刻画流体的「黏稠程度」本身。",
    "elastic_modulus": "应力与应变之比。应变是纯数，所以它的量纲与应力完全相同"
                       "（M·L⁻¹·T⁻²）—— 这是「量纲相同、物理意义不同」的又一实例。",
    "electric_charge": "电流对时间的积分，q = ∫i dt。量纲 T·I，单位库仑 C。",
    "electric_current": "单位时间通过的电荷量，i = dq/dt。它是 SI 七个基本量之一，"
                        "量纲 I 由安培这一基本单位直接定义。",
    "electric_field_strength": "单位电荷所受的电场力，E = F/q。N/C 与 V/m 量纲相同，"
                               "后者在静电学里更常用 —— 两种写法能互相换算，"
                               "正是量纲分析给出的结论。",
    "electric_potential": "单位电荷所具有的电势能，U = W/q。量纲 M·L²·T⁻³·I⁻¹，单位伏特 V。",
    "force": "改变物体运动状态的原因，F = m·a。量纲 M·L·T⁻²，单位牛顿 N。"
             "它是力学里被引用最多的量之一。",
    "frequency": "单位时间内重复的次数，f = 1/T。量纲 T⁻¹，单位赫兹 Hz。"
                 "它与角速度同量纲但意义不同。",
    "gravitational_constant": "万有引力定律 F = G·m₁·m₂/r² 中的比例常量，量纲 M⁻¹·L³·T⁻²。"
                              "它是少数量纲里带负质量幂次的常量。",
    "impulse": "力对时间的积分，量纲 M·L·T⁻¹。它与动量量纲完全一致 ——"
               "冲量定理说的正是「冲量等于动量变化」这件事。",
    "inductance": "线圈产生感应电动势的能力，量纲 M·L²·T⁻²·I⁻²，单位亨利 H。"
                  "它与电阻、电容一起构成电路的三个基本参数。",
    "jerk": "加速度对时间的变化率，a 的二阶导数。量纲 L·T⁻³，"
            "日常语感里对应「猛推一下」的那种突兀感。",
    "kinematic_viscosity": "动力黏度与密度之比，ν = μ/ρ，量纲 L²·T⁻¹。"
                           "它与扩散系数量纲相同，因为两者的输运方程形式一致。",
    "length": "SI 七个基本量之一，量纲 L。长度的基准是米，由光速定义。",
    "magnetic_flux_density": "单位电荷以单位速度运动时受到的磁力，量纲 M·T⁻²·I⁻¹，"
                             "单位特斯拉 T。",
    "mass": "SI 七个基本量之一，量纲 M。注意 SI 的质量基准是千克 kg 而不是克 g ——"
            "它是唯一自带词头的基本单位。",
    "moment_of_inertia": "刚体转动惯性的量度，I = ∫∫∫ r²·ρ dV。它是张量（矩阵结构），"
                         "元素量纲为 M·L²。转动惯量把牛顿第二定律搬到转动情形。",
    "momentum": "质量与速度的乘积，p = m·v。量纲 M·L·T⁻¹。它对时间的变化率就是力，"
                "这条关系是牛顿第二定律的原始形式。",
    "power": "单位时间内完成的功，P = W/t。量纲 M·L²·T⁻³，单位瓦特 W。"
             "它与辐射通量、声功率同量纲。",
    "pressure": "单位面积上受到的正压力，p = F/A。量纲 M·L⁻¹·T⁻²，单位帕斯卡 Pa。"
                "它与应力、弹性模量同量纲。",
    "resistance": "导体两端电压与电流之比，R = U/i。量纲 M·L²·T⁻³·I⁻²，单位欧姆 Ω。",
    "specific_heat_capacity": "单位质量物质升高单位温度所需的热量，量纲 L²·T⁻²·Θ⁻¹。"
                              "它把热学量与力学量通过温度这个基本量联系起来。",
    "spring_constant": "弹簧产生单位形变所需的力，k = F/x，量纲 M·T⁻²。"
                       "它与表面张力量纲相同 —— 两者都是「单位长度上的力」。",
    "stress": "单位面积上的内力，σ = F/A，量纲 M·L⁻¹·T⁻²。它与压强同量纲同形式，"
              "区别在于一个是构件内部的内力分布，一个是外部施加的压强。",
    "surface_tension": "液体表面单位长度上的收缩力，量纲 M·T⁻²。"
                       "它与弹簧刚度量纲相同，因为「单位长度上的力」与"
                       "「单位形变上的力」在量纲上是一回事。",
    "velocity": "位移对时间的变化率，v = dx/dt。量纲 L·T⁻¹。",
    "volume": "长度的三次方，量纲 L³。它是三维的几何测度。",
    "weight": "物体所受的重力，G = m·g。量纲 M·L·T⁻² —— 与力相同，因为重力本来就是力。"
              "质量与重力的区别正体现在这里：一个量纲是 M，一个是 M·L·T⁻²。",
    "work": "力与位移的标量积，W = F·s。量纲 M·L²·T⁻²，单位焦耳 J。"
            "注意它与力矩量纲相同、物理意义不同（一个是标量积，一个是叉积）。",
}

# ---------------------------------------------- 2) 没有定义式是正确答案的那些

DEFINED_AS = {
    "length": ("si-base", "长度是 SI 七个基本量之一，由米这一基本单位直接定义"
                          "（2019 年起以光速固定值定义），不是由别的量通过公式推导出来的。"),
    "mass": ("si-base", "质量是 SI 七个基本量之一，由千克这一基本单位直接定义"
                        "（2019 年起以普朗克常数固定值定义）。"),
    "time": ("si-base", "时间是 SI 七个基本量之一，由秒这一基本单位直接定义"
                        "（以铯-133 超精细跃迁频率固定值定义）。"),
    "luminous_intensity": ("si-base", "发光强度是 SI 七个基本量之一，由坎德拉这一基本单位定义。"
                                      "它带有主观视觉权重，是唯一与人眼响应相关的基本量。"),
    "energy": ("umbrella", "「能量」是一个总称量纲，不是一个具体的物理量："
                           "动能、势能、功、力矩、弯矩、热量、化学势能全都落在 "
                           "M·L²·T⁻² 上。它没有统一的定义式 —— "
                           "这正是「量纲相同不等于物理量相同」的最好例子。"),
    "chemical_potential_energy": ("umbrella", "化学势能指化学反应中伴随的能量变化部分，"
                                              "它没有独立的定义式：数值上就是能量，"
                                              "区别在于所处的情境（化学键的重新组合）。"
                                              "把它单独列出是为了标注这个语用区别。"),
}

# ---------------------------------------------------- 3) 新增定义式公式

E = {"M": 1, "L": 2, "T": -2}          # 能量
F_ = {"M": 1, "L": 1, "T": -2}         # 力
P_ = {"M": 1, "L": 2, "T": -3}         # 功率
PR = {"M": 1, "L": -1, "T": -2}        # 压强 / 应力
L2 = {"L": 2}
L3 = {"L": 3}
LEN = {"L": 1}
TIM = {"T": 1}
MASS = {"M": 1}
ACC = {"L": 1, "T": -2}
VEL = {"L": 1, "T": -1}
MOM = {"M": 1, "L": 1, "T": -1}
ANGM = {"M": 1, "L": 2, "T": -1}
OMEGA = {"T": -1}
ALPHA = {"T": -2}
CUR = {"I": 1}
CHG = {"T": 1, "I": 1}
VOLT = {"M": 1, "L": 2, "T": -3, "I": -1}
TORQ = {"M": 1, "L": 2, "T": -2}
RHO = {"M": 1, "L": -3}
NUV = {"L": 2, "T": -1}
MUV = {"M": 1, "L": -1, "T": -1}
GRAV = {"M": -1, "L": 3, "T": -2}
JERK = {"L": 1, "T": -3}
ENV = {"M": 1, "T": -3}                # 强度（能流密度）
JOULE = {"J": 1}                       # 发光强度


def sig(lhs_sym, lhs_dim, rhs, ops):
    return {"lhs": {"symbol": lhs_sym, "dim": lhs_dim}, "rhs": rhs, "ops": ops}


def e(sym, dim, **kw):
    d = {"symbol": sym, "dim": dim}
    d.update(kw)
    return d


NEW_FORMULAS = [
    {
        "id": "momentum_definition",
        "tex": "p = m·v",
        "zh": "动量等于质量与速度的乘积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("p", MOM, [e("m", MASS), e("v", VEL, structure="vector")], ["mul"]),
        "relations": [
            {"kind": "equivalence", "from": "kg·m/s", "to": "N·s", "via": "p = m·v",
             "note": "动量的两种等价写法：N·s 正是冲量的单位，说明冲量就是动量的变化量"},
        ],
        "defines": "momentum",
    },
    {
        "id": "torque_definition",
        "tex": "M = F·r",
        "zh": "力矩等于力与力臂的乘积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("M_torque", TORQ, [e("F", F_, structure="vector"),
                                            e("r", LEN, structure="vector")], ["mul"]),
        "relations": [
            {"kind": "equivalence", "from": "N·m", "to": "kg·m^2/s^2", "via": "M = F·r",
             "note": "力矩与能量量纲完全相同（都是 M·L²·T⁻²），"
                     "但力矩是叉积、能量是标量积——量纲相同、物理意义不同"},
        ],
        "defines": "torque",
    },
    {
        "id": "power_definition",
        "tex": "P = W/t",
        "zh": "功率等于功与所用时间之比",
        "category": "力学",
        "pattern": "ratio",
        "signature": sig("P", P_, [e("W_work", E), e("t", TIM, role="divisor")], ["div"]),
        "relations": [
            {"kind": "equivalence", "from": "J/s", "to": "W", "via": "P = W/t",
             "note": "瓦特就是焦耳每秒——SI 导出单位的定义方式"},
        ],
        "defines": "power",
    },
    {
        "id": "weight_definition",
        "tex": "G = m·g",
        "zh": "重力等于质量与重力加速度的乘积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("G", F_, [e("m", MASS), e("g", ACC)], ["mul"]),
        "relations": [],
        "defines": "weight",
    },
    {
        "id": "density_definition",
        "tex": "rho = m/V",
        "zh": "密度等于质量与体积之比",
        "category": "力学",
        "pattern": "ratio",
        "signature": sig("rho", RHO, [e("m", MASS), e("V_vol", L3, role="divisor")], ["div"]),
        "relations": [],
        "defines": "density",
    },
    {
        "id": "volume_definition",
        "tex": "V = l·w·h",
        "zh": "长方体的体积等于长、宽、高之积",
        "category": "几何与运动学",
        "pattern": "product",
        "signature": sig("V_vol", L3, [e("l", LEN), e("w", LEN), e("h", LEN)], ["mul"]),
        "relations": [],
        "defines": "volume",
    },
    {
        "id": "area_definition",
        "tex": "A = l·w",
        "zh": "矩形的面积等于长与宽之积",
        "category": "几何与运动学",
        "pattern": "product",
        "signature": sig("A_area", L2, [e("l", LEN), e("w", LEN)], ["mul"]),
        "relations": [],
        "defines": "area",
    },
    {
        "id": "frequency_definition",
        "tex": "f = 1/T",
        "zh": "频率等于周期的倒数",
        "category": "几何与运动学",
        "pattern": "ratio",
        "signature": sig("f", OMEGA, [e("T", TIM, role="divisor")], ["div"]),
        "relations": [
            {"kind": "equivalence", "from": "Hz", "to": "1/s", "via": "f = 1/T",
             "note": "赫兹就是「每秒一次」——频率的单位由时间单位导出"},
        ],
        "defines": "frequency",
    },
    {
        "id": "angular_velocity_definition",
        "tex": "omega = v/r",
        "zh": "角速度等于线速度与半径之比",
        "category": "几何与运动学",
        "pattern": "ratio",
        "signature": sig("omega", OMEGA, [e("v", VEL), e("r", LEN, role="divisor")], ["div"]),
        "relations": [
            {"kind": "equivalence", "from": "rad/s", "to": "1/s", "via": "omega = v/r",
             "note": "rad 是无因次标记，所以 rad/s 与 1/s 量纲完全相同"},
        ],
        "defines": "angular_velocity",
    },
    {
        "id": "angular_acceleration_definition",
        "tex": "alpha = a/r",
        "zh": "角加速度等于切向加速度与半径之比",
        "category": "几何与运动学",
        "pattern": "ratio",
        "signature": sig("alpha", ALPHA, [e("a", ACC), e("r", LEN, role="divisor")], ["div"]),
        "relations": [],
        "defines": "angular_acceleration",
    },
    {
        "id": "angular_momentum_definition",
        "tex": "L_ang = r·p",
        "zh": "角动量等于位矢与动量的叉积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("L_ang", ANGM, [e("r", LEN, structure="vector"),
                                          e("p", MOM, structure="vector")], ["mul"]),
        "relations": [
            {"kind": "equivalence", "from": "kg·m^2/s", "to": "J·s", "via": "L_ang = r·p",
             "note": "角动量与「作用量」量纲相同（J·s），这不是巧合："
                     "角动量的量子化单位正是作用量"},
        ],
        "defines": "angular_momentum",
    },
    {
        "id": "impulse_definition",
        "tex": "I_imp = F·t",
        "zh": "冲量等于力与作用时间的乘积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("I_imp", MOM, [e("F", F_, structure="vector"), e("t", TIM)], ["mul"]),
        "relations": [
            {"kind": "equivalence", "from": "N·s", "to": "kg·m/s", "via": "I_imp = F·t",
             "note": "冲量与动量量纲相同，冲量定理即由此而来"},
        ],
        "defines": "impulse",
    },
    {
        "id": "jerk_definition",
        "tex": "j_jerk = da/dt",
        "zh": "急动度等于加速度对时间的导数",
        "category": "几何与运动学",
        "pattern": "gradient",
        "signature": sig("j_jerk", JERK, [e("a", ACC, role="operand"),
                                           e("t", TIM, role="differentiator", order=1)],
                         ["derivative"]),
        "relations": [],
        "defines": "jerk",
    },
    {
        "id": "gravitational_constant_definition",
        "tex": "G_const = F·r^2/(m1·m2)",
        "zh": "由万有引力定律反解出的引力常量",
        "category": "力学",
        "pattern": "ratio",
        "signature": sig("G_const", GRAV,
                         [e("F", F_), e("r", LEN, exp=2),
                          e("m1", MASS, role="divisor"), e("m2", MASS, role="divisor")],
                         ["div"]),
        "relations": [],
        "defines": "gravitational_constant",
    },
    {
        "id": "bending_moment_definition",
        "tex": "M_bending = F·l",
        "zh": "弯矩等于力与力臂的乘积（梁的截面内力矩）",
        "category": "材料力学",
        "pattern": "product",
        "signature": sig("M_bending", TORQ, [e("F", F_), e("l", LEN)], ["mul"]),
        "relations": [],
        "defines": "bending_moment",
    },
    {
        "id": "electric_charge_definition",
        "tex": "q = i·t",
        "zh": "电荷量等于电流与时间的乘积",
        "category": "电磁学",
        "pattern": "product",
        "signature": sig("q", CHG, [e("i", CUR), e("t", TIM)], ["mul"]),
        "relations": [
            {"kind": "equivalence", "from": "C", "to": "A·s", "via": "q = i·t",
             "note": "库仑就是安培秒——电荷单位由电流单位导出"},
        ],
        "defines": "electric_charge",
    },
    {
        "id": "current_density_definition",
        "tex": "j = i/A",
        "zh": "电流密度等于电流与截面积之比",
        "category": "电磁学",
        "pattern": "ratio",
        "signature": sig("j", {"L": -2, "I": 1},
                         [e("i", CUR), e("A", L2, role="divisor")], ["div"]),
        "relations": [],
        "defines": "current_density",
    },
    {
        "id": "electric_potential_energy_definition",
        "tex": "E_pe = q·U",
        "zh": "电势能等于电荷量与电势的乘积",
        "category": "电磁学",
        "pattern": "product",
        "signature": sig("E_pe", E, [e("q", CHG), e("U", VOLT)], ["mul"]),
        "relations": [],
        "defines": "electric_potential_energy",
    },
    {
        "id": "gravitational_potential_energy_definition",
        "tex": "E_pg = m·g·h",
        "zh": "重力势能等于质量、重力加速度与高度之积",
        "category": "力学",
        "pattern": "product",
        "signature": sig("E_pg", E, [e("m", MASS), e("g", ACC), e("h", LEN)], ["mul"]),
        "relations": [],
        "defines": "gravitational_potential_energy_q",
    },
    {
        "id": "stress_definition",
        "tex": "sigma = F/A",
        "zh": "应力等于内力与受力面积之比",
        "category": "材料力学",
        "pattern": "ratio",
        "signature": sig("sigma", PR, [e("F", F_), e("A", L2, role="divisor")], ["div"]),
        "relations": [
            {"kind": "equivalence", "from": "Pa", "to": "N/m^2", "via": "sigma = F/A",
             "note": "应力的单位与压强相同：都是「单位面积上的力」"},
        ],
        "defines": "stress",
    },
    {
        "id": "elastic_modulus_definition",
        "tex": "E_mod = sigma/epsilon",
        "zh": "弹性模量等于应力与应变之比（胡克定律的斜率）",
        "category": "材料力学",
        "pattern": "ratio",
        "signature": sig("E_mod", PR, [e("sigma", PR),
                                        e("epsilon", {}, role="divisor")], ["div"]),
        "relations": [],
        "defines": "elastic_modulus",
    },
    {
        "id": "spring_constant_definition",
        "tex": "k = F/x",
        "zh": "弹簧刚度等于弹力与形变量之比",
        "category": "力学",
        "pattern": "ratio",
        "signature": sig("k", {"M": 1, "T": -2},
                         [e("F", F_), e("x", LEN, role="divisor")], ["div"]),
        "relations": [],
        "defines": "spring_constant",
    },
    {
        "id": "surface_tension_definition",
        "tex": "gamma = F/l",
        "zh": "表面张力等于表面上的收缩力与作用线长度之比",
        "category": "力学",
        "pattern": "ratio",
        "signature": sig("gamma", {"M": 1, "T": -2},
                         [e("F", F_), e("l", LEN, role="divisor")], ["div"]),
        "relations": [],
        "defines": "surface_tension",
    },
    {
        "id": "kinematic_viscosity_definition",
        "tex": "nu = mu/rho",
        "zh": "运动黏度等于动力黏度与密度之比",
        "category": "流体力学",
        "pattern": "ratio",
        "signature": sig("nu", NUV, [e("mu", MUV), e("rho", RHO, role="divisor")], ["div"]),
        "relations": [],
        "defines": "kinematic_viscosity",
    },
    {
        "id": "sound_pressure_definition",
        "tex": "p_ac = F/A",
        "zh": "声压是声波引起的交变压强",
        "category": "声学",
        "pattern": "ratio",
        "signature": sig("p_ac", PR, [e("F", F_), e("A", L2, role="divisor")], ["div"]),
        "relations": [],
        "defines": "sound_pressure",
    },
    {
        "id": "sound_power_definition",
        "tex": "P_ac = E/t",
        "zh": "声功率等于声源辐射的声能与时间之比",
        "category": "声学",
        "pattern": "ratio",
        "signature": sig("P_ac", P_, [e("E", E), e("t", TIM, role="divisor")], ["div"]),
        "relations": [],
        "defines": "sound_power",
    },
    {
        "id": "sound_intensity_definition",
        "tex": "I_ac = P/A",
        "zh": "声强等于声功率与垂直面积之比",
        "category": "声学",
        "pattern": "ratio",
        "signature": sig("I_ac", ENV, [e("P", P_), e("A", L2, role="divisor")], ["div"]),
        "relations": [],
        "defines": "sound_intensity",
    },
    {
        "id": "intensity_definition",
        "tex": "I_int = P/A",
        "zh": "强度（能流密度）等于功率与垂直面积之比",
        "category": "波动与辐射",
        "pattern": "ratio",
        "signature": sig("I_int", ENV, [e("P", P_), e("A", L2, role="divisor")], ["div"]),
        "relations": [],
        "defines": "intensity",
    },
    {
        "id": "radiant_flux_definition",
        "tex": "Phi_e = E/t",
        "zh": "辐射通量等于辐射能与时间之比",
        "category": "光学",
        "pattern": "ratio",
        "signature": sig("Phi_e", P_, [e("E", E), e("t", TIM, role="divisor")], ["div"]),
        "relations": [],
        "defines": "radiant_flux",
    },
    {
        "id": "luminous_flux_definition",
        "tex": "Phi_v = I_v·Omega",
        "zh": "光通量等于发光强度与立体角之积",
        "category": "光学",
        "pattern": "product",
        "signature": sig("Phi_v", JOULE, [e("I_v", JOULE),
                                           e("Omega", {}, structure="scalar")], ["mul"]),
        "relations": [],
        "defines": "luminous_flux",
    },
    {
        "id": "illuminance_definition",
        "tex": "E_v = I_v/r^2",
        "zh": "照度等于发光强度除以距离的平方（点光源的平方反比律）",
        "category": "光学",
        "pattern": "ratio",
        "signature": sig("E_v", {"L": -2, "J": 1},
                         [e("I_v", JOULE), e("r", LEN, role="divisor", exp=2)], ["div"]),
        "relations": [],
        "defines": "illuminance",
    },
    {
        # 理想气体状态方程。加它的直接理由是：物质的量原先一条公式都没有，
        # 页面的「相关物理式子」槽位是空的。而这条方程正是热力学温度与
        # 物质的量同时出现的地方——它是这两个基本量唯一被一起用到的式子。
        "id": "ideal_gas_law",
        "tex": "p·V = n·R·T",
        "zh": "理想气体状态方程：压强与体积之积等于物质的量、摩尔气体常量与温度之积",
        "category": "热力学",
        "pattern": "ratio",
        "signature": sig("p_press", PR,
                         [e("n", {"N": 1}),
                          e("R_gas", {"M": 1, "L": 2, "T": -2, "N": -1, "TH": -1}),
                          e("T", {"TH": 1}),
                          e("V_vol", L3, role="divisor")],
                         ["div"]),
        "relations": [
            {"kind": "equivalence", "from": "Pa·m^3", "to": "J", "via": "p·V = n·R·T",
             "note": "压强乘体积就是能量（J = Pa·m³），"
                     "所以 pV 与 nRT 量纲相同——这正是量纲分析核对这条方程的方式"},
        ],
    },
]

#: 逐条修正已写入数据的公式（幂等）。
#:
#: 理想气体方程最初写成 `p = n·R·T/V` 的比值型签名，但 `tex` 用的是
#: 教科书原样 `p·V = n·R·T`。结果是界面上「乘积型/比值型」的徽标与
#: 公式文本对不上——徽标按**签名**算，文本按**惯例**写，两者不一致
#: 时读者会以为工具算错了。改成纯乘积签名（V 用 exp=-1 表示在分母），
#: 让签名与文本描述同一件事。
PATCH_FORMULAS: dict[str, dict[str, object]] = {
    "ideal_gas_law": {
        "pattern": "product",
        "signature": sig("p_press", PR,
                         [e("n", {"N": 1}),
                          e("R_gas", {"M": 1, "L": 2, "T": -2, "N": -1, "TH": -1}),
                          e("T", {"TH": 1}),
                          e("V_vol", L3, exp=-1)],
                         ["mul"]),
    },
}

# 给已有公式补 defines
PATCH_DEFINES = {"work_from_force": "work"}

#: 修正 `magnetic_force_lorentz` 的 lhs 符号。
#:
#: 这条公式的 tex 写的是 `F = q·v·B`，`lhs.symbol` 却写成了 `B`——
#: 于是同一条签名里 `B` 既是「结果」又是一个乘数，而且量纲不同
#: （lhs 是力的量纲，rhs 的 B 是磁感应强度）。自检没抓到它，
#: 因为 `_check_formulas` 只比量纲、不比符号；后果是符号表里多出一行
#: 「B = 磁场力」而且没有跳转链接。
#:
#: 修完顺手把「同一签名里一个符号只能有一个量纲」加成自检规则
#: （见 registry._check_formulas），否则同类错误还会再溜进来。
PATCH_LHS_SYMBOL = {
    "magnetic_force_lorentz": ("B", "F"),
}

# 新增符号表条目（只有引擎自动解析不出来的才需要写）
NEW_GLOSSARY = [
    {
        "symbol": "w",
        "dim": {"L": 1},
        "zh": "宽度",
        "meaning": "矩形（或长方体）的一个边长，与长度同量纲",
    },
    {
        "symbol": "T",
        "dim": {"T": 1},
        "zh": "周期",
        "meaning": "一次完整振动（或一个循环）所用的时间。"
                   "同一个字母 T 也用作特斯拉的符号，两者量纲不同，按所在公式区分",
    },
    {
        "symbol": "T",
        "dim": {"TH": 1},
        "zh": "热力学温度",
        "meaning": "分子热运动剧烈程度的量度，SI 七个基本量之一。"
                   "同一个字母 T 在别的公式里也可能是周期（量纲 T），按所在公式区分",
    },
    {
        "symbol": "R_gas",
        "dim": {"M": 1, "L": 2, "T": -2, "N": -1, "TH": -1},
        "zh": "摩尔气体常量",
        "meaning": "理想气体状态方程 pV = nRT 中的比例常量，"
                   "量纲为能量每摩尔每开尔文，数值约 8.314 J/(mol·K)",
    },
    {
        "symbol": "epsilon",
        "dim": {},
        "zh": "应变",
        "meaning": "长度变化量与原始长度之比，是两个长度之比，因此是无因次纯数",
    },
    {
        "symbol": "Omega",
        "dim": {},
        "zh": "立体角",
        "meaning": "球面上面积与半径平方之比，是无因次纯数（单位 sr，但 sr 不构成量纲）",
    },
]


#: 缺失的两个 SI 基本量。原先注册表只有 7 个基本量里的 5 个
#: （长度/质量/时间/电流/发光强度），于是「SI 七个基本量」的说法在数据里
#: 站不住，搜「温度」直接查不到。补上之后七个基本量齐全。
NEW_QUANTITIES = [
    {
        "id": "thermodynamic_temperature",
        "zh": "热力学温度",
        "symbol": "T_th",
        "dim": {"TH": 1},
        "category": "热力学",
        "unit_forms": ["K"],
        "decompositions": [{"expr": "K"}],
        "aliases": ["温度", "T_th"],
        "note": "SI 七个基本量之一，量纲 Θ。它衡量分子热运动的剧烈程度，"
                "是唯一无法由其他基本量导出的热学量。摄氏温度 t = T − 273.15 K "
                "只是它的平移：两者量纲完全相同，零点不同——量纲分析看不出这个区别，"
                "这正是量纲法的边界。",
        "domain": "thermo",
        "defined_as": "si-base",
        "definition_note": "热力学温度是 SI 七个基本量之一，由开尔文这一基本单位定义"
                           "（2019 年起以玻尔兹曼常数固定值定义），不是由别的量通过公式"
                           "推导出来的。",
    },
    {
        "id": "amount_of_substance",
        "zh": "物质的量",
        "symbol": "n",
        "dim": {"N": 1},
        "category": "热力学",
        "unit_forms": ["mol"],
        "decompositions": [{"expr": "mol"}],
        "aliases": ["摩尔数"],
        "note": "SI 七个基本量之一，量纲 N。它把「微粒个数」按阿伏伽德罗常数折算成宏观量，"
                "是七个基本量里唯一纯粹为了计数而设立的。",
        "domain": "thermo",
        "defined_as": "si-base",
        "definition_note": "物质的量是 SI 七个基本量之一，由摩尔这一基本单位定义"
                           "（以阿伏伽德罗常数固定值定义）。它的特殊之处在于"
                           "衡量的其实是「粒子个数」这个纯数，乘以阿伏伽德罗常数才成为宏观量。",
    },
]


def main() -> int:
    # ---- quantities ----
    qdoc = json.loads(Q_PATH.read_text(encoding="utf-8"))
    qn = q_defined = 0
    qhave = {q["id"] for q in qdoc["quantities"]}
    qadded = 0
    for q in NEW_QUANTITIES:
        if q["id"] not in qhave:
            qdoc["quantities"].append(q)
            qadded += 1
    for q in qdoc["quantities"]:
        qid = q["id"]
        if not q.get("note") and qid in NOTES:
            q["note"] = NOTES[qid]
            qn += 1
        if qid in DEFINED_AS:
            kind, text = DEFINED_AS[qid]
            q["defined_as"] = kind
            q["definition_note"] = text
            q_defined += 1
    Q_PATH.write_text(
        json.dumps(qdoc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # ---- formulas ----
    fdoc = json.loads(F_PATH.read_text(encoding="utf-8"))
    have = {f["id"] for f in fdoc["formulas"]}
    added = 0
    fixed_sym = 0
    for f in NEW_FORMULAS:
        if f["id"] in have:
            continue
        fdoc["formulas"].append(f)
        added += 1
    patched = 0
    for f in fdoc["formulas"]:
        want = PATCH_DEFINES.get(f["id"])
        if want and not f.get("defines"):
            f["defines"] = want
            patched += 1
        fix = PATCH_LHS_SYMBOL.get(f["id"])
        if fix and f["signature"]["lhs"].get("symbol") == fix[0]:
            f["signature"]["lhs"]["symbol"] = fix[1]
            fixed_sym += 1
        fields = PATCH_FORMULAS.get(f["id"])
        if fields:
            for k, v in fields.items():
                f[k] = v
    F_PATH.write_text(
        json.dumps(fdoc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # ---- symbol glossary ----
    gdoc = json.loads(G_PATH.read_text(encoding="utf-8"))
    ghave = {(x["symbol"], json.dumps(x.get("dim"), sort_keys=True))
             for x in gdoc["symbols"]}
    gadded = 0
    for x in NEW_GLOSSARY:
        key = (x["symbol"], json.dumps(x.get("dim"), sort_keys=True))
        if key in ghave:
            continue
        gdoc["symbols"].append(x)
        gadded += 1
    G_PATH.write_text(
        json.dumps(gdoc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"quantities.json：新增物理量 {qadded} 条，补 note {qn} 条，"
          f"defined_as {q_defined} 条")
    print(f"formulas.json  ：新增公式 {added} 条，补 defines {patched} 条，"
          f"修正 lhs 符号 {fixed_sym} 条")
    print(f"symbol_glossary.json：新增符号 {gadded} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
