from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from yiriyishi.core.db import connect, init_db
from yiriyishi.paths import Paths


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths(tmp_path / "data")


@pytest.fixture
def conn(paths):
    c = connect(paths.db)
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def now():
    """固定时钟：所有优先级断言都基于它，避免时间相关的偶发失败。"""
    from datetime import datetime

    return datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def repo(conn):
    from yiriyishi.core.repository import TaskRepository

    return TaskRepository(conn)


@pytest.fixture
def people(conn):
    from yiriyishi.core.repository import PersonRepository

    return PersonRepository(conn)
