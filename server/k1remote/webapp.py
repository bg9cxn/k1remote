"""Web 服务：HTTPS 静态托管 + WebSocket（屏幕帧下发、控制指令上行）。

浏览器 ↔ 服务端 WebSocket 协议（M1 范围）：
  服务端 → 客户端
    二进制  [0x01][stateFlags][1024B 垂直字节帧缓冲]        屏幕帧
    文本    {"t":"hello","radio":bool,"tx":bool,"rx":bool}   接入问候
    文本    {"t":"status","radio":bool,"tx":bool,"rx":bool}  连接/状态变化
    文本    {"t":"rflog","rows":[{...}]}                     RF Log 活动行
  客户端 → 服务端
    文本    {"t":"key","key":"up","long":false}              注入按键
    文本    {"t":"ping"}                                     心跳
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Optional, Set

from aiohttp import WSMsgType, web

from .config import AppConfig
from .ptt import PttManager
from .radio.k5viewer import K5ViewerClient
from .radio.protocol import FLAG_LED_GREEN, FLAG_LED_RED, Frame, Key, ScreenFrame, RfLogFrame, RfLogHistoryFrame, ScreenState, keycode_from_name
from .webrtc import WebRTCService

log = logging.getLogger(__name__)

WS_MSG_SCREEN = 0x01


class Hub:
    """汇聚串口回调并广播给所有 WebSocket 客户端。"""

    def __init__(self) -> None:
        self.screen = ScreenState()
        self.radio_connected = False
        self.clients: Set[web.WebSocketResponse] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_tid: Optional[int] = None
        self.last_screen_at: Optional[float] = None  # 最近收到屏幕帧的时刻
        self._last_wake_at: float = 0.0

    # ---- radio 线程回调（经 call_soon_threadsafe 封送）----

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_tid = threading.get_ident()

    def on_radio_frame(self, frame: Frame) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._handle_frame, frame)

    def on_radio_state(self, connected: bool) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._handle_state, connected)

    def _handle_frame(self, frame: Frame) -> None:
        if isinstance(frame, ScreenFrame):
            self.screen.apply(frame)
            self.last_screen_at = time.monotonic()
            self.broadcast_binary(bytes((WS_MSG_SCREEN, self.screen.state_flags)) + bytes(self.screen.framebuffer))
            if self._flag_edge(frame.state_flags):
                self.broadcast_json(self.status_dict())
        elif isinstance(frame, RfLogFrame):
            if frame.rows:
                self.broadcast_json({
                    "t": "rflog",
                    "rows": [r.as_dict() for r in frame.rows[-8:]],
                })
        elif isinstance(frame, RfLogHistoryFrame):
            log.debug("RF Log 历史分页 %d 行（UI 待接入）", len(frame.rows))

    def _handle_state(self, connected: bool) -> None:
        self.radio_connected = connected
        self.broadcast_json(self.status_dict())

    # ---- 状态与广播 ----

    def _last_flags(self) -> int:
        return getattr(self, "_last_flags_value", -1)

    def _flag_edge(self, flags: int) -> bool:
        last = self._last_flags()
        self._last_flags_value = flags
        return last != flags

    def status_dict(self) -> dict:
        return {
            "t": "status",
            "radio": self.radio_connected,
            "tx": bool(self.screen.state_flags & FLAG_LED_RED),
            "rx": bool(self.screen.state_flags & FLAG_LED_GREEN),
        }

    def broadcast_binary(self, data: bytes) -> None:
        for ws in list(self.clients):
            asyncio.ensure_future(ws.send_bytes(data))

    def broadcast_json(self, obj: dict) -> None:
        """线程安全：串口 worker 线程（PTT 重连复位）也会调用。"""
        if self._loop is not None and threading.get_ident() != self._loop_tid:
            self._loop.call_soon_threadsafe(self._broadcast_json_now, obj)
        else:
            self._broadcast_json_now(obj)

    def _broadcast_json_now(self, obj: dict) -> None:
        text = json.dumps(obj, ensure_ascii=False)
        for ws in list(self.clients):
            asyncio.ensure_future(ws.send_str(text))

    async def send_snapshot(self, ws: web.WebSocketResponse, ptt: PttManager) -> None:
        await ws.send_json({"t": "hello", **{
            k: v for k, v in self.status_dict().items() if k != "t"},
            "ptt": ptt.held, "max_tx_s": ptt.max_tx_s})
        if self.screen.received_any:
            await ws.send_bytes(bytes((WS_MSG_SCREEN, self.screen.state_flags))
                                + bytes(self.screen.framebuffer))


def make_app(cfg: AppConfig, radio: K5ViewerClient, hub: Hub,
             webrtc: WebRTCService, ptt: PttManager, web_dir: Path) -> web.Application:
    wake_task: Optional[asyncio.Task] = None
    watchdog_task: Optional[asyncio.Task] = None

    async def wake_loop() -> None:
        """屏幕流停滞自动唤醒：对讲机 SetOff 深睡后推流静默，且固件的深睡
        倒计时只认射频收发（串口活动不阻止入睡）。停滞时注入 EXIT（各界面
        下最无害的按键）唤醒主循环；空闲省电的慢速流（~0.5s 一帧）不触发。
        发射保持期间跳过（数据会破坏 PTT，且唤醒键会排队到释放后）。"""
        try:
            while True:
                await asyncio.sleep(2.0)
                if not hub.radio_connected or not hub.clients or ptt.held or ptt.releasing:
                    continue
                now = time.monotonic()
                last = hub.last_screen_at
                if (last is None or now - last > 6.0) and now - hub._last_wake_at > 10.0:
                    hub._last_wake_at = now
                    radio.send_key(Key.EXIT)
                    log.info("屏幕流停滞，注入 EXIT 尝试唤醒对讲机")
        except asyncio.CancelledError:
            pass

    async def ptt_watchdog() -> None:
        """最长连续发射时限：到点强制释放并广播原因。"""
        try:
            while True:
                await asyncio.sleep(1.0)
                ptt.check_watchdog(time.monotonic())
        except asyncio.CancelledError:
            pass

    async def on_startup(app: web.Application) -> None:
        # 事件循环就绪后再启动串口线程，否则开头的连接回调会因 hub 未绑定 loop 而丢失
        nonlocal wake_task, watchdog_task
        hub.attach_loop(asyncio.get_running_loop())
        ptt.bind_loop(asyncio.get_running_loop())
        radio.start()
        wake_task = asyncio.create_task(wake_loop())
        watchdog_task = asyncio.create_task(ptt_watchdog())

    async def on_cleanup(app: web.Application) -> None:
        radio.stop()
        ptt.request_off(reason="服务停止")
        for task in (wake_task, watchdog_task):
            if task:
                task.cancel()
        # 关闭声卡流与 PeerConnection——否则 PortAudio 线程残留可能导致退出挂起
        try:
            await webrtc.close()
        except Exception as exc:
            log.warning("WebRTC 清理异常: %s", exc)
        if wake_task:
            wake_task.cancel()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(web_dir / "index.html")

    async def ws_handler(request: web.Request) -> web.StreamResponse:
        token = cfg.server.token
        if token and request.query.get("token") != token:
            raise web.HTTPUnauthorized(text="missing or invalid token")
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        hub.clients.add(ws)
        log.info("客户端接入（当前 %d 个）", len(hub.clients))
        try:
            await hub.send_snapshot(ws, ptt)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await handle_command(ws, msg.data, radio, hub, webrtc, ptt)
                elif msg.type == WSMsgType.ERROR:
                    log.warning("WS 错误: %s", msg.data)
        finally:
            hub.clients.discard(ws)
            ptt.release_if_holder(ws)  # 持有 PTT 的操作端断开 → 立即撤发
            log.info("客户端断开（剩余 %d 个）", len(hub.clients))
        return ws

    async def handle_command(ws: web.WebSocketResponse, raw: str,
                             radio: K5ViewerClient, hub: Hub,
                             webrtc: WebRTCService, ptt: PttManager) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("t")
        if kind == "key":
            if not hub.radio_connected:
                await ws.send_json({"t": "error", "msg": "对讲机未连接"})
                return
            key = keycode_from_name(str(msg.get("key", "")))
            if key is None:
                await ws.send_json({"t": "error", "msg": f"未知按键: {msg.get('key')}"})
                return
            radio.send_key(key, bool(msg.get("long", False)))
        elif kind == "ptt":
            # PTT 处理绝不允许异常冲垮 WS 处理器——连接断开会触发断线撤发，
            # 表现为"按下 1 秒后发射自动停止"
            try:
                if msg.get("on"):
                    err = ptt.request_on(ws)
                    if err:
                        await ws.send_json({"t": "error", "msg": err})
                else:
                    ptt.request_off(ws)
            except Exception as exc:
                log.exception("PTT 处理异常")
                ptt.request_off(ws, reason="内部错误")
                await ws.send_json({"t": "error", "msg": f"PTT 内部错误: {exc}"})
        elif kind == "rtc-offer":
            try:
                answer = await webrtc.handle_offer(str(msg.get("sdp", "")))
                await ws.send_json(answer)
            except Exception as exc:  # sdp 非法等
                log.warning("WebRTC offer 处理失败: %s", exc)
                await ws.send_json({"t": "error", "msg": f"WebRTC 失败: {exc}"})
        elif kind == "ping":
            await ws.send_json({"t": "pong"})

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", web_dir / "static")
    return app


def default_web_dir() -> Path:
    # server/k1remote/webapp.py → 项目根/web
    return Path(__file__).resolve().parents[2] / "web"


def run(cfg: AppConfig, radio: K5ViewerClient, webrtc: WebRTCService,
        web_dir: Optional[str] = None) -> None:
    web_path = Path(web_dir or cfg.server.web_dir or default_web_dir())
    hub = Hub()
    radio.on_frame = hub.on_radio_frame
    radio.on_state = hub.on_radio_state
    radio.on_link_reset = lambda: None  # PttManager 挂接后覆盖

    ptt = PttManager(
        radio, hub,
        max_tx_s=cfg.radio.max_tx_s,
        tail_ms=cfg.radio.tx_tail_ms,
        on_hold_change=(webrtc.sink.set_enabled if webrtc.sink is not None else None),
    )
    radio.on_link_reset = ptt.on_link_reset  # worker 线程调用，内部已线程安全

    ssl_ctx: Optional[ssl.SSLContext] = None
    if cfg.server.tls_cert and cfg.server.tls_key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cfg.server.tls_cert, cfg.server.tls_key)

    app = make_app(cfg, radio, hub, webrtc, ptt, web_path)
    scheme = "https" if ssl_ctx else "http"
    log.info("Web 服务启动: %s://%s:%s", scheme, cfg.server.host, cfg.server.port)
    try:
        web.run_app(app, host=cfg.server.host, port=cfg.server.port,
                    ssl_context=ssl_ctx, print=None,
                    shutdown_timeout=5.0)  # 打开的 WebSocket 不阻塞退出（默认 60s）
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        radio.stop()
        print("服务已停止")
