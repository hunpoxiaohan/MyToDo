"""本地调试用：灌入一批覆盖各分类的示例任务。

用法（PowerShell）：$env:PYTHONPATH='src'; .venv\\Scripts\\python.exe tools\\seed_demo.py --datadir .\\.devdata
每次运行都会追加一批，想从干净数据开始先删掉数据目录。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from yiriyishi.core.db import connect, init_db
from yiriyishi.core.recurrence import complete_and_roll
from yiriyishi.core.repository import PersonRepository, TaskRepository
from yiriyishi.paths import Paths


def seed(datadir: str | None) -> None:
    paths = Paths(datadir)
    conn = connect(paths.db)
    init_db(conn)
    tasks, people = TaskRepository(conn), PersonRepository(conn)
    now = datetime.now()

    me = people.create("我")
    boss = people.create("张经理")
    dev = people.create("李开发")

    rows = [
        dict(title="回复张经理的需求评审意见", owner_id=boss.id,
             due_time=now - timedelta(hours=2), importance=5, reminder_offset=15),
        dict(title="提交周报", owner_id=me.id,
             due_time=now.replace(hour=18, minute=0, second=0, microsecond=0),
             importance=4, reminder_offset=30),
        dict(title="修复登录超时 bug", owner_id=dev.id,
             due_time=now + timedelta(minutes=40), importance=5, reminder_offset=15),
        dict(title="整理季度 OKR", owner_id=me.id,
             due_time=now + timedelta(hours=6), importance=3, reminder_offset=60),
        dict(title="周五团队例会纪要", owner_id=me.id,
             due_time=now + timedelta(days=3), importance=2, reminder_offset=15),
        dict(title="下月版本规划", owner_id=boss.id,
             due_time=now + timedelta(days=12), importance=3, reminder_offset=1440),
        dict(title="每日代码走查", owner_id=dev.id,
             due_time=(now + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0),
             importance=4, recurrence="daily"),
        dict(title="月度账单核对", owner_id=me.id,
             due_time=now + timedelta(days=5), importance=3, recurrence="monthly:15"),
    ]
    existed = len(tasks.list(include_archived=True))
    for row in rows:
        tasks.create(**row)
    done = tasks.create(title="归档一份旧合同", owner_id=me.id,
                        due_time=now - timedelta(days=1), importance=2)
    complete_and_roll(tasks, done, now)

    conn.close()
    print(f"数据目录：{paths.root}")
    print(f"新增 {len(rows)} 条示例任务（此前已有 {existed} 条）")
    print("启动：$env:PYTHONPATH='src'; .venv\\Scripts\\python.exe -m yiriyishi --debug --datadir .devdata")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--datadir", default="./.devdata")
    seed(ap.parse_args().datadir)
