"""K5Viewer 串口客户端：保活、推流解析、按键注入、PTT 电平控制、断线自动重连。

工作线程持有串口做阻塞读写（pyserial 非线程安全，所有写操作都经
worker 线程统一执行），回调发生在 worker 线程——asyncio 侧需自行
用 loop.call_soon_threadsafe 封送。

PTT：set_ptt() 经控制队列交给 worker 线程执行 DTR/RTS 电平切换；
发射保持期间 worker 停发一切串口数据（保活/按键），否则 AIOC 固件
TXFRCPTT 会强制释放 PTT1；重连时 PTT 强制复位并回调 on_link_reset。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

import serial

from .protocol import (
    Frame,
    Key,
    StreamParser,
    keepalive_frame,
    key_frame,
)

log = logging.getLogger(__name__)


class K5ViewerClient:
    """对讲机 K5Viewer 流客户端。

    on_frame(frame)     — 任意已解析帧（ScreenFrame / RfLogFrame / ...）
    on_state(connected) — 串口打开/断开
    on_error(exc)       — 重连前的最后一次错误（可选）
    on_link_reset()     — 重连导致 PTT 强制复位（worker 线程调用）
    """

    def __init__(
        self,
        port: str,
        *,
        baud: int = 38400,
        keepalive_interval: float = 0.1,
        features: int = 0,
        reconnect_delay: float = 2.0,
        ptt=None,  # Optional[AiocPtt]
        ptt_shared: bool = False,  # True: AIOC 与对讲机同一串口（V1 单线）
        on_frame: Optional[Callable[[Frame], None]] = None,
        on_state: Optional[Callable[[bool], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_link_reset: Optional[Callable[[], None]] = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.keepalive_interval = keepalive_interval
        self.features = features
        self.reconnect_delay = reconnect_delay
        self.ptt_shared = ptt_shared
        self.on_frame = on_frame
        self.on_state = on_state
        self.on_error = on_error
        self.on_link_reset = on_link_reset

        self._ptt = ptt
        self._ptt_hold = False  # 仅 worker 线程读写
        self._tx: "queue.Queue[bytes]" = queue.Queue()
        self._control: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def ptt_available(self) -> bool:
        return self._ptt is not None

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker, name="k5viewer-serial", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ---- 控制（线程安全，经队列交 worker 执行）----

    def send_key(self, key: Key, long_press: bool = False) -> None:
        self._tx.put(key_frame(key, long_press))

    def send_raw(self, data: bytes) -> None:
        self._tx.put(data)

    def set_ptt(self, hold: bool) -> None:
        self._control.put(("ptt", hold))

    # ---- worker ----

    def _worker(self) -> None:
        parser = StreamParser()
        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                    # 清除 pyserial 打开串口时的默认 DTR/RTS 拉起（DTR=1 & RTS=0
                    # 是 AIOC 的 PTT 键控电平，默认状态是潜在误发射源）
                    ser.setDTR(False)
                    ser.setRTS(True)
                    if self._ptt is not None and self.ptt_shared:
                        self._ptt.attach(ser)  # 同口模式：句柄每次重开都要重新绑定
                    if self._ptt_hold:
                        # 重连意味着电平已被端口关闭复位，发射不可能还在持续
                        self._ptt_hold = False
                        log.warning("串口重连，PTT 已强制复位")
                        if self.on_link_reset:
                            self.on_link_reset()
                    log.info("串口已打开: %s @ %d", self.port, self.baud)
                    if self.on_state:
                        self.on_state(True)
                    parser = StreamParser()  # 重开串口后重置流状态
                    self._pump(ser, parser)
            except serial.SerialException as exc:
                log.warning("串口错误: %s", exc)
                if self.on_error:
                    self.on_error(exc)
            finally:
                if self.on_state:
                    self.on_state(False)
            if not self._stop.is_set():
                time.sleep(self.reconnect_delay)

    def _pump(self, ser: serial.Serial, parser: StreamParser) -> None:
        next_keepalive = 0.0
        while not self._stop.is_set():
            now = time.monotonic()

            # 控制队列无条件处理——PTT 释放必须即时，即使在保持期
            while True:
                try:
                    kind, value = self._control.get_nowait()
                except queue.Empty:
                    break
                if kind == "ptt" and value != self._ptt_hold:
                    if self._ptt is not None:
                        self._ptt.set(value)
                    self._ptt_hold = value
                    if not value:
                        next_keepalive = 0.0  # 释放后立即恢复保活，屏幕流补整帧

            # 发射保持期间静默一切串口数据（AIOC TXFRCPTT 数据优先会强制
            # 释放 PTT1）；期间新按键注入在队列里排队，释放后统一发出
            if not self._ptt_hold:
                if now >= next_keepalive:
                    ser.write(keepalive_frame(self.features))
                    next_keepalive = now + self.keepalive_interval
                while True:
                    try:
                        ser.write(self._tx.get_nowait())
                    except queue.Empty:
                        break

            data = ser.read(512)
            if data:
                for frame in parser.feed(data):
                    if self.on_frame:
                        self.on_frame(frame)
