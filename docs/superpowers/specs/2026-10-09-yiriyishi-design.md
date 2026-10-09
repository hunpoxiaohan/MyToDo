# 一日一事 · 设计规格

状态：待评审
日期：2026-10-09
本版取代会话中第一版设计（新增人员、周期任务、悬浮球三项需求，优先级算法改为双尺度）

## 1. 目标与定位

一款 Windows 优先、跨平台的桌面待办应用。核心卖点不是静态标签，而是**由完成日期自动推导的优先级**、**桌面悬浮球一眼看清任务分类**，以及**系统级到期提醒**。数据完全本地存储，可离线使用，联网能力只做备份与预留接口。

验收时的形态：本地 `python -m yiriyishi` 可调试运行，调试通过后交付单文件 `exe`。

## 2. 技术选型（已确认）

| 项 | 选择 | 理由 |
|---|---|---|
| GUI | PySide6（Qt6 Widgets） | 悬浮球需要无边框半透明置顶窗、托盘、动画、深浅色跟随系统，Qt 能干净做到；CustomTkinter 这些全要手写 |
| 存储 | SQLite（任务/人员）+ JSON（偏好/阈值/自启状态） | 结构化数据进 DB，易变偏好进 JSON，避免为几个开关建表 |
| 通知 | Windows 用 `windows-toasts`，macOS/Linux 用 `plyer` | 验收要求"点击通知唤起窗口"，plyer 在 Windows 的 PowerShell toast 无点击回调 |
| 依赖环境 | 项目内 `.venv` | 不污染已有全局 Python 3.12.10 环境 |
| 打包 | PyInstaller `--onefile --windowed` | 对 PySide6/plyer 有成熟 hook，一次成功率高 |
| 只用 | QtCore / QtGui / QtWidgets | 不引 QML 与 Qt 附加模块，压内存与包体 |

运行时依赖：`PySide6`、`plyer`、`windows-toasts`（仅 Windows 生效）。开发依赖：`pytest`、`pyinstaller`。

## 3. 模块划分

```
MyToDo/
├── pyproject.toml
├── .venv/
├── assets/                     # app.ico / app.png / 悬浮球图标
├── src/yiriyishi/
│   ├── __main__.py             # python -m yiriyishi 入口
│   ├── main.py                 # 启动装配：单实例 → QApplication → 主题 → 主窗 → 悬浮球 → 托盘 → 调度器
│   ├── paths.py                # 数据目录解析，区分源码运行 / sys.frozen / --datadir
│   ├── core/
│   │   ├── models.py           # Task / Person / AppSettings dataclass
│   │   ├── db.py               # 连接工厂(WAL) + schema 初始化 + user_version 迁移
│   │   ├── repository.py       # PersonRepository / TaskRepository（纯 SQL，不 import Qt）
│   │   ├── priority.py         # 双尺度优先级引擎（纯函数，唯一算法真相源）
│   │   ├── recurrence.py       # 重复规则解析与下次到期计算（纯函数）
│   │   ├── config.py           # JSON 原子读写 + 默认值深合并 + 损坏自动重建
│   │   ├── scheduler.py        # QObject 移入 QThread，30s tick，去重/免打扰判断
│   │   ├── notify.py           # Notifier 协议 + WindowsToastsNotifier + PlyerNotifier + 工厂
│   │   ├── autostart.py        # winreg / osascript / ~/.config/autostart
│   │   ├── sync.py             # SyncProvider 协议 + LocalBackupProvider + HttpProvider(占位)
│   │   └── log.py              # RotatingFileHandler 2MB×3
│   └── ui/
│       ├── theme.py            # design tokens → QSS 生成，浅/深两套，运行时热切换
│       ├── single_instance.py  # QLocalServer / QLocalSocket
│       ├── task_delegate.py    # 列表项自绘：优先级色条 + 逾期闪烁 + 人员色点
│       ├── task_list.py        # QListView + 分类/筛选/排序模型
│       ├── task_form.py        # 新增/编辑表单（含人员可搜下拉、日期快捷、周期规则）
│       ├── detail_panel.py     # 右侧详情 + 富文本备注
│       ├── filter_bar.py       # 分类 chips + 人员/状态/关键词筛选 + 排序
│       ├── toast_banner.py     # 主窗顶部滑入横幅 + 「推迟 15 分钟」
│       ├── float_ball.py       # 悬浮球：拖拽、角标计数、点击展开/收起
│       ├── float_panel.py      # 分类概览面板（逾期/今日/周期/未来）
│       ├── settings_dialog.py  # 优先级阈值、免打扰、主题、自启、悬浮球开关、备份目录
│       └── main_window.py      # 布局编排、close→托盘、几何与选中项持久化、草稿恢复
└── tests/
```

