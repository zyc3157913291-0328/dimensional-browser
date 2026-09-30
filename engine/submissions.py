"""公式补录：用户提交、存储、状态机。

背景（用户提出）
----------------
界面上有些「算法枚举」出来的量纲组合，量纲完全正确，但工具找不到
使用它的现实物理式子。用户可以把自己的资料（教材出处、论文）提交进来。

设计原则（三条都是刻意的）
--------------------------
1. **用户数据与我的数据物理隔离**。用户提交存在工作区根目录的
   `submissions.json`，与 `data/*.json` 完全分开。我不可能因为
   重新生成数据文件而覆盖掉用户的东西。

2. **只有 verified 进树**。`pending`（待核实）与 `disputed`（存疑）
   只在条目旁展示，不参与建树——因为它们尚未被确认为现实物理式子，
   混进「依据现实式子」的树里会破坏那个树的可信度。

3. **存疑不删除**。`disputed` 只是加一个黄色标记，用户写的原文
   照常展示。核实结论与用户原文是两件事，不能因为核实不通过就把
   用户的贡献抹掉。

核实机制
--------
本轮**只做提交与存储**，不做自动核实。原因是拓扑限制：本地服务器
进程没有联网能力，`web_search` 属于 agent 会话的工具。所以：
  - `verify` 字段预留结构（来源链接、判断依据、核实时间）
  - `promote` / `dispute` 两个方法已实现并测试，供后续核实流程调用
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .paths import submissions_path

DEFAULT_PATH = submissions_path()

# 状态机。转换由 promote / dispute 强制约束，不接受任意写入。
STATUS_PENDING = "pending"
STATUS_VERIFIED = "verified"
STATUS_DISPUTED = "disputed"

_ALL_STATUSES = (STATUS_PENDING, STATUS_VERIFIED, STATUS_DISPUTED)

# 补录的种类。决定核实通过后挂到页面的哪个槽位（见页面规范 §5）。
# 存在的理由：原先把「补录」等同于「补一条公式」，但用户报告
# 「有些量纲没有定义式」之后，定义式的空缺也需要同一条补录通道——
# 它填的不是量纲组合，而是一条等式。两者的核实判据不同，必须分开。
KIND_FORMULA = "formula"        # 该量纲组合出现在什么现实式子里
KIND_DEFINITION = "definition"  # 某个物理量的定义式
KIND_UNIT_FORM = "unit_form"    # 某个物理量的惯用单位写法
KIND_NOTE = "note"              # 某个物理量的一句话描述

_ALL_KINDS = (KIND_FORMULA, KIND_DEFINITION, KIND_UNIT_FORM, KIND_NOTE)

KIND_ZH: dict[str, str] = {
    KIND_FORMULA: "公式来源",
    KIND_DEFINITION: "定义式",
    KIND_UNIT_FORM: "常见写法",
    KIND_NOTE: "条目说明",
}

#: 哪些种类允许「没有表达式」——说明类与定义类的内容本就不必是单位组合。
_KINDS_WITHOUT_EXPR = (KIND_NOTE,)

#: 哪些种类必须给表达式。
_KINDS_WITH_EXPR = (KIND_FORMULA, KIND_DEFINITION, KIND_UNIT_FORM)

# 允许的状态转换。pending 是唯一入口；已定论的条目可以再翻案，
# 因为核实本身可能有错——但必须显式走转换方法。
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_VERIFIED, STATUS_DISPUTED}),
    STATUS_VERIFIED: frozenset({STATUS_DISPUTED}),
    STATUS_DISPUTED: frozenset({STATUS_VERIFIED}),
}

# 单条提交的字段长度上限。防止界面被误粘贴的超长文本撑坏。
_MAX_LEN = {
    "query": 120,
    "target_dim": 120,
    "target_qid": 80,
    "expr": 200,
    "zh": 200,
    "note": 2000,
    "source": 1000,
}


class SubmissionError(ValueError):
    """提交数据非法。"""


@dataclass
class Submission:
    """一条补录。"""

    id: str
    created_at: str
    query: str
    target_dim: str
    expr: str
    zh: str = ""
    note: str = ""
    source: str = ""
    status: str = STATUS_PENDING
    # 补录种类，决定核实通过后填哪个槽位。默认 formula 以兼容旧数据。
    kind: str = KIND_FORMULA
    # 该补录针对哪个物理量 id（定义式 / 常见写法 / 说明类需要）。
    target_qid: str = ""
    # 核实结果。结构预留：{"verdict","reason","urls","checked_at","checked_by"}
    verify: dict[str, Any] | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(raw: Mapping[str, Any]) -> "Submission":
        kind = str(raw.get("kind", KIND_FORMULA))
        if kind not in _ALL_KINDS:
            kind = KIND_FORMULA
        return Submission(
            id=str(raw.get("id", "")),
            created_at=str(raw.get("created_at", "")),
            query=str(raw.get("query", "")),
            target_dim=str(raw.get("target_dim", "")),
            expr=str(raw.get("expr", "")),
            zh=str(raw.get("zh", "")),
            note=str(raw.get("note", "")),
            source=str(raw.get("source", "")),
            status=str(raw.get("status", STATUS_PENDING)),
            kind=kind,
            target_qid=str(raw.get("target_qid", "")),
            verify=dict(raw["verify"]) if isinstance(raw.get("verify"), Mapping) else None,
            history=[
                dict(h) for h in raw.get("history", []) if isinstance(h, Mapping)
            ],
        )


class SubmissionStore:
    """提交的持久化与状态机。

    线程安全：HTTP 服务器是多线程的，所以读写都持锁。
    原子写入：先写临时文件再 replace，避免中途崩溃留下半个文件。
    """

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: dict[str, Submission] = {}
        self._loaded = False
        self.load_error: str = ""

    # -------- 持久化 --------

    def load(self) -> None:
        """读取文件。文件不存在是正常情况（还没有人提交过），不算错误。"""
        with self._lock:
            self._items = {}
            self._loaded = True
            self.load_error = ""
            if not self.path.is_file():
                return
            try:
                text = self.path.read_text(encoding="utf-8-sig")
                doc = json.loads(text)
            except (OSError, json.JSONDecodeError) as exc:
                # 读坏了不能静默丢弃用户数据：记下错误，界面上显示，
                # 但不覆盖文件（保留现场供人工修复）。
                self.load_error = f"submissions.json 读取失败：{exc}"
                return
            raw_items = doc.get("items") if isinstance(doc, dict) else doc
            if not isinstance(raw_items, list):
                self.load_error = "submissions.json 结构不对：缺少 items 数组"
                return
            for raw in raw_items:
                if not isinstance(raw, Mapping):
                    continue
                s = Submission.from_json(raw)
                if s.id:
                    self._items[s.id] = s

    def _save_locked(self) -> None:
        doc = {
            "schema": 1,
            "note": "用户提交的公式补录。此文件由工具写入，但内容属于用户——"
                    "工具不会删除或改写用户填写的内容，只更新 status / verify / history。",
            "items": [s.to_json() for s in self._sorted()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".submissions-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -------- 查询 --------

    def _sorted(self) -> list[Submission]:
        """按创建时间倒序——越新的越靠前。"""
        return sorted(self._items.values(), key=lambda s: s.created_at, reverse=True)

    def all(self) -> list[Submission]:
        with self._lock:
            return list(self._sorted())

    def get(self, sid: str) -> Submission | None:
        with self._lock:
            return self._items.get(sid)

    def by_status(self, status: str) -> list[Submission]:
        with self._lock:
            return [s for s in self._sorted() if s.status == status]

    def for_expr(self, expr: str, dim: str = "") -> list[Submission]:
        """查出针对某个量纲组合的所有提交。

        用**变体集合**做匹配（见 _norm_variants 的说明）：写法歧义无法
        自动消解，所以任一等价写法命中即算命中。这里放宽是安全的，
        因为调用方还会按量纲二次过滤。
        """
        keys = _norm_variants(expr)
        if not keys:
            return []
        with self._lock:
            out = []
            for s in self._sorted():
                if not (_norm_variants(s.expr) & keys):
                    continue
                if dim and s.target_dim and s.target_dim != dim:
                    continue
                out.append(s)
            return out

    def verified_for_expr(self, expr: str, dim: str = "") -> list[Submission]:
        return [
            s for s in self.for_expr(expr, dim) if s.status == STATUS_VERIFIED
        ]

    def for_quantity(self, qid: str, kind: str = "") -> list[Submission]:
        """查出针对某个物理量的补录，可按种类过滤。

        定义式、常见写法、说明这三类是按**物理量**挂的（不是按量纲组合），
        所以查询键也不同——混用会让「静电场的定义式」飘到所有同量纲条目上。
        """
        if not qid:
            return []
        with self._lock:
            return [
                s for s in self._sorted()
                if s.target_qid == qid and (not kind or s.kind == kind)
            ]

    def counts(self) -> dict[str, int]:
        with self._lock:
            out = {st: 0 for st in _ALL_STATUSES}
            for s in self._items.values():
                out[s.status] = out.get(s.status, 0) + 1
            out["total"] = len(self._items)
            return out

    # -------- 写入 --------

    def create(
        self,
        query: str,
        target_dim: str,
        expr: str,
        zh: str = "",
        note: str = "",
        source: str = "",
        kind: str = KIND_FORMULA,
        target_qid: str = "",
    ) -> Submission:
        """新建一条待核实提交。"""
        if kind not in _ALL_KINDS:
            raise SubmissionError(
                f"未知的补录种类 {kind!r}；允许：{list(_ALL_KINDS)}"
            )
        fields = {
            "query": query, "target_dim": target_dim, "expr": expr,
            "zh": zh, "note": note, "source": source, "target_qid": target_qid,
        }
        for name, value in fields.items():
            if len(str(value)) > _MAX_LEN[name]:
                raise SubmissionError(
                    f"{name} 超长（上限 {_MAX_LEN[name]} 字符）"
                )
        if kind in _KINDS_WITH_EXPR and not expr.strip():
            what = "定义式" if kind == KIND_DEFINITION else "量纲组合表达式"
            raise SubmissionError(f"{what}不能为空")
        if kind in (KIND_DEFINITION, KIND_UNIT_FORM) and not target_qid.strip():
            raise SubmissionError("这一种类的补录必须指明是针对哪个物理量")
        if not target_dim.strip() and kind == KIND_FORMULA:
            raise SubmissionError("缺少目标量纲")
        if not (note.strip() or source.strip()):
            # 没有出处也没有说明的提交无法核实，要求至少给一个
            raise SubmissionError("请至少填写「说明」或「出处」二者之一")

        now = datetime.now().isoformat(timespec="seconds")
        s = Submission(
            id=f"sub-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            created_at=now,
            query=query.strip()[: _MAX_LEN["query"]],
            target_dim=target_dim.strip(),
            expr=expr.strip(),
            zh=zh.strip(),
            note=note.strip(),
            source=source.strip(),
            kind=kind,
            target_qid=target_qid.strip(),
        )
        with self._lock:
            self._items[s.id] = s
            self._save_locked()
        return s

    def delete(self, sid: str) -> bool:
        """删除一条。仅在用户明确要求时调用——正常流程不该删用户数据。"""
        with self._lock:
            if sid not in self._items:
                return False
            del self._items[sid]
            self._save_locked()
            return True

    # -------- 状态机 --------

    def promote(self, sid: str, reason: str = "", urls: Iterable[str] = (),
                checked_by: str = "agent") -> Submission:
        """核实在先：转为 verified。只有 verified 会进入建树。"""
        return self._transition(sid, STATUS_VERIFIED, reason, list(urls), checked_by)

    def dispute(self, sid: str, reason: str = "", urls: Iterable[str] = (),
                checked_by: str = "agent") -> Submission:
        """核实不通过：转为 disputed。**保留用户原文**，只加存疑标记。"""
        return self._transition(sid, STATUS_DISPUTED, reason, list(urls), checked_by)

    def _transition(
        self, sid: str, to: str, reason: str, urls: list[str], checked_by: str
    ) -> Submission:
        if to not in _ALL_STATUSES:
            raise SubmissionError(f"未知状态 {to!r}")
        with self._lock:
            s = self._items.get(sid)
            if s is None:
                raise SubmissionError(f"没有 id 为 {sid!r} 的提交")
            if to == s.status:
                return s
            if to not in _ALLOWED_TRANSITIONS.get(s.status, frozenset()):
                raise SubmissionError(
                    f"不允许的状态转换：{s.status} → {to}"
                )
            now = datetime.now().isoformat(timespec="seconds")
            s.history.append({"at": now, "from": s.status, "to": to,
                              "reason": reason[:500], "by": checked_by})
            s.status = to
            s.verify = {
                "verdict": to,
                "reason": reason[:2000],
                "urls": urls[:10],
                "checked_at": now,
                "checked_by": checked_by,
            }
            self._save_locked()
            return s

    # -------- 供建树使用 --------

    def verified_map(self) -> dict[str, list[Submission]]:
        """{规范化表达式: [已核实的提交]}，供建树时并入。

        以规范化表达式为键，且列表内按时间倒序，便于取最新的一条作为
        该组合的说明来源。
        """
        with self._lock:
            out: dict[str, list[Submission]] = {}
            for s in self._sorted():
                if s.status != STATUS_VERIFIED:
                    continue
                out.setdefault(_norm_expr(s.expr), []).append(s)
            return out


def _norm_variants(expr: str) -> set[str]:
    """把一个表达式规范化成**一组**可比较的键。

    为什么要一组而不是一个：写法歧义无法自动消解。典型例子
    `m2` —— 在 `m2*Pa` 里用户想表达的是「米平方」，但在
    `F = G·m1·m2/r²` 里 `m1`/`m2` 是一个合法标识符（两个物体的质量）。
    解析器无法判断，所以两种解释都生成出来，匹配时任一命中即算命中。

    匹配放宽不会引入错误：命中的提交还要经过量纲比对才会被采纳
    （见 SubmissionStore.for_expr 的 dim 参数与建树时的量纲校验）。
    """
    if not expr:
        return set()
    s = str(expr).strip()
    out: set[str] = set()

    # 变体一：解析器规范化（能处理 * · 空格 ^ ** 等写法差异）
    rendered = ""
    try:
        from . import parser as P  # 延迟导入，避免模块级循环依赖
        rendered = P.render(P.parse(s))
        out.add(rendered)
        # 变体一之二：**结构**规范形（额外归一乘法的交换律）。
        # 少了它，用户提交 `m·N` 时匹配不到库里已有的 `N·m`。
        out.add(P.canonical_expr(s))
    except Exception:  # noqa: BLE001 — 解析失败是预期情况
        pass

    # 变体二/三：在渲染结果上做「标识符尾随数字 ↔ 显式幂」的双向替换。
    # m2 → m^2，以及 m^2 → m2。
    bases = [b for b in (rendered, s) if b]
    for base in bases:
        # 单字母后跟一位数字，且后面不是数字/字母 → 视为幂
        as_pow = re.sub(r"(?<![A-Za-z0-9_])([A-Za-z])(\d)(?![0-9A-Za-z_])",
                        r"\1^\2", base)
        out.add(as_pow)
        as_digit = re.sub(r"(?<![A-Za-z0-9_])([A-Za-z])\^(\d)(?![0-9A-Za-z_])",
                          r"\1\2", base)
        out.add(as_digit)

    # 变体四：纯宽松（去符号、小写），保证任何输入都有键
    loose = s
    for ch in (" ", "\t", "\n", "*", "×", "⋅", ".", "·", "^"):
        loose = loose.replace(ch, "")
    out.add(loose.replace("÷", "/").lower())

    return {v for v in out if v}


def _norm_expr(expr: str) -> str:
    """单个规范化键（取最简单的一个），仅用于展示与日志。

    真正的匹配请用 _norm_variants()——单键会漏配写法歧义。
    """
    variants = _norm_variants(expr)
    if not variants:
        return ""
    return sorted(variants, key=lambda v: (len(v), v))[0]
