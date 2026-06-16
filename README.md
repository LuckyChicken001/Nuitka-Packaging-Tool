# Nuitka 打包工具

一款基于 **Python + PySide6** 的 Nuitka 图形化打包工具，面向 Windows 用户。无需记忆冗长的命令行参数，通过可视化界面即可完成 Python 程序编译打包。

[![GitHub Repo](https://img.shields.io/badge/GitHub-Nuitka--Packaging--Tool-181717?logo=github)](https://github.com/LuckyChicken001/Nuitka-Packaging-Tool)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-green)
![Nuitka](https://img.shields.io/badge/Nuitka-2.0%2B-orange)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)
---

## ✨ 功能特性

- **可视化配置** — 入口文件、输出目录、输出文件名、图标等一键设置
- **资源管理** — 支持添加单个文件或整个文件夹（`--include-data-files` / `--include-data-dir`）
- **常用选项勾选** — 独立模式、单文件、隐藏控制台、PySide6/PyQt5 插件、LTO 等
- **并行编译** — 可自定义 `--jobs` 线程数，默认使用本机 CPU 核心数
- **高级参数** — 支持手动输入额外 Nuitka 命令行参数
- **实时日志** — 打包过程输出实时显示，错误 / 警告 / 成功分色展示
- **进度追踪** — 进度条 + 分阶段指示（分析 → 生成 → 编译 → 链接 → 完成）
- **内置 MinGW64** — 可打包自带编译器，分发后开箱即用（需自行放置 `mingw64` 目录）
- **分页界面** — 基本设置 / 资源管理 / 打包选项 / 打包日志，布局清晰
- **后台打包** — 使用 `QThread` 执行，界面不卡顿，可随时停止

---


## 📋 环境要求

- Windows 10 / 11
- Python 3.9 及以上（推荐 3.11）
- [Nuitka](https://nuitka.net/) 2.0+
- [PySide6](https://pypi.org/project/PySide6/) 6.5+

---

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/LuckyChicken001/Nuitka-Packaging-Tool.git
cd Nuitka-Packaging-Tool
```
### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 准备 MinGW64（可选，推荐）

将 MinGW64 解压到项目根目录下的 `mingw64` 文件夹：

```
Nuitka-Packaging-Tool/
├── main.py
├── mingw64/
│   └── bin/
│       ├── gcc.exe
│       └── ...
└── ...
```
> MinGW64 体积较大，未包含在仓库中。可从 [WinLibs](https://winlibs.com/) 等渠道下载。

### 4. 运行程序

```bash
python main.py
```

---

## 📖 使用说明

### 基本设置

| 配置项 | 说明 |
|--------|------|
| 入口文件 | 选择要打包的 `.py` 主程序 |
| 输出目录 | 生成文件的存放路径（留空则使用 Nuitka 默认位置） |
| 输出文件名 | 如 `我的程序.exe` |
| 程序图标 | 可选，`.ico` 格式 |

### 资源管理

- **添加文件** — 打包单个资源文件（配置、图片等）
- **添加文件夹** — 打包整个目录，目标路径默认为文件夹名称

### 打包选项

勾选所需编译选项，每个选项均附带中文说明。常用组合：

- GUI 程序：`独立模式` + `单文件` + `隐藏控制台` + `PySide6 插件`
- 控制台工具：`独立模式` + `单文件`

### 打包自身为 exe

```bash
python build_self.py
```

编译完成后会在项目目录生成 `Nuitka打包工具.exe`（首次编译耗时较长，请耐心等待）。

---

## 📁 项目结构

```
Nuitka-Packaging-Tool/
├── main.py              # 主程序（GUI）
├── build_self.py        # 将本工具编译为 exe 的脚本
├── requirements.txt     # Python 依赖
├── README.md
└── mingw64/             # MinGW64 编译器（需自行下载，不纳入 Git）
```
---

## ⚙️ 技术实现

- **界面框架**：PySide6（纯代码构建，无 `.ui` 文件）
- **打包引擎**：Nuitka，通过 `subprocess.Popen` 调用
- **异步处理**：`QThread` + 双线程分别读取 stdout / stderr
- **进度推断**：解析 Nuitka 日志关键词，映射到编译阶段
- **编译器路径**：自动指向 `./mingw64/bin` 并加入 `PATH`

---

## ❓ 常见问题

**Q：打包时提示找不到 MinGW64？**  
A：请将 MinGW64 解压到项目目录下的 `mingw64` 文件夹，或取消勾选 `--mingw64` 并使用系统已安装的编译器。

**Q：资源文件夹怎么添加？**  
A：在「资源管理」页点击「添加文件夹」。程序本体仍需在「基本设置」中指定入口 `.py` 文件。

**Q：`build_self.py` 编译失败，提示 scipy 相关错误？**  
A：可执行 `pip uninstall scipy -y` 后重试；脚本已排除测试模块并禁用 `implicit-imports` 插件以规避此问题。

**Q：编译很慢正常吗？**  
A：正常。PySide6 + onefile + 内置 Nuitka 首次编译可能需要 20–40 分钟。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支（`git checkout -b feature/awesome-feature`）
3. 提交更改（`git commit -m 'Add awesome feature'`）
4. 推送分支（`git push origin feature/awesome-feature`）
5. 发起 Pull Request

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源。

---

## 🙏 致谢

- [Nuitka](https://nuitka.net/) — Python 编译器
- [PySide6](https://doc.qt.io/qtforpython/) — Qt for Python
- [MinGW-w64](https://www.mingw-w64.org/) — Windows 下的 GCC 工具链

---

如果这个项目对你有帮助，欢迎点个 ⭐ [Star](https://github.com/LuckyChicken001/Nuitka-Packaging-Tool)！
