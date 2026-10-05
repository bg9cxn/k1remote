"""演示模式：无硬件时生成动态假屏幕流，用于前端与链路开发验证。

接口与 K5ViewerClient 一致（start/stop/send_key + on_frame/on_state）。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, List, Optional, Tuple

from .protocol import (
    FRAME_LINES,
    FRAMEBUFFER_SIZE,
    FLAG_LED_GREEN,
    FLAG_LED_RED,
    Key,
    ScreenFrame,
    encode_chunk,
)

log = logging.getLogger(__name__)

# 3x5 迷你数字字体（每行 3 bit，高位在左），仅够演示画面对讲机风格的内容
FONT_3X5 = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b001, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
    ".": (0b000, 0b000, 0b000, 0b000, 0b010),
}


class DemoRadio:
    fps = 8
    ptt_available = True  # 演示模式允许 PTT 状态机走通（无射频）

    def __init__(self) -> None:
        self.on_frame: Optional[Callable[[ScreenFrame], None]] = None
        self.on_state: Optional[Callable[[bool], None]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # 与 K5ViewerClient 相同的接口

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="demo-radio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def send_key(self, key: Key, long_press: bool = False) -> None:
        log.info("demo: 忽略按键注入 %s%s", key, " (long)" if long_press else "")

    def set_ptt(self, hold: bool) -> None:
        log.info("demo: PTT -> %s（无射频）", "ON" if hold else "off")

    # ---- 动画 ----

    def _worker(self) -> None:
        if self.on_state:
            self.on_state(True)
        fb = bytearray(FRAMEBUFFER_SIZE)
        interval = 1.0 / self.fps
        next_tick = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic()
            flags = self._compose(fb, t)
            if self.on_frame:
                self.on_frame(ScreenFrame(flags, self._encode_all(fb)))
            next_tick += interval
            self._stop.wait(max(0.0, next_tick - time.monotonic()))
            if time.monotonic() - next_tick > interval:
                next_tick = time.monotonic()
        if self.on_state:
            self.on_state(False)

    @staticmethod
    def _px(fb: bytearray, x: int, y: int, on: bool = True) -> None:
        if 0 <= x < 128 and 0 <= y < 64:
            i = (y // 8) * 128 + x
            bit = 1 << (y % 8)
            if on:
                fb[i] |= bit
            else:
                fb[i] &= ~bit

    @classmethod
    def _rect(cls, fb: bytearray, x0: int, y0: int, x1: int, y1: int, fill: bool = False) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if fill or y in (y0, y1) or x in (x0, x1):
                    cls._px(fb, x, y)

    @classmethod
    def _text(cls, fb: bytearray, x: int, y: int, text: str) -> int:
        for ch in text:
            glyph = FONT_3X5.get(ch)
            if glyph is None:
                x += 4
                continue
            for row, bits in enumerate(glyph):
                for col in range(3):
                    if bits & (0b100 >> col):
                        cls._px(fb, x + col, y + row)
            x += 4
        return x

    def _compose(self, fb: bytearray, t: float) -> int:
        fb[:] = bytearray(FRAMEBUFFER_SIZE)

        # 状态行：电池块 + 信号块
        self._rect(fb, 0, 0, 22, 6)
        for i in range(5):
            if (int(t) // 2 + i) % 5:
                self._rect(fb, 2 + i * 4, 2, 4 + i * 4, 4, fill=True)
        self._rect(fb, 100, 0, 124, 6)

        # 主屏第一行：频率（演示文本渲染）
        self._text(fb, 8, 12, "155.5000")

        # S 表：随时间摆动的条
        level = int((math.sin(t * 1.3) * 0.5 + 0.5) * 90) + 6
        self._rect(fb, 4, 24, 4 + level, 29, fill=True)
        self._rect(fb, 4, 24, 118, 29)

        # 滚动游标方块
        cx = int((math.sin(t * 0.9) * 0.5 + 0.5) * 108) + 4
        self._rect(fb, cx, 38, cx + 7, 45, fill=True)

        # 二进制计数器（8 格）
        counter = int(t) % 256
        for i in range(8):
            if counter & (1 << i):
                self._rect(fb, 8 + i * 14, 54, 18 + i * 14, 61, fill=True)

        # TX/RX 状态模拟：RX 每 6s 收 2s，TX 每 13s 发 1.5s
        phase_rx = t % 6.0
        phase_tx = t % 13.0
        flags = 0
        if phase_tx < 1.5:
            flags |= FLAG_LED_RED
        elif phase_rx < 2.0:
            flags |= FLAG_LED_GREEN
        return flags

    @staticmethod
    def _encode_all(fb: bytes) -> List[Tuple[int, bytes]]:
        return [(idx, encode_chunk(idx, fb)) for idx in range(FRAME_LINES * 16)]
