"""量纲关系浏览器引擎。

模块依赖方向（刻意保持单向，见设计文档 4.1）：

    dim          ← 无依赖，纯向量空间
    parser       ← 无依赖，纯语法
    algebra      ← dim
    evaluator    ← dim, parser
    registry     ← 以上全部（做数据自检）
    formula/tree ← registry
    api          ← 唯一出口

这个方向不允许反向。registry 能 import evaluator，但 evaluator
绝不能 import registry——否则测试 evaluator 就要先造一份完整数据。
"""

from .dim import Dim, DimError

__all__ = ["Dim", "DimError"]
