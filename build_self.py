#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将本 Nuitka 打包工具自身编译为单个 exe 文件。

使用前请确保：
1. 已将 MinGW64 放置在 ./mingw64 目录下
2. 已安装 requirements.txt 中的依赖

用法：
    python build_self.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 打包工具本体不需要这些模块；排除后可避免 anti-bloat 警告并绕过
# 本机 scipy/setuptools 损坏导致的 implicit-imports 插件崩溃。
NOFOLLOW_IMPORTS = (
    "nuitka.tools.testing",
    "nuitka.tools.benchmarks",
    "nuitka.tools.profiling",
    "nuitka.distutils",
    "doctest",
    "unittest",
    "setuptools",
    "scipy",
    "numpy.testing",
    "pytest",
)


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    main_script = base_dir / "main.py"
    mingw_bin = base_dir / "mingw64" / "bin"

    if not main_script.is_file():
        print(f"错误：找不到 {main_script}")
        return 1

    if not mingw_bin.is_dir():
        print(
            f"警告：未找到 MinGW64 目录 {mingw_bin}\n"
            "请先将 MinGW64 解压到 mingw64 文件夹，否则打包可能失败。"
        )

    env = os.environ.copy()
    if mingw_bin.is_dir():
        env["PATH"] = str(mingw_bin) + os.pathsep + env.get("PATH", "")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--windows-console-mode=disable",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        "--mingw64",
        "--output-filename=Nuitka打包工具.exe",
        # 关闭 implicit-imports：编译 GUI 工具本体时不需要，且可规避本机 scipy 损坏问题
        "--disable-plugin=implicit-imports",
        f"--include-data-dir={base_dir / 'mingw64'}=mingw64",
        "--include-package=nuitka",
        "--include-package=ordered_set",
        "--include-package=zstandard",
    ]

    for module_name in NOFOLLOW_IMPORTS:
        cmd.append(f"--nofollow-import-to={module_name}")

    cmd.append(str(main_script))

    print("开始编译 Nuitka 打包工具...")
    print("命令：", " ".join(cmd))
    print()

    result = subprocess.run(cmd, env=env, cwd=str(base_dir))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
