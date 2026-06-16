#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nuitka 打包工具 - 图形界面程序
使用 PySide6 构建，支持实时日志输出与后台打包。
"""

from __future__ import annotations

import os
import shlex
import sys
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def get_app_base_dir() -> Path:
    """获取程序根目录（开发模式与 Nuitka 打包后均适用）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_mingw_bin_dir() -> Path:
    """程序自带的 MinGW64 编译器路径。"""
    return get_app_base_dir() / "mingw64" / "bin"


def resolve_nuitka_executable() -> list[str]:
    """
    解析 Nuitka 调用方式。
    开发环境使用 python -m nuitka；打包后优先查找同目录下的 nuitka 入口。
    """
    base_dir = get_app_base_dir()

    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "nuitka"]

    candidates = [
        base_dir / "nuitka.exe",
        base_dir / "nuitka.cmd",
        base_dir / "nuitka",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]

    return [sys.executable, "-m", "nuitka"]


class PackProgressTracker:
    """根据 Nuitka 日志输出推断打包阶段与进度。"""

    STEP_NAMES = ("启动", "分析", "生成", "资源", "编译", "链接", "单文件", "完成")

    def __init__(self, onefile: bool = False):
        self._onefile = onefile
        self._progress = 0
        self._stage = "等待开始"
        self._step_index = 0
        self._compile_hits = 0

    @property
    def step_index(self) -> int:
        return self._step_index

    def reset(self) -> tuple[int, str, int]:
        self._progress = 0
        self._stage = "准备启动"
        self._step_index = 0
        self._compile_hits = 0
        return self._progress, self._stage, self._step_index

    def _apply(self, progress: int, stage: str, step_index: int) -> tuple[int, str, int] | None:
        progress = min(100, max(self._progress, progress))
        changed = (
            progress != self._progress
            or stage != self._stage
            or step_index != self._step_index
        )
        self._progress = progress
        self._stage = stage
        self._step_index = step_index
        if changed:
            return progress, stage, step_index
        return None

    def update(self, line: str) -> tuple[int, str, int] | None:
        lower = line.lower()

        if any(k in lower for k in ("successfully created", "completed successfully")):
            return self._apply(100, "打包完成", 7)

        if self._onefile and any(
            k in lower for k in ("onefile", "single file", "bootstrap", "creating binary")
        ):
            result = self._apply(94, "单文件打包", 6)
            if result:
                return result

        if any(k in lower for k in ("linking", "link.exe", "collect2", "ld.exe", "generating payload")):
            result = self._apply(88, "链接程序", 5)
            if result:
                return result

        if any(
            k in lower
            for k in (
                "compiling ",
                "building ",
                ".c.o",
                "backend c:",
                "scons: building",
                "gcc ",
                "cl.exe",
            )
        ):
            self._compile_hits += 1
            sub = min(84, 42 + self._compile_hits)
            return self._apply(sub, "C 语言编译", 4)

        if any(
            k in lower
            for k in ("running c compilation via scons", "pass 2", "c compilation via scons")
        ):
            result = self._apply(42, "C 语言编译", 4)
            if result:
                return result

        if any(
            k in lower
            for k in ("data composer", "copying data", "copying '", "include-data", "data files")
        ):
            result = self._apply(35, "复制资源文件", 3)
            if result:
                return result

        if any(
            k in lower
            for k in (
                "generating source code",
                "c backend",
                "completed python level",
                "optimizing module",
            )
        ):
            result = self._apply(22, "生成 C 源代码", 2)
            if result:
                return result

        if any(
            k in lower
            for k in (
                "starting python compilation",
                "nuitka-plugins",
                "nuitka-options",
                "pass 1",
                "analysing",
            )
        ):
            result = self._apply(10, "分析 Python 代码", 1)
            if result:
                return result

        if "nuitka" in lower and self._progress < 3:
            return self._apply(3, "准备启动", 0)

        return None


