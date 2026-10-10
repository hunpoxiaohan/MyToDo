from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("yiriyishi.ui.single_instance")

SERVER_NAME = "yiriyishi-v1"
GREETING = "SHOW"
TIMEOUT_MS = 600


class SingleInstance(QObject):
    """只允许一个实例；后来者唤起已存在的主窗口。"""

    activate_requested = Signal()

    def __init__(self, lock_dir: Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._lock = QLockFile(str(self._lock_path(lock_dir)))
        self._server: QLocalServer | None = None

    @staticmethod
    def _lock_path(lock_dir: Path | None) -> Path:
        if lock_dir is not None:
            lock_dir.mkdir(parents=True, exist_ok=True)
            return lock_dir / f"{SERVER_NAME}.lock"
        # 默认落在系统临时目录，避免从 exe 所在目录（可能只读）写锁文件
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardPath.TempLocation))
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{SERVER_NAME}.lock"

    def acquire(self) -> bool:
        """True = 自己是首实例；False = 已通知首实例，应退出。"""
        if self._lock.tryLock(200):
            self._start_server()
            return True
        if self._notify_owner():
            log.info("检测到已有实例运行，唤起其主窗口")
            return False
        # 陈旧锁（上次异常退出）：夺锁继续
        self._lock.removeStaleLockFile()
        if self._lock.tryLock(200):
            self._start_server()
            return True
        return True  # 拿不准时宁可正常启动，也别让用户打不开

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
        self._lock.unlock()

    def _start_server(self) -> None:
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(SERVER_NAME):
            log.warning("单实例服务启动失败：%s", self._server.errorString())
            self._server = None

    def _on_connection(self) -> None:
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(socket.deleteLater)

    def _read(self) -> None:
        socket = self.sender()
        if socket is None:
            return
        if GREETING in bytes(socket.readAll().data()).decode("utf-8", "ignore"):
            log.info("收到二次启动请求，唤起主窗口")
            self.activate_requested.emit()

    def _notify_owner(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if not socket.waitForConnected(TIMEOUT_MS):
            return False
        socket.write(GREETING.encode("utf-8"))
        socket.waitForBytesWritten(TIMEOUT_MS)
        socket.disconnectFromServer()
        return True