边界约定：`core/` 全部不 import QtWidgets，可脱离 GUI 单测；`ui/` 不写 SQL，只调 repository；`priority.py` 与 `recurrence.py` 是纯函数，输入 `(now, due_time, importance, settings)` 输出等级与分数。任何人改颜色规则只能改 `priority.py` 一处。

## 4. 数据结构

```sql
CREATE TABLE people (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL UNIQUE,
    color TEXT,                       -- 人员标识色，用于色点
    sort_order INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1          -- 停用不出现在下拉，历史任务仍保留
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    note TEXT,                        -- 富文本 HTML 片段
    owner_id TEXT REFERENCES people(id),
    due_time TIMESTAMP,               -- NULL = 无截止时间
    importance INTEGER DEFAULT 3,     -- 1-5，仅作同级内排序权重
    status TEXT DEFAULT 'todo',       -- todo / done / archived
    recurrence TEXT,                  -- NULL=普通; 'daily' | 'weekly:1,3,5' | 'monthly:15' | 'every_n_days:3'
    recurrence_group_id TEXT,         -- 同一周期任务的所有实例共享，用于"只显示当期"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    reminder_offset INTEGER DEFAULT 15,
    last_notified_at TIMESTAMP,        -- 防重复提醒
    postponed_until TIMESTAMP          -- 「推迟 15 分钟」用，不改动 due_time
);

CREATE TABLE focus_sessions (          -- 本期建表不写入（V1.1 番茄钟用）
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    duration_minutes INTEGER,
    started_at TIMESTAMP,
    completed BOOLEAN
);
```

索引：`tasks(status, due_time)`、`tasks(owner_id)`、`tasks(recurrence_group_id, status)`、`tasks(last_notified_at)`。

周期实例语义：一条任务实例 = 一期。完成当期时写入 `completed_at` 并按规则生成下一期新行（同一 `recurrence_group_id`，新 `id`，新 `due_time`，继承标题/负责人/备注/规则）。列表与悬浮面板里，同一 group 只展示 `status='todo'` 的最早一期，历史已完成期数进"已完成"分类。

分类（bucket）是**计算值不是字段**，规则见 §5，改到期时间会自动换类。

`config.json`：优先级阈值、免打扰时段、主题模式、自启意图、悬浮球开关与位置、窗口几何、上次选中任务、默认排序、备份目录、`schema_version`。任务数据一律不进 JSON。

## 5. 优先级引擎（双尺度，阈值可在设置改）

```
m = (due_time + postponed_until) - now
同一自然日内到期的任务走"小时尺度"，跨天的走"天尺度"。
```

| 等级 | 触发条件（默认阈值） | 颜色 | 备注 |
|---|---|---|---|
| OVERDUE | m < 0 | 红 `#E74C3C` | 1Hz 闪烁 + 强制置顶 |
| URGENT | 当天任务，m ≤ `urgent_hours`=30min | 红 `#E74C3C` | 默认值来自验收标准 1，改动需在设置页显式操作 |
| WARNING | 当天任务，m ≤ `warning_hours`=8h | 黄 `#F39C12` | |
| TODAY | 当天任务其余部分 | 鼠尾草绿 `#8A9A5B` | |
| NEAR | 跨天任务，剩余 ≤ `near_days`=1 天 | 黄 `#F39C12` | |
| WEEK | 跨天任务，剩余 ≤ `week_days`=3 天 | 浅绿 `#A8B88A` | |
| FUTURE | 跨天任务，剩余 > 3 天 | 灰 `#95A5A6` | |
| NORMAL | `due_time` 为 NULL | 灰 `#95A5A6` | |
| DONE | `status='done'` | 灰、删除线、沉底 | 不参与颜色计算 |

`time_urgency_factor`（用于排序，单调递减到更精确的位次）：

```
OVERDUE → 100 + min(20, 逾期小时数)        # 100..120
URGENT  → 70  + (urgent_hours - m) / urgent_hours * 20
WARNING → 45  + (warning_hours - m) / (warning_hours - urgent_hours) * 25
TODAY   → 30  + (1 - m / 24h) * 15
NEAR    → 25
WEEK    → 15
FUTURE  → 5
无到期  → 0
DONE    → -1000

score = importance * 10 + time_urgency_factor
排序 = score DESC, due_time ASC, created_at DESC
```

