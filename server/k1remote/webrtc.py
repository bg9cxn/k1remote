"""WebRTC 下行：把对讲机收音推给浏览器（aiortc）。

M3 范围：单向音频（服务端 → 浏览器）；操作员麦克风轨已接收但暂不播放（M4）。
局域网/WireGuard 内 P2P 直连，不配置 STUN/TURN。
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import time
from typing import Optional

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame

from .audio import (
    AudioSource,
    RATE,
    SAMPLES_PER_FRAME,
    AudioMeter,
)

log = logging.getLogger(__name__)


class RadioAudioTrack(AudioStreamTrack):
    """按队列水位比例调节消费速率，从 AudioSource 拉 20ms 帧。

    时钟设计：采集跑在 AIOC 的声卡时钟上，与树莓派系统时钟存在 ppm 级
    偏差。若按系统墙钟定速消费，队列会被漂移抽干（→静音）或塞满（→周期
    性丢帧爆音）。因此消费节奏按队列水位比例调节（目标积压 3 帧，±30%
    速率调节范围）：速率自动跟随采集时钟，漂移免疫；正常水位下延迟
    ~60-80ms。队列 maxlen=15 是停顿时的硬安全网。
    """

    TARGET_BACKLOG = 3  # 目标积压帧数（60ms）
    MIN_DELAY = 0.004
    MAX_DELAY = 0.06
    FADE_SAMPLES = 96  # 2ms @48k
    REPORT_INTERVAL = 30.0

    def __init__(self, source: AudioSource, meter: AudioMeter) -> None:
        super().__init__()
        self._source = source  # 实为 SourceTap（订阅端），见 WebRTCService.handle_offer
        self._meter = meter
        self._timestamp = 0
        self._fade_pending = False
        self._tail: Optional[np.ndarray] = None  # 上一帧尾部（淡化用）
        self._underruns = 0
        # 诊断计数
        self._last_report = time.monotonic()
        self._report_underruns = 0

    @staticmethod
    def pacing_delay(queue_len: int) -> float:
        """按水位计算下一帧前的等待秒数（比例控制器）。"""
        delay = 0.02 + (RadioAudioTrack.TARGET_BACKLOG - queue_len) * 0.02 * 0.3
        return min(RadioAudioTrack.MAX_DELAY, max(RadioAudioTrack.MIN_DELAY, delay))

    def _declick(self, arr: np.ndarray) -> np.ndarray:
        """欠载（静音段）后恢复真实音频时做 2ms 淡入（0→1 渐起），消除咔声。"""
        n = min(self.FADE_SAMPLES, arr.size)
        if n > 0:
            fade = np.linspace(0.0, 1.0, n, dtype=np.float32)
            arr[:n] = np.round(arr[:n].astype(np.float32) * fade).astype(np.int16)
        self._tail = arr[-self.FADE_SAMPLES:].copy()
        return arr

    async def recv(self) -> AudioFrame:
        # 采集流的启动在 handle_offer（source.start → subscribe）中完成，
        # 这里只负责按水位 pacing 消费
        qlen = self._source.queue_len()
        data = self._source.read_frame()
        self._meter.update(data)

        if qlen is None:
            # 合成源（无时钟域问题）：按帧长定速
            await asyncio.sleep(0.02)
        else:
            await asyncio.sleep(self.pacing_delay(qlen))
            if qlen == 0:
                self._underruns += 1
                self._fade_pending = True  # 欠载后恢复真实音频时淡入

        arr = np.frombuffer(data, dtype=np.int16).copy()
        if self._fade_pending:
            arr = self._declick(arr)
            self._fade_pending = False
        else:
            self._tail = arr[-self.FADE_SAMPLES:].copy()

        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(arr.tobytes())
        frame.sample_rate = RATE
        self._timestamp += SAMPLES_PER_FRAME
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, RATE)

        self._maybe_report()
        return frame

    def _maybe_report(self) -> None:
        now = time.monotonic()
        if now - self._last_report < self.REPORT_INTERVAL:
            return
        overruns = getattr(self._source, "overruns", 0)
        underrun_delta = self._underruns - self._report_underruns
        self._report_underruns = self._underruns
        self._last_report = now
        if underrun_delta or overruns:
            log.info("音频诊断（%ds）: 欠载 %d 次, 采集溢出 %d | 累计欠载 %d —— "
                     "欠载持续增长说明消费跟不上采集时钟，请反馈",
                     int(self.REPORT_INTERVAL), underrun_delta, overruns, self._underruns)


class WebRTCService:
    """多会话 WebRTC：每个浏览器独立 PeerConnection + 独立采集订阅。

    下行：每会话独立 RadioAudioTrack（各自订阅队列），两个浏览器同时收听
    互不抢帧。上行：浏览器麦克风轨 → sink.feed()（预卷缓冲），PTT 锁存时
    播放；多会话时采用"最新连接的麦克风优先"门控（单操作员锁，避免两路
    麦克风交替污染发话 FIFO）。
    """

    def __init__(self, source: AudioSource, sink=None) -> None:
        self.source = source
        self.sink = sink
        self.meter = AudioMeter()
        self._pcs: set = set()
        self._peer_taps: dict = {}  # pc -> SourceTap
        self._peer_mics: dict = {}  # pc -> peer_id
        self._active_mic: Optional[str] = None

    async def handle_offer(self, sdp: str, mline_index: int = 0) -> dict:
        """处理浏览器的 offer，返回 answer（信令 WS 消息体）。"""
        self.source.start()  # 幂等
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        peer_id = f"peer{id(pc) & 0xFFFF:x}"
        tap = self.source.subscribe()
        self._pcs.add(pc)
        self._peer_taps[pc] = tap
        self._peer_mics[pc] = peer_id

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            log.info("WebRTC %s: %s", peer_id, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                self._pcs.discard(pc)
                t = self._peer_taps.pop(pc, None)
                if t is not None:
                    self.source.unsubscribe(t)
                if self._active_mic == self._peer_mics.get(pc):
                    self._active_mic = None
                self._peer_mics.pop(pc, None)
                if not self._pcs:
                    self.source.stop()

        @pc.on("track")
        def on_track(track) -> None:
            log.info("收到浏览器音轨: %s（%s，kind=%s）", track.id, peer_id, track.kind)
            # 操作员麦克风轨：持续喂给发话通路（预卷缓冲），PTT 锁存时播放
            async def consume() -> None:
                first_error = None
                try:
                    while True:
                        frame = await track.recv()
                        if self.sink is None:
                            continue
                        if self._active_mic is None:
                            self._active_mic = peer_id  # 首个到者声明
                        if self._active_mic == peer_id:
                            self.sink.feed(frame)
                except Exception as exc:
                    first_error = exc
                if first_error is not None:
                    log.warning("麦克风轨消费结束（%s）: %s", peer_id, first_error)
            asyncio.ensure_future(consume())

        pc.addTrack(RadioAudioTrack(tap, self.meter))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {"t": "rtc-answer", "sdp": pc.localDescription.sdp}

    async def close(self) -> None:
        for pc in list(self._pcs):
            await pc.close()
        self._pcs.clear()
        self._peer_taps.clear()
        self._peer_mics.clear()
        self._active_mic = None
        self.source.stop()
        if self.sink is not None:
            self.sink.stop()
