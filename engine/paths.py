"""路径解析：区分「随程序分发的只读资源」与「用户可写数据」。

为什么需要这一层
----------------
用户明确说项目最终要能**打包成软件分发 / 上传 GitHub / 做成公网站点**。
一旦用 PyInstaller 之类打成一个 exe，`Path(__file__).resolve().parent.parent`
就指向解包后的**临时目录**，于是两类东西会同时出问题：

  1. `data/` 与 `web/` —— 它们是随程序走的只读资源，必须能定位到解包目录
     （PyInstaller 把它放在 `sys._MEIPASS`）。
  2. `submissions.json` —— 它是**用户数据**，绝不能写在临时目录里：
     exe 一退出临时目录就没了，用户提交的内容会连同目录一起被删掉。

开发状态（直接跑源码）下两者是同一个目录，所以这个区分在开发时看不出来，
只有打包之后才暴露。与其等到打包时才发现，不如现在就分开写。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在打包后的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源（`data/`、`web/`）的根目录。"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        # 非 onefile 打包：资源就在 exe 旁边
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return resource_root() / "data"


def web_dir() -> Path:
    return resource_root() / "web"


def user_data_dir() -> Path:
    """用户可写数据的目录。

    打包后刻意落在**当前工作目录**而不是 exe 旁边：exe 可能装在
    `C:\\Program Files`（无写权限），而 cwd 是用户自己选的。
    开发状态下就是项目根目录，与既有行为完全一致。
    """
    if is_frozen():
        return Path.cwd()
    return Path(__file__).resolve().parent.parent


def submissions_path() -> Path:
    return user_data_dir() / "submissions.json"
