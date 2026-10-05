"""生产版 PTT 状态机：目标状态 + 约束校验 + 看门狗 + 优雅释放。

安全设计（全部围绕"卡发射是最大风险"）：
  - 键上前校验：电台红灯位已亮（卡发射/VOX 误触发）则拒绝键上；
  - 单操作员锁：同一时刻仅一个 WebSocket 会话可持有 PTT；
  - 看门狗：最长连续发射时限（默认 180s）强制释放；
  - 断线撤发：持有会话的 WebSocket 断开即释放；
  - 重连复位：串口重连意味着电平已被端口关闭复位，状态强制回零；
  - 最终兜底：对讲机 TxTOut（部署时配置）。
尾音保护（tail）：松开 PTT 后界面立即回就绪，但服务端保持发射
tail_ms（默认 400ms，覆盖浏览器在途语音 + 发话缓冲），停播后再等
输出管线播完（120ms）才撤 DTR 电平；期间重新按下则取消释放无缝续发。
线程模型：常规调用在事件循环线程；on_link_reset 可能来自串口 worker
线程——内部全部走线程安全通道（client.set_ptt 队列 / hub 线程安全广播）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)


class PttManager:
    def __init__(
        self,
        client,  # K5ViewerClient / DemoRadio：set_ptt(hold) + ptt_available
        hub,     # Hub：broadcast_json（线程安全）
        *,
        max_tx_s: float = 180.0,
        tail_ms: int = 400,
        on_hold_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.client = client
        self.hub = hub
        self.max_tx_s = max_tx_s
        self.tail_ms = tail_ms
        self.on_hold_change = on_hold_change
        self.held = False
        self.holder = None  # 持有会话的 ws 对象
        self.tx_started_at: Optional[float] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_tid: Optional[int] = None
        self._releasing = False  # 尾音保护进行中（电平未撤）
        self._tail_handle: Optional[asyncio.TimerHandle] = None
        self._dtr_handle: Optional[asyncio.TimerHandle] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_tid = threading.get_ident()

    @property
    def releasing(self) -> bool:
        return self._releasing

    # ---- UI 指令 ----

    def request_on(self, ws) -> Optional[str]:
        """请求键上；返回 None 表示接受，否则返回拒绝原因。"""
        if not getattr(self.client, "ptt_available", False):
            return "PTT 不可用：未接 AIOC（或演示模式未启用）"
        if self.held:
            if self.holder is ws:
                return None  # 幂等
            return "其他操作员正在发射"
        if self.hub.screen.led_tx:
            return "对讲机已在发射（红灯位亮），拒绝键上——请检查卡发射"
        self._cancel_tail()  # 尾音期内重按：取消释放，无缝续发
        self.held = True
        self.holder = ws
        self.tx_started_at = time.monotonic()
        self.client.set_ptt(True)  # 尾音期内为幂等空操作
        if self.on_hold_change:
            self.on_hold_change(True)  # 已开启时为幂等
        self._broadcast()
        log.info("PTT 键上（t=%.1f）", self.tx_started_at)
        return None

    def request_off(self, ws=None, reason: str = "", immediate: bool = False) -> None:
        """松开 PTT。默认走尾音保护（保持发射 tail_ms 后再撤电平）；
        immediate=True 用于串口重连等电平已经消失的场景。"""
        if not self.held:
            return
        if ws is not None and self.holder is not ws:
            return  # 非持有者无权释放
        duration = time.monotonic() - self.tx_started_at if self.tx_started_at else 0.0
        self.held = False
        self.holder = None
        self.tx_started_at = None
        self._broadcast(reason)
        log.info("PTT 释放（持续 %.1fs%s）", duration, f"，原因: {reason}" if reason else "")

        from_worker = self._loop is None or threading.get_ident() != self._loop_tid
        if immediate or from_worker or self._loop is None:
            self._do_release()
            return
        # 尾音保护：停播与撤电平均延后执行（见 _finish_tail/_release_dtr）
        self._releasing = True
        self._tail_handle = self._loop.call_later(
            self.tail_ms / 1000.0, self._finish_tail, reason)

    # ---- 看门狗（asyncio 任务周期调用）----

    def check_watchdog(self, now: float) -> Optional[str]:
        if self.held and self.tx_started_at and now - self.tx_started_at > self.max_tx_s:
            self.request_off(reason="发射超时保护")
            return "发射超时保护"
        return None

    # ---- 生命周期事件 ----

    def release_if_holder(self, ws) -> None:
        if self.held and self.holder is ws:
            self.request_off(ws=ws, reason="操作端断开")

    def on_link_reset(self) -> None:
        """串口重连：电平已被端口关闭复位，状态强制回零（可从 worker 线程调用）。"""
        self.request_off(immediate=True)

    # ---- 释放执行（尾音 -> 停播 -> 撤电平）----

    def _do_release(self) -> None:
        self._releasing = False
        if self.on_hold_change:
            self.on_hold_change(False)
        self.client.set_ptt(False)

    def _finish_tail(self, reason: str) -> None:
        self._tail_handle = None
        if self.held or not self._releasing:
            return  # 尾音期内重新按下，释放已取消
        self._releasing = False
        if self.on_hold_change:
            self.on_hold_change(False)  # 停播 + 清缓冲
        if self._loop is None:
            self.client.set_ptt(False)
            return
        # 等输出管线把最后的内容播完（low latency ≈ 90ms，留余量）
        self._dtr_handle = self._loop.call_later(0.12, self._release_dtr)

    def _release_dtr(self) -> None:
        self._dtr_handle = None
        if self.held or self._releasing:
            return  # 期间重新按下，撤电平作废
        self.client.set_ptt(False)

    def _cancel_tail(self) -> None:
        if self._tail_handle:
            self._tail_handle.cancel()
            self._tail_handle = None
        if self._dtr_handle:
            self._dtr_handle.cancel()
            self._dtr_handle = None
        self._releasing = False

    # ---- 广播 ----

    def _broadcast(self, reason: str = "") -> None:
        self.hub.broadcast_json({
            "t": "ptt", "held": self.held,
            "max_tx_s": self.max_tx_s, "reason": reason,
        })