class PackWorker(QThread):
    """在后台线程中执行 Nuitka 打包命令。"""

    log_message = Signal(str, str)  # text, level: info | warning | error
    progress_changed = Signal(int, str, int)  # percent, stage, step_index
    finished = Signal(bool, str)  # success, message

    def __init__(
        self,
        command: list[str],
        env: dict[str, str],
        onefile: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._command = command
        self._env = env
        self._onefile = onefile
        self._tracker = PackProgressTracker(onefile=onefile)
        self._process: subprocess.Popen | None = None
        self._stop_requested = False

    def stop(self) -> None:
        """请求终止打包进程。"""
        self._stop_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def _classify_line(self, line: str, is_stderr: bool) -> str:
        """根据输出内容判断日志级别。"""
        lower = line.lower()
        if any(k in lower for k in ("error", "fatal", "failed", "失败", "错误")):
            return "error"
        if any(k in lower for k in ("warning", "warn", "警告")):
            return "warning"
        if is_stderr:
            return "warning"
        return "info"

    def run(self) -> None:
        try:
            progress, stage, step = self._tracker.reset()
            self.progress_changed.emit(progress, stage, step)

            self.log_message.emit(
                "执行命令：\n" + " ".join(shlex.quote(arg) for arg in self._command),
                "info",
            )

            self._process = subprocess.Popen(
                self._command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env,
                cwd=str(get_app_base_dir()),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            assert self._process.stdout is not None
            assert self._process.stderr is not None

            def _read_stream(stream, is_stderr: bool) -> None:
                for line in stream:
                    if self._stop_requested:
                        break
                    text = line.rstrip("\n\r")
                    if text:
                        self.log_message.emit(
                            text, self._classify_line(text, is_stderr)
                        )
                        result = self._tracker.update(text)
                        if result:
                            self.progress_changed.emit(*result)

            stdout_thread = threading.Thread(
                target=_read_stream, args=(self._process.stdout, False), daemon=True
            )
            stderr_thread = threading.Thread(
                target=_read_stream, args=(self._process.stderr, True), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()

            return_code = self._process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            if self._stop_requested:
                self.finished.emit(False, "打包已被用户中止。")
                return

            if return_code == 0:
                self.progress_changed.emit(100, "打包完成", 7)
                self.finished.emit(True, "打包成功完成！")
            else:
                self.finished.emit(False, f"打包失败，退出代码：{return_code}")

        except FileNotFoundError:
            self.finished.emit(
                False,
                "未找到 Nuitka 或 Python 解释器，请确认已正确安装 Nuitka。",
            )
        except Exception as exc:
            self.finished.emit(False, f"打包过程发生异常：{exc}")


class NuitkaPackerWindow(QWidget):
    """Nuitka 打包工具主窗口。"""

    def __init__(self):
        super().__init__()
        self._worker: PackWorker | None = None
        self._setup_ui()
        self._apply_styles()
        self._connect_signals()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Nuitka 打包工具")
        self.resize(760, 520)
        self.setMinimumSize(680, 460)
        self.setObjectName("mainWindow")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 顶部标题栏
        header = QFrame()
        header.setObjectName("headerBar")
        header.setFixedHeight(56)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 8, 20, 8)
        header_layout.setSpacing(2)

        title_label = QLabel("Nuitka 打包工具")
        title_label.setObjectName("headerTitle")
        subtitle_label = QLabel("可视化配置 · 一键编译 · 实时日志")
        subtitle_label.setObjectName("headerSubtitle")
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        root_layout.addWidget(header)

        # 主内容区
        content = QWidget()
        content.setObjectName("contentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(10)

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("mainTabs")
        self.tab_widget.addTab(self._create_basic_tab(), "基本设置")
        self.tab_widget.addTab(self._create_resource_tab(), "资源管理")
        self.tab_widget.addTab(self._create_options_tab(), "打包选项")
        self.tab_widget.addTab(self._create_log_tab(), "打包日志")
        content_layout.addWidget(self.tab_widget, stretch=1)

        # 打包进度（始终可见）
        progress_panel = QFrame()
        progress_panel.setObjectName("progressPanel")
        progress_layout = QVBoxLayout(progress_panel)
        progress_layout.setContentsMargins(4, 0, 4, 0)
        progress_layout.setSpacing(8)

        self.stage_label = QLabel("等待开始打包…")
        self.stage_label.setObjectName("stageLabel")

        steps_row = QHBoxLayout()
        steps_row.setSpacing(6)
        self.step_labels: list[QLabel] = []
        for name in PackProgressTracker.STEP_NAMES:
            step_lbl = QLabel(name)
            step_lbl.setObjectName("stepPending")
            step_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step_lbl.setMinimumWidth(36)
            self.step_labels.append(step_lbl)
            steps_row.addWidget(step_lbl, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("packProgressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setFixedHeight(22)

        progress_layout.addWidget(self.stage_label)
        progress_layout.addLayout(steps_row)
        progress_layout.addWidget(self.progress_bar)
        content_layout.addWidget(progress_panel)

        # 底部控制栏（始终可见）
        control_bar = QFrame()
        control_bar.setObjectName("controlBar")
        control_layout = QHBoxLayout(control_bar)
        control_layout.setContentsMargins(4, 4, 4, 0)
        control_layout.setSpacing(10)

        self.start_btn = QPushButton("开始打包")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn = QPushButton("停止打包")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("secondaryBtn")
        self.clear_log_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.start_btn.setMinimumHeight(36)
        self.stop_btn.setMinimumHeight(36)
        self.clear_log_btn.setMinimumHeight(36)
        self.start_btn.setMinimumWidth(110)

        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.clear_log_btn)
        control_layout.addStretch()
        content_layout.addWidget(control_bar)

        root_layout.addWidget(content, stretch=1)

    def _create_basic_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("basicScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        entry_group = QGroupBox("入口文件")
        entry_layout = QHBoxLayout(entry_group)
        self.entry_edit = QLineEdit()
        self.entry_edit.setPlaceholderText("请选择要打包的 Python 入口文件 (.py)")
        self.entry_browse_btn = QPushButton("浏览")
        self.entry_browse_btn.setObjectName("secondaryBtn")
        self.entry_browse_btn.setFixedWidth(88)
        self.entry_browse_btn.setCursor(Qt.PointingHandCursor)
        entry_layout.addWidget(self.entry_edit)
        entry_layout.addWidget(self.entry_browse_btn)
        layout.addWidget(entry_group)

        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(12)

        dir_label = QLabel("输出目录")
        dir_label.setObjectName("fieldLabel")
        output_layout.addWidget(dir_label)
        output_dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("留空则使用 Nuitka 默认输出位置")
        self.output_dir_browse_btn = QPushButton("浏览")
        self.output_dir_browse_btn.setObjectName("secondaryBtn")
        self.output_dir_browse_btn.setFixedWidth(88)
        self.output_dir_browse_btn.setCursor(Qt.PointingHandCursor)
        output_dir_row.addWidget(self.output_dir_edit)
        output_dir_row.addWidget(self.output_dir_browse_btn)
        output_layout.addLayout(output_dir_row)

        name_label = QLabel("输出文件名")
        name_label.setObjectName("fieldLabel")
        output_layout.addWidget(name_label)
        self.output_edit = QLineEdit("我的程序.exe")
        output_layout.addWidget(self.output_edit)

        icon_label = QLabel("程序图标")
        icon_label.setObjectName("fieldLabel")
        output_layout.addWidget(icon_label)
        icon_row = QHBoxLayout()
        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("未选择图标时将使用默认图标")
        self.icon_browse_btn = QPushButton("浏览")
        self.icon_browse_btn.setObjectName("secondaryBtn")
        self.icon_browse_btn.setFixedWidth(88)
        self.icon_browse_btn.setCursor(Qt.PointingHandCursor)
        icon_row.addWidget(self.icon_edit)
        icon_row.addWidget(self.icon_browse_btn)
        output_layout.addLayout(icon_row)

        layout.addWidget(output_group)

        scroll.setWidget(content)
        return scroll

    def _create_resource_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        resource_group = QGroupBox("资源列表")
        resource_layout = QVBoxLayout(resource_group)

        self.resource_table = QTableWidget(0, 3)
        self.resource_table.setHorizontalHeaderLabels(["类型", "源路径", "目标路径"])
        self.resource_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.resource_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.resource_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.resource_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.resource_table.setAlternatingRowColors(True)
        resource_layout.addWidget(self.resource_table)

        resource_btn_layout = QHBoxLayout()
        self.add_file_btn = QPushButton("添加文件")
        self.add_file_btn.setObjectName("secondaryBtn")
        self.add_file_btn.setCursor(Qt.PointingHandCursor)
        self.add_folder_btn = QPushButton("添加文件夹")
        self.add_folder_btn.setObjectName("secondaryBtn")
        self.add_folder_btn.setCursor(Qt.PointingHandCursor)
        self.remove_resource_btn = QPushButton("删除选中")
        self.remove_resource_btn.setObjectName("secondaryBtn")
        self.remove_resource_btn.setCursor(Qt.PointingHandCursor)
        resource_btn_layout.addWidget(self.add_file_btn)
        resource_btn_layout.addWidget(self.add_folder_btn)
        resource_btn_layout.addWidget(self.remove_resource_btn)
        resource_btn_layout.addStretch()
        resource_layout.addLayout(resource_btn_layout)
        layout.addWidget(resource_group)
        return tab

    def _make_option_checkbox(self, title: str, description: str) -> QCheckBox:
        """创建带说明文字的选项复选框，支持自动换行。"""
        chk = QCheckBox(f"{title}\n{description}")
        chk.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return chk

    def _create_options_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("optionsScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        cpu_count = os.cpu_count() or 4
        jobs_group = QGroupBox("并行编译")
        jobs_layout = QHBoxLayout(jobs_group)
        jobs_layout.setSpacing(12)

        jobs_label = QLabel("编译线程数 (--jobs)：")
        self.jobs_spin = QSpinBox()
        self.jobs_spin.setRange(1, max(cpu_count, 1))
        self.jobs_spin.setValue(cpu_count)
        self.jobs_spin.setToolTip(
            "设置 Nuitka 编译时使用的 CPU 核心数，数值越大编译越快，但占用资源越多"
        )
        cpu_hint = QLabel(f"本机逻辑处理器：{cpu_count} 核")
        cpu_hint.setObjectName("hintLabel")

        jobs_layout.addWidget(jobs_label)
        jobs_layout.addWidget(self.jobs_spin)
        jobs_layout.addWidget(cpu_hint)
        jobs_layout.addStretch()
        layout.addWidget(jobs_group)

        options_group = QGroupBox("编译选项")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(10)

        self.chk_standalone = self._make_option_checkbox(
            "独立模式 (--standalone)",
            "生成包含所有依赖的独立文件夹，可在无 Python 环境的电脑上运行",
        )
        self.chk_onefile = self._make_option_checkbox(
            "单文件模式 (--onefile)",
            "在独立模式基础上，将整个程序打包成单个 .exe 文件",
        )
        self.chk_disable_console = self._make_option_checkbox(
            "隐藏控制台 (--windows-console-mode=disable)",
            "运行时不显示黑色命令行窗口，适用于 GUI 程序",
        )
        self.chk_pyside6 = self._make_option_checkbox(
            "启用 PySide6 插件 (--enable-plugin=pyside6)",
            "如果程序使用 PySide6 作为界面库，必须勾选此项",
        )
        self.chk_pyqt5 = self._make_option_checkbox(
            "启用 PyQt5 插件 (--enable-plugin=pyqt5)",
            "如果程序使用 PyQt5 作为界面库，必须勾选此项",
        )
        self.chk_lto = self._make_option_checkbox(
            "启用链接时优化 (--lto)",
            "启用后可以减小生成文件体积，但编译时间会变长",
        )
        self.chk_assume_yes = self._make_option_checkbox(
            "自动确认下载 (--assume-yes-for-downloads)",
            "自动确认下载编译器依赖，避免交互提示",
        )
        self.chk_module = self._make_option_checkbox(
            "生成 .pyd 扩展模块 (--module)",
            "将代码编译为扩展模块而非可执行程序",
        )

        for chk in (
            self.chk_standalone,
            self.chk_onefile,
            self.chk_disable_console,
            self.chk_pyside6,
            self.chk_pyqt5,
            self.chk_lto,
            self.chk_assume_yes,
            self.chk_module,
        ):
            options_layout.addWidget(chk)

        self.chk_standalone.setChecked(True)
        self.chk_assume_yes.setChecked(True)
        layout.addWidget(options_group)

        advanced_group = QGroupBox("高级参数")
        advanced_layout = QVBoxLayout(advanced_group)
        self.advanced_edit = QTextEdit()
        self.advanced_edit.setPlaceholderText(
            "在此输入额外参数，如：--nofollow-import-to=numpy"
        )
        self.advanced_edit.setMinimumHeight(100)
        advanced_layout.addWidget(self.advanced_edit)
        layout.addWidget(advanced_group)

        scroll.setWidget(content)
        return scroll

    def _create_log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(0)

        self.log_edit = QTextEdit()
        self.log_edit.setObjectName("logEdit")
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 10))
        self.log_edit.setPlaceholderText("打包日志将在此实时显示…")
        layout.addWidget(self.log_edit)
        return tab

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #mainWindow {
                background-color: #eef4fc;
            }
            #headerBar {
                background-color: #1565c0;
                border: none;
            }
            #headerTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 700;
                background: transparent;
            }
            #headerSubtitle {
                color: #bbdefb;
                font-size: 12px;
                font-weight: 400;
                background: transparent;
            }
            #contentArea {
                background-color: #eef4fc;
            }
            #controlBar {
                background-color: transparent;
                border-top: 1px solid #c5daf5;
                padding-top: 4px;
            }
            #progressPanel {
                background-color: #ffffff;
                border: 1px solid #c5daf5;
                border-radius: 10px;
                padding: 10px 12px;
            }
            #stageLabel {
                color: #1565c0;
                font-size: 13px;
                font-weight: 700;
            }
            #stageFailed {
                color: #c62828;
                font-size: 13px;
                font-weight: 700;
            }
            #stepPending {
                color: #90a4bf;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 2px;
                border-radius: 6px;
                background-color: #f5f9ff;
            }
            #stepActive {
                color: #ffffff;
                font-size: 11px;
                font-weight: 700;
                padding: 4px 2px;
                border-radius: 6px;
                background-color: #1976d2;
            }
            #stepDone {
                color: #1565c0;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 2px;
                border-radius: 6px;
                background-color: #bbdefb;
            }
            #packProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #e3f0ff;
                text-align: center;
                color: #0d47a1;
                font-weight: 700;
            }
            #packProgressBar::chunk {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #42a5f5, stop:1 #1565c0
                );
                border-radius: 6px;
            }
            QTabWidget#mainTabs {
                background-color: transparent;
            }
            QTabWidget#mainTabs::pane {
                border: 1px solid #c5daf5;
                border-radius: 10px;
                background-color: #ffffff;
                top: -1px;
            }
            QTabWidget#mainTabs::tab-bar {
                alignment: left;
            }
            QTabBar::tab {
                background-color: #e3f0ff;
                color: #5a7a9a;
                border: 1px solid #c5daf5;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 8px 18px;
                margin-right: 4px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1565c0;
                border-bottom: 2px solid #1976d2;
            }
            QTabBar::tab:hover:!selected {
                background-color: #bbdefb;
                color: #1565c0;
            }
            #optionsScroll {
                background-color: transparent;
            }
            #optionsScroll > QWidget > QWidget {
                background-color: transparent;
            }
            #basicScroll {
                background-color: transparent;
            }
            #basicScroll > QWidget > QWidget {
                background-color: transparent;
            }
            #fieldLabel {
                color: #1565c0;
                font-size: 12px;
                font-weight: 600;
                margin-bottom: 2px;
            }
            QWidget {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
                color: #1e3a5f;
            }
            QGroupBox {
                font-weight: 600;
                font-size: 13px;
                border: 1px solid #c5daf5;
                border-radius: 10px;
                margin-top: 16px;
                padding: 16px 14px 12px 14px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #1565c0;
            }
            QLabel {
                color: #2c5282;
                background: transparent;
            }
            #hintLabel {
                color: #5a7a9a;
                font-size: 12px;
            }
            QSpinBox {
                border: 1px solid #b8d4f5;
                border-radius: 8px;
                padding: 6px 10px;
                background-color: #ffffff;
                color: #1e3a5f;
                min-width: 72px;
            }
            QSpinBox:hover {
                border: 1px solid #64b5f6;
            }
            QSpinBox:focus {
                border: 2px solid #1976d2;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 20px;
                border: none;
                background-color: #e3f0ff;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #bbdefb;
            }
            QSpinBox::up-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #1565c0;
                width: 0;
                height: 0;
            }
            QSpinBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #1565c0;
                width: 0;
                height: 0;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #b8d4f5;
                border-radius: 8px;
                padding: 8px 12px;
                background-color: #ffffff;
                color: #1e3a5f;
                selection-background-color: #90caf9;
                selection-color: #0d47a1;
            }
            QLineEdit:hover, QTextEdit:hover {
                border: 1px solid #64b5f6;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 2px solid #1976d2;
                padding: 7px 11px;
            }
            QLineEdit:disabled, QTextEdit:disabled {
                background-color: #f5f9ff;
                color: #90a4bf;
            }
            #logEdit {
                background-color: #f8fbff;
                border: 1px solid #c5daf5;
            }
            QTableWidget {
                border: 1px solid #c5daf5;
                border-radius: 8px;
                background-color: #ffffff;
                gridline-color: #dce8f8;
                alternate-background-color: #f5f9ff;
                selection-background-color: #bbdefb;
                selection-color: #0d47a1;
            }
            QTableWidget::item {
                padding: 6px 8px;
            }
            QHeaderView::section {
                background-color: #e3f0ff;
                color: #1565c0;
                padding: 8px 10px;
                border: none;
                border-bottom: 2px solid #90caf9;
                font-weight: 600;
            }
            QCheckBox {
                spacing: 10px;
                color: #2c5282;
                padding: 4px 0;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #90caf9;
                background-color: #ffffff;
                subcontrol-position: top left;
                subcontrol-origin: margin;
                margin-top: 2px;
            }
            QCheckBox::indicator:hover {
                border: 2px solid #42a5f5;
            }
            QCheckBox::indicator:checked {
                background-color: #1976d2;
                border: 2px solid #1976d2;
                image: none;
            }
            QCheckBox::indicator:checked:hover {
                background-color: #1565c0;
                border: 2px solid #1565c0;
            }
            QPushButton#primaryBtn {
                background-color: #1976d2;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-weight: 700;
                font-size: 14px;
            }
            QPushButton#primaryBtn:hover {
                background-color: #1565c0;
            }
            QPushButton#primaryBtn:pressed {
                background-color: #0d47a1;
            }
            QPushButton#primaryBtn:disabled {
                background-color: #b0bec5;
                color: #eceff1;
            }
            QPushButton#secondaryBtn {
                background-color: #ffffff;
                color: #1565c0;
                border: 1px solid #90caf9;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton#secondaryBtn:hover {
                background-color: #e3f2fd;
                border: 1px solid #42a5f5;
            }
            QPushButton#secondaryBtn:pressed {
                background-color: #bbdefb;
            }
            QPushButton#secondaryBtn:disabled {
                background-color: #f5f9ff;
                color: #90a4bf;
                border: 1px solid #dce8f8;
            }
            QPushButton#stopBtn {
                background-color: #ffffff;
                color: #1565c0;
                border: 2px solid #1976d2;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: 700;
                font-size: 14px;
            }
            QPushButton#stopBtn:hover {
                background-color: #e3f2fd;
            }
            QPushButton#stopBtn:pressed {
                background-color: #bbdefb;
            }
            QPushButton#stopBtn:disabled {
                background-color: #f5f9ff;
                color: #b0bec5;
                border: 2px solid #dce8f8;
            }
            QScrollBar:vertical {
                background: #f5f9ff;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #90caf9;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #64b5f6;
            }
            QScrollBar:horizontal {
                background: #f5f9ff;
                height: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal {
                background: #90caf9;
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0;
                height: 0;
            }
            QMessageBox {
                background-color: #ffffff;
            }
            """
        )

    def _connect_signals(self) -> None:
        self.entry_browse_btn.clicked.connect(self._browse_entry_file)
        self.add_file_btn.clicked.connect(self._add_resource_file)
        self.add_folder_btn.clicked.connect(self._add_resource_folder)
        self.remove_resource_btn.clicked.connect(self._remove_resource)
        self.icon_browse_btn.clicked.connect(self._browse_icon_file)
        self.output_dir_browse_btn.clicked.connect(self._browse_output_dir)
        self.start_btn.clicked.connect(self._start_packing)
        self.stop_btn.clicked.connect(self._stop_packing)
        self.clear_log_btn.clicked.connect(self.log_edit.clear)
        self._reset_progress()

        # 单文件模式通常需要独立模式
        self.chk_onefile.toggled.connect(self._on_onefile_toggled)

    def _reset_progress(self) -> None:
        self.progress_bar.setValue(0)
        self.stage_label.setObjectName("stageLabel")
        self.stage_label.setText("等待开始打包…")
        self.stage_label.style().unpolish(self.stage_label)
        self.stage_label.style().polish(self.stage_label)
        onefile = self.chk_onefile.isChecked()
        for i, lbl in enumerate(self.step_labels):
            lbl.setVisible(i != 6 or onefile)
            lbl.setObjectName("stepPending")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _update_progress(self, value: int, stage: str, step_index: int) -> None:
        self.progress_bar.setValue(value)
        self.stage_label.setObjectName("stageLabel")
        self.stage_label.setText(f"当前阶段：{stage}")
        self.stage_label.style().unpolish(self.stage_label)
        self.stage_label.style().polish(self.stage_label)

        onefile = self.chk_onefile.isChecked()
        for i, lbl in enumerate(self.step_labels):
            if i == 6 and not onefile:
                lbl.setVisible(False)
                continue
            lbl.setVisible(True)
            if i < step_index:
                obj_name = "stepDone"
            elif i == step_index:
                obj_name = "stepActive"
            else:
                obj_name = "stepPending"
            lbl.setObjectName(obj_name)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _on_onefile_toggled(self, checked: bool) -> None:
        if checked and not self.chk_standalone.isChecked():
            self.chk_standalone.setChecked(True)
        if hasattr(self, "step_labels"):
            self.step_labels[6].setVisible(checked)

    def _browse_entry_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Python 入口文件", "", "Python 文件 (*.py)"
        )
        if path:
            self.entry_edit.setText(path)

    def _browse_icon_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图标文件", "", "图标文件 (*.ico)"
        )
        if path:
            self.icon_edit.setText(path)

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_dir_edit.setText(str(Path(folder).resolve()))

    def _is_resource_duplicate(self, src_path: str) -> bool:
        for row in range(self.resource_table.rowCount()):
            if self.resource_table.item(row, 1).text() == src_path:
                return True
        return False

    def _add_resource_row(self, resource_type: str, src_path: str, dst_path: str) -> None:
        row = self.resource_table.rowCount()
        self.resource_table.insertRow(row)

        type_item = QTableWidgetItem(resource_type)
        type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
        self.resource_table.setItem(row, 0, type_item)
        self.resource_table.setItem(row, 1, QTableWidgetItem(src_path))
        self.resource_table.setItem(row, 2, QTableWidgetItem(dst_path))

    def _add_resource_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要打包的文件", "", "所有文件 (*.*)"
        )
        if not paths:
            return

        for path in paths:
            src_path = str(Path(path).resolve())
            if self._is_resource_duplicate(src_path):
                QMessageBox.information(self, "提示", f"该资源已在列表中：\n{src_path}")
                continue

            dst_name = Path(path).name
            self._add_resource_row("文件", src_path, dst_name)

    def _add_resource_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择资源文件夹")
        if not folder:
            return

        src_path = str(Path(folder).resolve())
        if self._is_resource_duplicate(src_path):
            QMessageBox.information(self, "提示", "该文件夹已在列表中。")
            return

        dst_name = Path(folder).name
        self._add_resource_row("文件夹", src_path, dst_name)

    def _remove_resource(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.resource_table.selectedIndexes()},
            reverse=True,
        )
        for row in selected_rows:
            self.resource_table.removeRow(row)

    def _append_log(self, text: str, level: str = "info") -> None:
        colors = {
            "info": QColor("#1e3a5f"),
            "warning": QColor("#e65100"),
            "error": QColor("#c62828"),
            "success": QColor("#1565c0"),
        }
        fmt = QTextCharFormat()
        fmt.setForeground(colors.get(level, colors["info"]))

        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text + "\n", fmt)
        self.log_edit.setTextCursor(cursor)
        self.log_edit.ensureCursorVisible()

    def _validate_inputs(self) -> str | None:
        entry = self.entry_edit.text().strip()
        if not entry:
            return "请选择入口 Python 文件。"
        if not Path(entry).is_file():
            return f"入口文件不存在：{entry}"
        if not entry.lower().endswith(".py"):
            return "入口文件必须是 .py 文件。"

        icon = self.icon_edit.text().strip()
        if icon and not Path(icon).is_file():
            return f"图标文件不存在：{icon}"
        if icon and not icon.lower().endswith(".ico"):
            return "图标文件必须是 .ico 格式。"

        output_name = self.output_edit.text().strip()
        if not output_name:
            return "请填写输出文件名。"

        output_dir = self.output_dir_edit.text().strip()
        if output_dir:
            output_path = Path(output_dir)
            if output_path.exists() and not output_path.is_dir():
                return f"输出路径必须是文件夹：{output_dir}"
            if not output_path.exists():
                try:
                    output_path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    return f"无法创建输出目录：{output_dir}\n{exc}"

        mingw_bin = get_mingw_bin_dir()
        if not mingw_bin.is_dir():
            return (
                f"未找到程序自带的 MinGW64 编译器目录：{mingw_bin}\n"
                "请将 MinGW64 放置在程序目录下的 mingw64 文件夹中。"
            )

        return None

    def _build_command(self) -> list[str]:
        """根据界面选项构建 Nuitka 命令。"""
        cmd = resolve_nuitka_executable().copy()

        if self.chk_standalone.isChecked():
            cmd.append("--standalone")
        if self.chk_onefile.isChecked():
            cmd.append("--onefile")
        if self.chk_disable_console.isChecked():
            cmd.append("--windows-console-mode=disable")
        if self.chk_pyside6.isChecked():
            cmd.append("--enable-plugin=pyside6")
        if self.chk_pyqt5.isChecked():
            cmd.append("--enable-plugin=pyqt5")
        if self.chk_lto.isChecked():
            cmd.append("--lto=yes")
        if self.chk_assume_yes.isChecked():
            cmd.append("--assume-yes-for-downloads")
        if self.chk_module.isChecked():
            cmd.append("--module")

        cmd.append(f"--jobs={self.jobs_spin.value()}")

        # 使用程序自带的 MinGW64
        cmd.append("--mingw64")

        # 资源文件 / 资源文件夹
        for row in range(self.resource_table.rowCount()):
            resource_type = self.resource_table.item(row, 0).text()
            src = self.resource_table.item(row, 1).text()
            dst = self.resource_table.item(row, 2).text()
            if resource_type == "文件":
                cmd.append(f"--include-data-files={src}={dst}")
            else:
                cmd.append(f"--include-data-dir={src}={dst}")

        # 图标
        icon = self.icon_edit.text().strip()
        if icon:
            cmd.append(f"--windows-icon-from-ico={icon}")

        # 输出目录
        output_dir = self.output_dir_edit.text().strip()
        if output_dir:
            cmd.append(f"--output-dir={Path(output_dir).resolve()}")

        # 输出文件名
        output_name = self.output_edit.text().strip()
        cmd.append(f"--output-filename={output_name}")

        # 高级参数
        advanced = self.advanced_edit.toPlainText().strip()
        if advanced:
            cmd.extend(shlex.split(advanced, posix=False))

        # 入口文件（放在最后）
        cmd.append(self.entry_edit.text().strip())

        return cmd

    def _build_env(self) -> dict[str, str]:
        """构建子进程环境变量，将自带 MinGW64 加入 PATH。"""
        env = os.environ.copy()
        mingw_bin = str(get_mingw_bin_dir())
        env["PATH"] = mingw_bin + os.pathsep + env.get("PATH", "")
        return env

    def _set_packing_state(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.entry_browse_btn.setEnabled(not running)
        self.add_file_btn.setEnabled(not running)
        self.add_folder_btn.setEnabled(not running)
        self.remove_resource_btn.setEnabled(not running)
        self.icon_browse_btn.setEnabled(not running)
        self.output_dir_browse_btn.setEnabled(not running)
        self.output_dir_edit.setEnabled(not running)
        self.jobs_spin.setEnabled(not running)

    def _start_packing(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "提示", "打包任务正在进行中。")
            return

        error = self._validate_inputs()
        if error:
            QMessageBox.warning(self, "输入错误", error)
            return

        command = self._build_command()
        env = self._build_env()

        self._append_log("=" * 60, "info")
        self._append_log("开始打包...", "success")
        self.tab_widget.setCurrentIndex(3)
        self._reset_progress()
        self._update_progress(0, "准备启动", 0)
        self._set_packing_state(True)

        self._worker = PackWorker(
            command,
            env,
            onefile=self.chk_onefile.isChecked(),
            parent=self,
        )
        self._worker.log_message.connect(self._append_log)
        self._worker.progress_changed.connect(self._update_progress)
        self._worker.finished.connect(self._on_pack_finished)
        self._worker.start()

    def _stop_packing(self) -> None:
        if self._worker and self._worker.isRunning():
            self._append_log("正在停止打包进程...", "warning")
            self._worker.stop()

    def _on_pack_finished(self, success: bool, message: str) -> None:
        self._set_packing_state(False)
        level = "success" if success else "error"
        self._append_log(message, level)

        if success:
            self.stage_label.setObjectName("stageLabel")
            self._update_progress(100, "打包完成", 7)
            QMessageBox.information(self, "打包完成", message)
        else:
            self.stage_label.setObjectName("stageFailed")
            self.stage_label.setText(f"打包失败：{message}")
            self.stage_label.style().unpolish(self.stage_label)
            self.stage_label.style().polish(self.stage_label)
            QMessageBox.critical(self, "打包失败", message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Nuitka 打包工具")
    app.setStyle("Fusion")

    window = NuitkaPackerWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