`importance` 星级不再直接决定颜色（颜色由日期主导，符合"根据完成日期定义优先级"），只在同等级内决定先后，并保留一个例外：`due_time` 为 NULL 时，4-5 星显示鼠尾草绿边框以示"重要但不紧急"。

深浅色模式下等级色相不变，深色下把红/黄提亮为 `#FF6B5B` / `#FFB84D` 以保证对比度。

## 6. 提醒链路

QThread 内 30 秒 tick：取 `status='todo' AND due_time IS NOT NULL`，触发点 `now ≥ due - reminder_offset`（若 `postponed_until` 存在则用它作参照）且 `last_notified_at` 为空 → 发系统通知 + 应用内 Toast 横幅 → 回写 `last_notified_at` 去重。

免打扰时段（默认 22:00–08:00，支持跨午夜）：期间**只写 `last_notified_at` 与日志，不弹窗**，避免时段结束后一次性轰炸。到期未处理的任务不再重复通知，改由 UI 红色闪烁承担提醒职责。

Windows 点击唤起：`windows-toasts` 的 `on_activated` 回调在非 GUI 线程触发 → 经 QObject 信号（队列连接）回主线程 → `show + raise + activateWindow + 选中并滚动到该任务 + 高亮 1.5s`。启动时调 `SetCurrentProcessExplicitAppUserModelID` 保证通知归属应用。macOS/Linux 走 plyer，点击定位降级为双击托盘唤起，属已知限制。

应用内 Toast 横幅支持「推迟 15 分钟」：写 `postponed_until = now + 15min`、清空 `last_notified_at`，不改 `due_time`，因此分类与颜色随之后移。

调度线程**每次自己开一条 SQLite 连接**，与 GUI 线程不共享 connection 对象（sqlite3 跨线程共享是崩溃源）。

## 7. 悬浮球与托盘

悬浮球：`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`，48px 圆形半透明，`paintEvent` 画鼠尾草绿圆 + 白色任务图标 + 右上角红点数字（逾期数 + 今日待办数，均为 0 时不画红点）。按下即拖（移动超过 4px 判定拖拽，否则算点击），位置写 `config.json`；单击展开/收起面板，面板靠屏幕边缘时自动翻转方向避免出界。鼠标移出面板 400ms 后自动收起。设置里可完全隐藏悬浮球。

面板 320×420：四个分组标题（逾期 n / 今日 n / 周期 n / 未来 n），每组最多显示 5 条，超出显示"查看全部"跳主窗口；每条一行 = 优先级色点 + 标题（超长截断）+ 负责人 + 剩余时间（"3h 后"/"已逾期 2h"）。点击一条 → 唤起主窗口并定位。面板顶部一个「+ 新任务」快捷入口。

托盘：`setQuitOnLastWindowClosed(False)`，关窗 = 隐藏到托盘（首次弹提示），右键菜单「显示主窗口 / 暂停提醒 / 隐藏悬浮球 / 退出」。仅「退出」真正 `quit()`，并顺带停线程、关连接。

单实例：`QLocalServer("yiriyishi-v1")`，二次启动发 `SHOW` 后自身退出，首实例唤起窗口。

## 8. 主界面（尽可能节简）

单页布局，不做多级导航：

```
┌───────────────────────────────────────────────────┐
│ [+ 新任务]  [逾期|今日|周期|未来|已完成|全部]  人▾ 排序▾ 🔍 │
├───────────────────────────┬───────────────────────┤
│ 任务列表（色条 + 标题 +    │  详情 / 编辑          │
│ 负责人色点 + 剩余时间）    │  标题 负责人 到期时间  │
│                           │  周期规则 星级 备注    │
│                           │  [完成] [删除]        │
└───────────────────────────┴───────────────────────┘
```

筛选：分类 chips 单选 + 人员下拉（多选）+ 关键词搜标题，三者取交集；状态默认排除 archived。排序下拉写回 `config.json` 作为记忆值。双击列表项进编辑，右键菜单含「完成 / 推迟 15 分钟 / 删除 / 归档」。日期选择用 `QDateTimeEdit` + 三个快捷按钮（今天 18:00 / 明天 09:00 / 本周五 18:00），避免手打。备注用 `QTextEdit` 存 HTML 子集，粘贴纯文本自动转义。

配色：浅底 `#F5F5F7`，强调 `#8A9A5B`；深底 `#1C1E21`、卡片 `#26292E`。字体：中文走系统族（Microsoft YaHei / PingFang SC / Noto Sans CJK 回退），数字与日期用等宽（Consolas / Menlo / DejaVu Sans Mono）。深色跟随系统用 `QStyleHints.colorScheme()`（Qt≥6.5）+ 监听变更事件重贴 QSS，设置里可切「跟随系统 / 强制浅 / 强制深」。所有颜色一律从 `theme.py` 的 token 取，禁止控件内硬编码 hex——这是验收"切深色无文字不可见"的实现前提。

