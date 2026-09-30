"""JSON API：把建树结果序列化，供 Web 界面消费。

设计文档 4.1：api 是唯一出口。前端只做渲染，不含任何量纲计算——
这样界面上显示的每一条关系都必然经过引擎的齐次性校验，
不可能出现"前端算出来好看但物理上错"的内容。

只依赖标准库 http.server，零第三方依赖。
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .dim import Dim
from .matcher import Matcher
from .paths import web_dir
from .registry import Registry
from .submissions import KIND_ZH as SUBMISSION_KIND_ZH
from .submissions import SubmissionError, SubmissionStore
from .tree import EDGE_KINDS, Branch, EntityView, TreeBuilder

WEB_DIR = web_dir()


def dim_to_json(dim: Dim) -> dict[str, Any]:
    """量纲序列化成前端可直接渲染的形式。

    同时给三种表示，避免前端自己拼：
      si        M·L·T⁻²      规范式，用于严格比较与展示
      zh        质量·长度·时间⁻²  中文式，教学用
      plain     M·L·T^-2      纯文本式，方便复制
      powers    原始幂次字典    便于前端做二次判断
    """
    return {
        "si": dim.format_si(),
        "zh": dim.describe_zh(),
        "plain": dim.format_si(superscript=False),
        "powers": dim.to_json(),
        "is_dimensionless": dim.is_dimensionless,
    }


def branch_to_json(b: Branch, depth: int = 0) -> dict[str, Any]:
    return {
        "kind": b.kind,
        "label": b.label,
        "short_label": b.short_label,
        "explain": b.explain,
        "expr": b.expr,
        "zh": b.zh,
        "dim": dim_to_json(b.dim),
        "points_to": list(b.points_to),
        "source": {
            "formula_id": b.source.formula_id,
            "tex": b.source.tex,
            "formula_zh": b.source.formula_zh,
            "note": b.source.note,
            "pattern": b.source.pattern,
            "pattern_zh": b.source.pattern_zh,
            "is_from_formula": b.source.is_from_formula,
            "inverse": b.source.inverse,
            "inverse_of": b.source.inverse_of,
            "submitted_id": b.source.submitted_id,
            "submitted_status": b.source.submitted_status,
            "submitted_source": b.source.submitted_source,
            "submitted_kind": b.source.submitted_kind,
        },
        "children": [branch_to_json(c, depth + 1) for c in b.children],
    }


def entity_to_json(e: EntityView) -> dict[str, Any]:
    return {
        "id": e.id,
        "zh": e.zh,
        "symbol": e.symbol,
        "category": e.category,
        "domain": e.domain if hasattr(e, "domain") else "",
        "domain_zh": e.domain_zh if hasattr(e, "domain_zh") else "",
        "structure": e.structure,
        "dim": dim_to_json(e.dim),
        "unit_forms": list(e.unit_forms),
        "unit_form_details": [dict(d) for d in e.unit_form_details],
        "note": e.note,
        "defined_as": e.defined_as,
        "definition_note": e.definition_note,
        "homogeneous": list(e.homogeneous),
        "equivalent": [branch_to_json(b) for b in e.equivalent],
        "generated": [branch_to_json(b) for b in e.generated],
        "searched": [branch_to_json(b) for b in e.searched],
        "search_ran": e.search_ran,
        "submitted": [branch_to_json(b) for b in e.submitted],
        "disputed": [branch_to_json(b) for b in e.disputed],
        "formulas": list(e.formulas),
        "definitions": list(e.definitions),
    }


class Api:
    """把 Registry + TreeBuilder + SubmissionStore 包成可查询的服务对象。

    每次请求都新建 TreeBuilder 很浪费，但 TreeBuilder 本身几乎无状态
    （只有 max_depth 与提交存储引用），所以复用同一实例是安全的。
    """

    def __init__(self, reg: Registry, submissions: SubmissionStore | None = None) -> None:
        self.reg = reg
        self.submissions = submissions or SubmissionStore()
        if not self.submissions._loaded:  # noqa: SLF001
            self.submissions.load()
        self._builders: dict[int, TreeBuilder] = {}
        self._lock = threading.Lock()
        # 联想匹配器：构建一次，之后每次键入只做一次全量打分（约 1 ms）
        self.matcher = Matcher(reg)
        self._suggest_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def builder(self, max_depth: int) -> TreeBuilder:
        with self._lock:
            b = self._builders.get(max_depth)
            if b is None:
                b = TreeBuilder(
                    self.reg, max_depth=max_depth, submissions=self.submissions
                )
                self._builders[max_depth] = b
            return b

    # -------- 端点实现 --------

    def suggest(self, text: str, limit: int = 8) -> dict[str, Any]:
        """即时联想。用户要求「直接在输入的同时匹配可能结果」。

        加一层小缓存：同一个前缀在连续键入中会被反复请求
        （用户删掉一个字再打回来），缓存命中可以完全省掉这次打分。
        缓存只增不减，但键空间受用户实际输入限制，且条目很小。
        """
        q = (text or "").strip()
        if not q:
            return {"query": q, "items": []}
        key = (q, limit)
        # 缓存与 _builders 共用同一把锁：HTTP 服务器是多线程的，
        # 无锁写 dict 虽然不会崩，但可能让一次 clear() 与写入交错、
        # 丢掉刚算好的结果。加锁的代价可以忽略（读多写少）。
        with self._lock:
            hit = self._suggest_cache.get(key)
        if hit is None:
            hit = self.matcher.suggest(q, limit=limit)
            with self._lock:
                if len(self._suggest_cache) > 512:
                    self._suggest_cache.clear()
                self._suggest_cache[key] = hit
        return {"query": q, "items": hit}

    def search(self, query: str, max_depth: int = 2,
               with_search: bool = False) -> dict[str, Any]:
        builder = self.builder(max_depth)
        result = builder.lookup(query, max_depth=max_depth)
        entities = []
        for e in result.entities:
            if with_search:
                builder.with_search(e)
            entities.append(entity_to_json(e))
        return {
            "query": result.query,
            "matched_kind": result.matched_kind,
            "error": result.error,
            "suggestions": list(result.suggestions),
            "entities": entities,
        }

    def browse(self) -> dict[str, Any]:
        """首页分类浏览：按领域分组列出全部物理量。

        用户要求「不搜索也能到达每一个词条」，所以这里必须覆盖
        registry 里的**全部**条目——自检里也把「有条目没分类」当错误拦下，
        保证不会有条目从首页消失。

        每组内的顺序用中心度（更常用的量在前），而不是字母序。
        """
        groups = []
        total = 0
        for d in self.reg.domains_ordered():
            qs = self.reg.quantities_in_domain(d.id)
            total += len(qs)
            groups.append({
                "id": d.id,
                "zh": d.zh,
                "en": d.en,
                "desc": d.desc,
                "covers": list(d.covers),
                "count": len(qs),
                "items": [
                    {
                        "id": q.id,
                        "zh": q.zh,
                        "symbol": q.symbol,
                        "category": q.category,
                        "dim_si": q.dim.format_si(),
                        "dim_zh": q.dim.describe_zh(),
                        "unit_forms": list(q.unit_forms[:3]),
                        "structure": q.structure,
                    }
                    for q in qs
                ],
            })
        return {
            "groups": groups,
            "total_indexed": total,
            "total_quantities": len(self.reg.quantities),
            # 覆盖完整性由调用方（前端）核对：两者必须相等
            "complete": total == len(self.reg.quantities),
        }

    def meta(self) -> dict[str, Any]:
        """界面初始化用：统计信息 + 可查询的示例。

        示例刻意挑「能体现设计价值」的入口，而不是随便列几个单位：
        牛（等价分支最丰富）、电场强度（N/C 与 V/m 同量纲）、
        力矩（与能量同量纲异构）、转动惯量（矩阵结构）。
        """
        reg = self.reg
        examples = [
            {"q": "牛", "why": "看 SI 基准分解与多条等价写法"},
            {"q": "电场强度", "why": "N/C 与 V/m 其实是同一个量"},
            {"q": "力矩", "why": "与能量量纲相同，物理意义不同"},
            {"q": "转动惯量", "why": "矩阵结构，元素量纲为 kg·m²"},
            {"q": "动量", "why": "由力对时间积分得到"},
            {"q": "电势", "why": "V、J/C、W/A 三种写法"},
            {"q": "弯沉", "why": "不存在的词，看错误提示"},
        ]
        categories: dict[str, int] = {}
        for q in reg.quantities.values():
            categories[q.category] = categories.get(q.category, 0) + 1

        return {
            "counts": {
                "units": len(reg.units),
                "quantities": len(reg.quantities),
                "formulas": len(reg.formulas),
                "algebra_rules": len(reg.algebra_rules),
                "patterns": len(reg.patterns),
                "dims_indexed": len(reg._by_dim),  # noqa: SLF001
                "symbols": len(reg.symbol_glossary),
                "submissions": self.submissions.counts(),
            },
            "categories": categories,
            "examples": examples,
            "base_dimensions": {
                "M": "质量", "L": "长度", "T": "时间",
                "I": "电流", "TH": "温度", "N": "物质的量", "J": "发光强度",
            },
            "edge_kinds": {
                k: {"label": v["label"], "short": v["zh"], "explain": v["explain"]}
                for k, v in EDGE_KINDS.items()
            },
            # 补录种类：界面按这张表决定表单标题与要填哪些字段（页面规范 §5）
            "submission_kinds": {
                k: {"zh": zh} for k, zh in SUBMISSION_KIND_ZH.items()
            },
            # 「没有定义式」的两种合法理由（页面规范 §3）
            "defined_as": {
                "si-base": "SI 基本量，由基本单位定义",
                "umbrella": "总称量纲，没有唯一定义式",
            },
            "pinyin": {
                "chars": len(self.matcher.pinyin_table),
                "enabled": bool(self.matcher.pinyin_table),
            },
        }

    def quantity(self, qid: str, max_depth: int = 2) -> dict[str, Any]:
        q = self.reg.quantities.get(qid)
        if q is None:
            return {"error": f"没有 id 为 {qid!r} 的物理量"}
        view = self.builder(max_depth)._view_for_quantity(q, max_depth)  # noqa: SLF001
        return {"entity": entity_to_json(view)}

    # -------- 公式补录 --------

    def list_submissions(
        self, status: str = "", dim: str = "", expr: str = ""
    ) -> dict[str, Any]:
        items = self.submissions.all()
        if status:
            items = [s for s in items if s.status == status]
        if dim:
            items = [s for s in items if s.target_dim == dim]
        if expr:
            items = [s for s in items if _same_expr(s.expr, expr)]
        return {
            "counts": self.submissions.counts(),
            "load_error": self.submissions.load_error,
            "items": [s.to_json() for s in items],
        }

    def create_submission(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """新建一条补录。只做写入与校验，不做核实（核实属于 agent 会话）。"""
        try:
            s = self.submissions.create(
                query=str(payload.get("query", "")),
                target_dim=str(payload.get("target_dim", "")),
                expr=str(payload.get("expr", "")),
                zh=str(payload.get("zh", "")),
                note=str(payload.get("note", "")),
                source=str(payload.get("source", "")),
                kind=str(payload.get("kind", "")) or "formula",
                target_qid=str(payload.get("target_qid", "")),
            )
        except SubmissionError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "item": s.to_json(),
            "counts": self.submissions.counts(),
        }


class _Handler(BaseHTTPRequestHandler):
    api: Api
    server_version = "DimensionalBrowser/0.1"

    # 静音默认的每请求日志，避免污染控制台
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/meta":
                self._json(self.api.meta())
                return
            if path == "/api/browse":
                self._json(self.api.browse())
                return
            if path == "/api/search":
                q = (query.get("q") or [""])[0]
                depth = _int_arg(query, "depth", 2, 0, 4)
                want_search = (query.get("search") or ["0"])[0] in ("1", "true", "yes")
                self._json(self.api.search(q, depth, want_search))
                return
            if path == "/api/suggest":
                q = (query.get("q") or [""])[0]
                limit = _int_arg(query, "limit", 8, 1, 30)
                self._json(self.api.suggest(q, limit))
                return
            if path == "/api/quantity":
                qid = (query.get("id") or [""])[0]
                depth = _int_arg(query, "depth", 2, 0, 4)
                self._json(self.api.quantity(qid, depth))
                return
            if path == "/api/health":
                self._json({"ok": True})
                return
            if path == "/api/submissions":
                self._json(
                    self.api.list_submissions(
                        status=(query.get("status") or [""])[0],
                        dim=(query.get("dim") or [""])[0],
                        expr=(query.get("expr") or [""])[0],
                    )
                )
                return
            # 静态文件
            self._static(path)
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/submission":
            self._json({"error": "未知端点"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._json({"ok": False, "error": "请求体为空"}, status=400)
            return
        # 上限 256 KB：提交表单最多几千字符，超了说明请求不对
        if length > 256 * 1024:
            self._json({"ok": False, "error": "请求体过大"}, status=413)
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json({"ok": False, "error": f"请求体不是合法 JSON：{exc}"},
                       status=400)
            return
        if not isinstance(payload, Mapping):
            self._json({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
            return
        result = self.api.create_submission(payload)
        self._json(result, status=200 if result.get("ok") else 400)

    # -------- 响应工具 --------

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        # 防目录穿越：解析后必须仍在 WEB_DIR 内
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not Found")
            return
        ctype, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", (ctype or "application/octet-stream") + "; charset=utf-8"
            if (ctype or "").startswith("text/")
            else ctype or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def _same_expr(a: str, b: str) -> bool:
    """宽松比较两个表达式写法是否等价（用于按表达式过滤提交）。

    复用 submissions 模块的规范化，保证「写入时建的键」和
    「查询时算的键」用的是同一套规则——否则会出现存进去了却查不到。
    """
    from .submissions import _norm_expr
    return _norm_expr(a) == _norm_expr(b)


def _int_arg(query: dict[str, list[str]], key: str, default: int,
             lo: int, hi: int) -> int:
    try:
        v = int((query.get(key) or [str(default)])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class Server(ThreadingHTTPServer):
    """带 verbose 开关的服务器，便于测试时静音。"""

    daemon_threads = True

    def __init__(self, addr: tuple[str, int], api: Api, verbose: bool = False):
        self.api = api
        self.verbose = verbose
        handler = type("_BoundHandler", (_Handler,), {"api": api})
        super().__init__(addr, handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path | str | None = None,
    submissions_path: Path | str | None = None,
    verbose: bool = False,
) -> Server:
    """启动服务。返回 Server 实例，调用方负责 serve_forever / shutdown。

    自检不通过就拒绝启动——不允许带着错误数据开界面。
    """
    reg = Registry.load(data_dir) if data_dir else Registry.load()
    problems = reg.selfcheck()
    if problems:
        raise RuntimeError(
            f"数据自检未通过（{len(problems)} 项），拒绝启动界面：\n  - "
            + "\n  - ".join(problems)
        )
    store = SubmissionStore(submissions_path or SubmissionStore().path)
    store.load()
    return Server((host, port), Api(reg, store), verbose=verbose)
