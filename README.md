# 一日一事

本地优先的跨平台桌面待办应用：动态优先级 + 悬浮球 + 系统级提醒 + 周期任务。
界面走「暖中性 + 柔和留白」路线——奶油底、燕麦层级、鼠尾草绿点缀，优先级语义色（红/黄/灰/绿）恒定不随主题变。
完整设计见 `docs/superpowers/specs/2026-10-09-yiriyishi-design.md`。

## 环境准备

需要 Python 3.10+（开发用 3.12）。下面命令按 **PowerShell** 写；用 Git Bash 的话把
`$env:X='y';` 换成前缀 `X=y`、把 `\` 换成 `/` 即可。

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

`[dev]` 额外带 `pytest`、`pyinstaller`；只装运行依赖用 `pip install -e .`。

## 本地调试

```powershell
# 1. 灌一批覆盖各分类的示例任务（逾期/今日/周期/未来/已完成）
$env:PYTHONPATH='src'; .venv\Scripts\python.exe tools\seed_demo.py --datadir .\.devdata

# 2. 启动：独立数据目录 + 控制台 DEBUG 日志
$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m yiriyishi --debug --datadir .\.devdata
```

> `PYTHONPATH=src 命令` 是 bash/zsh 语法，在 PowerShell 里会报「无法将 PYTHONPATH=src 项识别为…」。
> PowerShell 必须写成 `$env:PYTHONPATH='src'; 命令`。

用 `.devdata` 而不是默认目录，是为了让调试数据和你的真实待办互不干扰。
`seed_demo.py` 每次运行都会**追加**一批新任务，想从干净数据开始就先删掉 `.devdata\`。
改了代码要重启进程才生效（SQLite 与调度线程都在启动时建好）。

### 命令行参数

| 参数 | 作用 |
| --- | --- |
| `--datadir PATH` | 自定义数据目录。默认 `%APPDATA%\一日一事`（macOS `~/Library/Application Support/一日一事`，Linux `~/.config/一日一事`） |
| `--debug` | 控制台输出 DEBUG 日志，并在主窗底部状态栏追加当前数据目录 |
| `--minimized` | 启动即收进托盘，不弹主窗 |
| `--smoke` | 构建完整界面后 3 秒自动退出，用于打包产物自检 |

单实例：第二次启动会把消息发给首实例并自行退出（唤起主窗），所以调试时看到「秒退」通常是另一个实例已经在了。

### 调试时看哪里

| 内容 | 位置 |
| --- | --- |
| 日志 | `<datadir>/logs/app.log`（轮转，保留 3 份） |
| 数据库 | `<datadir>/data.db`（SQLite，WAL，另有 `-wal`/`-shm` 临时文件） |
| 配置 | `<datadir>/config.json`（阈值、免打扰、悬浮球位置、窗口几何） |
| 崩溃草稿 | `<datadir>/draft.json`（未保存的编辑，保存或退出后清除） |
| 备份 | `<datadir>/backups/` |

想直接看数据（不用装 sqlite3 CLI，用 venv 里的 Python）：

```powershell
.venv\Scripts\python.exe -c "import sqlite3;[print(r) for r in sqlite3.connect('.devdata/data.db').execute('select title,status,due_time from tasks')]"
```

### 界面核版（不弹窗）

改完样式想确认长什么样，不必启动 GUI：离屏渲染各界面到 PNG。

```powershell
$env:PYTHONPATH='src;tools'; $env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe tools\screenshot.py --out .\.preview
```

产出 `.preview/{main,panel,ball}-{light,dark}.png`。offscreen 环境没有中文字体，
文字会显示成方框——这只影响你看图，不影响真实运行；版式、间距、配色都是准确的。

## 测试

```powershell
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH='src'; .venv\Scripts\python.exe -m pytest -q
```

103 项。其中 GUI 冒烟会在 offscreen 下 `grab()` 真实走一遍 `paintEvent`/delegate，
能抓住「构造期不报错、一绘制就崩」的问题（缺 import、枚举名写错都在这一步暴露过）。
另有两条测试专门校验样式表：属性必须在 Qt 支持的白名单内、QSS 里出现的 `#objectName`
必须真有控件在用——写错属性 Qt 是静默失效的，只能这样兜。

只跑某一块：`.venv\Scripts\python.exe -m pytest tests\test_ui_smoke.py -q -k panel`。
PowerShell 的 `$env:` 变量在当前会话里持续有效，上面那条跑过一次后可以直接接着跑这条。

## 打包 exe

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm build.spec
```

产物 `dist/yiriyishi.exe`（单文件、无控制台窗口，约 54MB —— PySide6 onefile 的正常量级）。
打包后先自检再验收。exe 是 GUI 程序，PowerShell 直接敲不会等它跑完，要用
`Start-Process -Wait` 拿退出码：

```powershell
$p = Start-Process -FilePath .\dist\yiriyishi.exe `
        -ArgumentList '--smoke','--datadir','.pkgtest' -PassThru -Wait
"exit=$($p.ExitCode)"   # 0 = 界面构建通过
```

`build.spec` 里处理了四件容易踩的事：

- `PYZ(a.pure)` 而不是 `a.scripts`，写错会 `AssertionError: Invalid entry passed to PYZ`。
- `assets/` 打进包内，由 `paths.asset_dir()` 经 `sys._MEIPASS` 读取。
- `plyer` / `windows_toasts` / `winrt` 都是运行期动态导入，必须 `collect_all`，否则打包后通知静默失效。
- `excludes` 掉 QtQml/QtQuick，能省十几 MB。

