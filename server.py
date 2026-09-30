"""启动量纲关系浏览器。

用法：
    python server.py                # 默认 127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-browser   # 不自动开浏览器

启动前会跑数据自检；不通过就拒绝启动，绝不带着错误数据开界面。
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.api import serve  # noqa: E402
from engine.registry import RegistryError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="量纲关系浏览器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="打印每个请求")
    args = ap.parse_args()

    try:
        server = serve(args.host, args.port, verbose=args.verbose)
    except RegistryError as exc:
        print(f"数据自检失败，拒绝启动：\n{exc}")
        return 1
    except RuntimeError as exc:
        print(str(exc))
        return 1
    except OSError as exc:
        print(f"无法绑定 {args.host}:{args.port} —— {exc}")
        print("换一个端口，或用 --port 指定。")
        return 1

    url = f"http://{args.host}:{args.port}/"
    print(f"量纲关系浏览器已启动：{url}")
    print("按 Ctrl+C 停止。\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