## 9. 联网 / 同步

`SyncProvider` 协议：`export_snapshot() -> Path`、`import_snapshot(path)`、`push(changes)`、`pull(since)`。本期实现 `LocalBackupProvider`（把 `data.db` + `config.json` 拷到设置里指定的备份目录，可指向坚果云/OneDrive 同步目录，文件名带时间戳并保留最近 10 份）。`HttpSyncProvider` 只留类与 NotImplementedError，并写明未来需要的字段（`updated_at` 增量、软删标记）。全部离线可用，同步失败仅记日志并在设置页显示上次备份结果，不打断主流程。

## 10. 异常恢复与非功能

崩溃恢复：窗口几何与选中项每次变更即存；编辑器草稿 2s 防抖写 `draft.json`，重启若存在未提交草稿则顶部提示「恢复上次未保存的编辑 / 丢弃」。所有写库操作包 try/except 并落日志，通知发送失败降级为应用内横幅（不静默丢失）。

启动：只做 DB 连接 + 主题 + 主窗必需构建，其余（备份检查、统计）延迟到事件循环空闲，目标冷启动 < 2s。

内存：QtWidgets 基线实测通常 100–140MB，**"空闲 < 100MB"这条不承诺达标**，实施时先实测再决定是否继续压缩（手段：不引 QML、图标不重复加载、悬浮面板懒构建）。若必须达标需换 CustomTkinter。

日志：`logging` + `RotatingFileHandler`，DEBUG 落 `<datadir>/logs/app.log`，WARNING 起同时进 stderr（调试期可见），记录通知失败、自启写入失败、DB 迁移、同步结果。

## 11. 测试与验收

pytest（`core/` 全层，不依赖真实 GUI 与真实通知）：
- `priority.py`：假时钟打边界（m = -1、0、2h、2h+1s、8h、当天末尾、1 天、3 天、3 天+1s、NULL、done），断言等级 + 颜色 + score 单调性；`postponed_until` 生效
- `recurrence.py`：`daily` 跨月、`weekly:1,3,5`、`monthly:15` 月末不存在、`every_n_days:3`、完成后生成下期的 group 继承
- `repository.py`：CRUD、人员停用后历史任务仍显示、去重字段、group 只返回当期
- `config.py`：原子写、缺字段深合并默认值、损坏文件自动重建
- `scheduler.py`：stub notifier 断言触发一次不重复、免打扰内只记不发、跨午夜时段、暂停提醒全跳过
- `autostart.py`：三平台命令/键值构造走 dry-run，不改本机注册表
- GUI：`QT_QPA_PLATFORM=offscreen` 冒烟（主窗、悬浮球、面板、深浅色切换不崩）

人工验收（真实系统级行为，按原需求 5 条 + 新增 3 条）：
1. 建 1h 后到期的低优先级任务 → 黄色（1h > 30min 阈值）；时间推到剩 30min → 变红
2. 设 1 分钟后提醒并最小化 → 收到原生通知，点击唤起主窗并定位该任务
3. 开启自启 → 重启电脑后自动运行且托盘/悬浮球可见
4. 关主窗进程仍在，仅点「退出」才结束
5. 切深色模式全元素适配，无文字不可见
6. 悬浮球可拖拽、点击展开四分类面板、点条目定位任务、角标数字与列表一致
7. 周期任务完成后自动生成下一期，"周期"分类只显示当期
8. 筛选（分类 × 人员 × 关键词）与四种排序结果正确

交付流程：本地调试全绿 → PyInstaller 出 `--onefile --windowed --icon assets/app.ico`，`--collect-data plyer`，winrt 隐藏导入补齐，`paths.py` 区分 `sys.frozen` → **打包后必须重跑验收 1–8**（源码通过 ≠ 打包通过，通知与资源路径是打包最常见的坑）。

## 12. 本期不做

番茄钟（25/5 + 音效）、统计面板与趋势图、拖拽排序、多协办人、真正的多端同步服务端。`focus_sessions` 建表但不写入。

## 13. 已知限制

- macOS/Linux 通知点击无法定位任务（plyer 无回调），降级为双击托盘
- 开机自启在 Windows 上需在打包后重新写一次注册表路径，开发期指向 `pythonw`，首次运行会自我修正
- 「空闲内存 < 100MB」不保证达标（见 §10）
- 深色模式跟随系统在 Windows 10 1903 以下取不到，回退浅色
