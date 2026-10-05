"""AIOC 硬件 PTT 控制。

AIOC (github.com/skuep/AIOC) 串口电平约定：**DTR=1 且 RTS=0 → PTT 吸合（发射）**，
释放为 DTR=0 且 RTS=1。注意 pyserial 打开串口时默认拉起 DTR/RTS，因此 open()
后必须立即回到释放态，否则会意外键上发射。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import serial

log = logging.getLogger(__name__)


class AiocPtt:
    """AIOC PTT 控制。线程安全。

    两种用法：
      - 独占模式：AiocPtt(port)，首次 set() 时自行打开串口；
      - 共享模式：attach(ser) 绑定外部已打开的句柄——V1 单线方案下，同一
        AIOC 串口既要跑 K5Viewer 协议又要控 PTT 电平，Windows 不允许双开，
        必须复用一个 serial 句柄。
    """

    def __init__(self, port: str) -> None:
        self.port = port
        self._ser: Optional[serial.Serial] = None
        self._shared = False
        self._lock = threading.Lock()
        self._active = False

    def attach(self, ser: serial.Serial) -> None:
        """绑定外部已打开的串口句柄（可重入：重连后须重新绑定），并回到释放态。"""
        with self._lock:
            self._ser = ser
            self._shared = True
            ser.setDTR(False)
            ser.setRTS(True)
            log.info("AIOC 共享串口已绑定: %s", self.port)

    @property
    def is_shared(self) -> bool:
        return self._shared

    def _ensure_open(self) -> serial.Serial:
        if self._ser is None or not self._ser.is_open:
            self._ser = serial.Serial(self.port, 9600, timeout=1.0)
            # 打开瞬间 DTR/RTS 默认拉起会键上发射，立刻释放
            self._ser.setDTR(False)
            self._ser.setRTS(True)
            log.info("AIOC 串口已打开: %s", self.port)
        return self._ser

    def set(self, active: bool) -> None:
        with self._lock:
            ser = self._ensure_open()
            # AIOC 固件在 DTR/RTS 变化回调里应用 PTT，且 UART 发送器忙（TE=1）
            # 时直接丢弃该次变更（不排队不重试，见 usb_serial.c）。因此：
            #   断言：先 setRTS(False) 再 setDTR(True) —— 断言只发生在第二次
            #         回调（dtr=1 & rts=0 同时满足），把竞态窗口压到一次请求；
            #   释放：先 setDTR(False)（回调即释放），再补一次 RTS 翻转生成
            #         额外的线状态回调，防止"释放撞上 TE 忙被丢弃"导致卡发射。
            if active:
                ser.setRTS(False)
                ser.setDTR(True)
            else:
                ser.setDTR(False)
                ser.setRTS(True)
                ser.setRTS(False)
                ser.setRTS(True)
            self._active = active
            log.info("PTT -> %s (t=%.3f)", "ON" if active else "off", time.monotonic())

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    # 兜底：任何情况下关闭前先释放 PTT
                    self._ser.setDTR(False)
                    self._ser.setRTS(True)
                except serial.SerialException:
                    pass
                if not self._shared:
                    self._ser.close()
            if not self._shared:
                self._ser = None
            self._active = False

    def __enter__(self) -> "AiocPtt":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