打包失败先确认没有旧进程占着 `dist/`（托盘里的实例不会自己退）：
`Get-Process yiriyishi -ErrorAction SilentlyContinue`。
日志很长，判断成败别只盯结尾几行，直接看退出码：

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm build.spec; "exit=$LASTEXITCODE"
```

## 验收清单

需求文档里的 5 条硬性验收，逐条对应到这里：

1. **临近到期变色**：默认剩余 ≤30 分钟转红 `#E74C3C`、≤8 小时转黄 `#F39C12`（当天任务按小时、
   跨天任务按天两套刻度），阈值在设置里可调。色值由 `theme.LEVEL_COLORS` 单点定义，
   深浅色下语义不变（深色提亮为 `#FFB84D`/`#FF6B5B` 以保证对比度）。
2. **托盘 + 原生通知 + 点击唤起**：Windows 走 `windows-toasts`，点击通知回主窗并滚动定位到该任务；
   macOS/Linux 走 `plyer`，点击定位降级为双击托盘唤起。
3. **开机自启**：Windows 写 HKCU `...\Run`，macOS 写 LaunchAgent，Linux 写 autostart desktop 文件。
   重启后应自动出现在托盘。
4. **关窗不退进程**：`setQuitOnLastWindowClosed(False)` + `closeEvent` 拦截，只有托盘菜单「退出」真正结束。
5. **深色模式完整适配**：设置里切「跟随系统 / 浅色 / 深色」即时热切换，不重建控件；
   暖中性底换成暖炭底，列表、面板、横幅、菜单、日历弹窗都跟着变。

手工验收还需要你确认的：真实通知弹窗与点击定位、重启后的自启、悬浮球拖拽与面板翻转、
免打扰时段（22:00–08:00）不弹窗但到期任务红色闪烁。

## 已知限制

- Windows 之外的通知点击无法回传任务 id（plyer 不支持回调），降级为双击托盘唤起。
- Qt Widgets 常驻内存实测约 140MB（任务管理器 python.exe 口径），达不到需求文档里「空闲 <100MB」那条，其余指标可达。
- 联网同步只留了 `SyncProvider` 接口，本期未实现（本地为主）。
- 悬浮球/面板：**本体实心**，只有形状之外的那圈像素透明，而且透明部分用 `setMask` 从窗口里切掉。
  两个都得要——只用 alpha 会留下一个看不见却吞点击与滚轮的矩形（就是最初的「穿透」），
  只用 mask 则 1-bit 边界把圆边切成台阶。面板卡片由 `paintEvent` 自绘，不依赖样式表。
  代价是没有真投影（Qt QSS 不支持 `box-shadow`），层次靠描边与暖色深浅表达。

## 项目结构

```
src/yiriyishi/
├── main.py              入口：参数、单实例、线程装配
├── paths.py             数据目录与 assets 解析（源码/frozen 同一套）
├── core/
│   ├── models.py        Task / Person / AppSettings / PrioritySettings
│   ├── db.py            SQLite 连接与建表（WAL、外键）
│   ├── repository.py    任务与人员的读写；每线程一条连接
│   ├── priority.py      优先级引擎：重要度 × 时间紧迫度 → 分值与等级
│   ├── recurrence.py    周期任务滚动（daily/weekly/monthly/every_n_days）
│   ├── scheduler.py     30 秒扫描线程，只「决定」提醒，不发送
│   ├── notify.py        系统通知（windows-toasts / plyer 双后端）
│   ├── config.py        config.json 读写
│   ├── autostart.py     三平台开机自启
│   └── log.py           轮转日志
└── ui/
    ├── theme.py         唯一的颜色真相源：token + QSS + QPalette
    ├── main_window.py   主窗装配、托盘、悬浮球接线
    ├── filter_bar.py    分类 chip + 搜索 + 人员 + 排序
    ├── task_list.py     delegate 绘制的任务行（色条/闪烁/悬停）
    ├── task_form.py     详情编辑卡片与新建对话框
    ├── float_panel.py   悬浮球与分类面板（行内完成）
    ├── toast_banner.py  应用内提醒横幅（推迟 15 分钟）
    ├── settings_dialog.py
    └── single_instance.py

tests/                   pytest，含 offscreen GUI 冒烟
tools/seed_demo.py       演示数据
tools/screenshot.py      离屏渲染界面核版
build.spec               PyInstaller 单文件配置
docs/superpowers/specs/  设计文档
```

## 交互速查

| 想做什么 | 怎么做 |
| --- | --- |
| 看全局分类 | 点桌面悬浮球 → 分类面板；再点一次收起 |
| 就地完成一条 | 面板里点该行右侧「完成」，周期任务自动滚下一期，不用回主窗 |
| 定位到某条 | 点面板里的行 / 点系统通知 → 唤起主窗并滚动选中 |
| 推迟 | 顶部横幅「推迟 15 分钟」，不改到期时间，颜色随之后移 |
| 快速新建 | 面板「+ 新任务」，或主窗右上「+ 新任务」 |
| 隐藏一切 | 托盘菜单：暂停提醒 / 隐藏悬浮球 / 退出 |
| 改阈值与免打扰 | 设置 → 优先级；深浅色与悬浮球开关也在这里 |
