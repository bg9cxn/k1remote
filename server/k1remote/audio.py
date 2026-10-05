"""对讲机音频采集：AIOC 声卡 → 20ms 帧（48kHz mono s16）。

回调线程写入有界队列，WebRTC 轨按实时节奏拉取；欠载时补静音帧。
无声卡（演示模式）时用正弦源替代，接口一致。
"""

from __future__ import annotations

import collections
import logging
import math
import struct
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# AIOC 在各平台的设备名：Windows="AIOC Audio"、Linux ALSA="All-In-One-Cable:
# USB Audio"、部分固件="USB Audio CODEC"。按子串依次匹配。
AIOC_NAME_CANDIDATES = ("aioc", "all-in-one-cable", "usb audio")


def pick_device(devices, name: Optional[str], is_input: bool):
    """按服务端策略选设备：1) 名称匹配（自定名优先，缺省 AIOC 候选链）；
    2) 仅一个候选时直接用（无头树莓派上唯一声卡必然是 AIOC）；3) None=系统默认。

    返回 (index, note)；index 为 None 时 note 说明原因。
    """
    key = "max_input_channels" if is_input else "max_output_channels"
    role = "输入" if is_input else "输出"
    usable = [(i, d) for i, d in enumerate(devices) if d[key] > 0]
    if name:
        candidates = [name]
    else:
        candidates = list(AIOC_NAME_CANDIDATES)
    for cand in candidates:
        for idx, dev in usable:
            if cand.lower() in dev["name"].lower():
                note = f"匹配 {cand!r}"
                if not name and cand.lower() != "aioc":
                    note += "（AIOC 非 Windows 命名）"
                return idx, note
    if not name and len(usable) == 1:
        idx, dev = usable[0]
        return idx, f"系统仅有一个{role}设备，直接使用"
    if name:
        return None, f"未找到{name!r}，使用系统默认"
    return None, f"未匹配 AIOC 命名，使用系统默认{role}"

RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = RATE * FRAME_MS // 1000  # 960
BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2  # s16 mono

SILENCE = b"\x00" * BYTES_PER_FRAME


class AudioSource:
    """read_frame() -> 20ms s16 mono PCM 字节。"""

    def read_frame(self) -> bytes:
        raise NotImplementedError

    def queue_len(self) -> Optional[int]:
        """当前未消费帧数；None = 无队列反馈（合成源，按帧长定速）。"""
        return None

    def subscribe(self) -> "_SelfTap":
        """返回一个独立消费端（多会话各自订阅、互不抢帧）。

        基类缺省为"自享"型消费端（单消费者源，如合成音源）。
        """
        return _SelfTap(self)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _SelfTap:
    """基类源的"自享"消费端：直接代理源本身。"""

    def __init__(self, source: "AudioSource") -> None:
        self._source = source

    def read_frame(self) -> bytes:
        return self._source.read_frame()

    def queue_len(self) -> Optional[int]:
        return self._source.queue_len()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    overruns = 0


class SourceTap:
    """单消费者的独立采集队列——多个 WebRTC 会话各自订阅、互不抢帧。"""

    def __init__(self, parent: "SoundCardSource") -> None:
        self._parent = parent
        self._queue: collections.deque[bytes] = collections.deque(maxlen=15)
        self._lock = threading.Lock()

    @property
    def overruns(self) -> int:
        return self._parent.overruns

    def push(self, data: bytes) -> None:
        with self._lock:
            self._queue.append(data)  # 满则丢最旧（硬安全网）

    def read_frame(self) -> bytes:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
        return SILENCE  # 欠载补静音

    def queue_len(self) -> int:
        with self._lock:
            return len(self._queue)

    def start(self) -> None:
        pass  # 接口完备：采集流的生命周期由 SoundCardSource 管理

    def stop(self) -> None:
        pass


class SoundCardSource(AudioSource):
    """sounddevice 采集（回调线程 → 按订阅者 fan-out）。

    多会话设计：每个 WebRTC 会话通过 subscribe() 获得独立队列，采集回调
    把（增益处理后的）每帧复制分发给全部订阅者——两个浏览器同时收听互
    不抢帧。单会话延迟/时钟设计不变：会话队列硬上限 15 帧（300ms），消费
    速率由音轨按队列水位比例调节，自动跟随 AIOC 采集时钟。溢出计数用于
    诊断 USB/ALSA 层问题。
    """

    def __init__(self, device: Optional[str] = None, rate: int = RATE,
                 gain: float = 1.0) -> None:
        self.device_name = device
        self.rate = rate
        self.gain = gain
        self._taps: list[SourceTap] = []
        self.overruns = 0  # 采集回调 status 警告（ALSA/USB 层丢帧）
        self._stream = None

    def subscribe(self) -> SourceTap:
        self.start()  # 幂等：流已开则直接返回
        tap = SourceTap(self)
        self._taps.append(tap)
        log.info("采集订阅 +1（当前 %d 个会话）", len(self._taps))
        return tap

    def unsubscribe(self, tap: SourceTap) -> None:
        try:
            self._taps.remove(tap)
        except ValueError:
            pass
        log.info("采集订阅 -1（剩余 %d 个会话）", len(self._taps))

    def start(self) -> None:
        if self._stream is not None:
            return  # 幂等：多会话订阅时只开一次采集流
        import sounddevice as sd

        device_id = self._resolve_device(sd)
        self._stream = sd.InputStream(
            samplerate=self.rate, channels=1, dtype="int16",
            blocksize=SAMPLES_PER_FRAME, device=device_id,
            latency="low",
            callback=self._on_audio)
        self._stream.start()
        log.info("声卡采集已启动: %s @ %dHz (low latency)", device_id, self.rate)

    def _resolve_device(self, sd) -> Optional[int]:
        idx, note = pick_device(sd.query_devices(), self.device_name, is_input=True)
        log.info("收音输入设备: %s", note if idx is None else f"[{idx}] {sd.query_devices()[idx]['name']} ({note})")
        return idx

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status:
            self.overruns += 1
            log.debug("声卡溢出 #%d: %s", self.overruns, status)
        if self.gain != 1.0:
            arr = np.clip(indata.astype(np.float32) * self.gain,
                          -32768, 32767).astype(np.int16)
            data = arr.tobytes()
        else:
            data = bytes(indata)
        for tap in tuple(self._taps):  # fan-out：每个会话独立队列
            tap.push(data)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            log.info("声卡采集已停止")


class SineSource(AudioSource):
    """演示模式：440Hz 正弦（-18dBFS），用于无硬件验证 WebRTC 链路。

    多会话：subscribe() 返回独立相位的正弦源，两个浏览器各自听到连续
    正弦（共享相位会导致交替错帧）。
    """

    def __init__(self, freq: float = 440.0, rate: int = RATE) -> None:
        self.freq = freq
        self.rate = rate
        self._phase = 0.0

    def subscribe(self) -> "_SelfTap":
        return _SelfTap(SineSource(self.freq, self.rate))

    def read_frame(self) -> bytes:
        step = 2 * math.pi * self.freq / self.rate
        amp = 32767 * 0.125
        samples = bytearray()
        for _ in range(SAMPLES_PER_FRAME):
            samples += struct.pack("<h", int(amp * math.sin(self._phase)))
            self._phase += step
        return bytes(samples)


class AudioMeter:
    """供状态面板用的简易电平（dBFS），随 read_frame 更新。"""

    def __init__(self) -> None:
        self.dbfs: float = -120.0
        self._last_update = 0.0

    def update(self, frame: bytes) -> None:
        now = time.monotonic()
        if now - self._last_update < 0.25:
            return
        self._last_update = now
        count = len(frame) // 2
        if not count:
            return
        acc = 0
        for i in range(0, len(frame), 2):
            sample = struct.unpack_from("<h", frame, i)[0]
            acc += sample * sample
        rms = math.sqrt(acc / count)
        self.dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -120.0


# ---- 播放端（对讲机发话通路：浏览器麦克风 → AIOC 输出）----

_RESAMPLER = None  # 懒初始化（av 导入较重）


def frame_to_pcm48k(frame) -> bytes:
    """任意 av.AudioFrame → 48kHz mono s16 PCM 字节。"""
    global _RESAMPLER
    if _RESAMPLER is None:
        import av
        _RESAMPLER = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks = []
    for out in _RESAMPLER.resample(frame):
        chunks.append(out.to_ndarray().tobytes())
    return b"".join(chunks)


class SoundCardSink:
    """AIOC 播放端。

    操作员麦克风轨持续 feed() 进字节流 FIFO（保留最近 ~120ms），PTT 锁存
    时开启播放：先播预卷（避免吞掉第一个音节），再实时跟随；释放即停并
    清空缓冲。FIFO 按字节精确取数，与上游帧长解耦。电平/偏差由 AIOC
    硬件输出电平决定（aioc-util 可调）。
    """

    FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 20ms @48k s16 mono

    def __init__(self, device: Optional[str] = None, rate: int = RATE,
                 preroll_frames: int = 6, gain: float = 1.0) -> None:
        self.device_name = device
        self.rate = rate
        self.gain = gain
        self.preroll_bytes = max(preroll_frames, 2) * self.FRAME_BYTES
        self.max_bytes = 25 * self.FRAME_BYTES  # 硬上限 ~500ms
        self._buf = bytearray()
        self._enabled = False
        self._lock = threading.Lock()
        self._stream = None
        self._fed_frames = 0

    def feed(self, frame) -> None:
        data = frame_to_pcm48k(frame)
        self._fed_frames += 1
        if self._fed_frames % 250 == 1:
            log.info("麦克风轨已接入（累计 %d 帧）", self._fed_frames)
        if len(data) % 2:  # 重采样可能产生奇数字节尾，丢掉半样本
            data = data[:len(data) - 1]
        if not data:
            return
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) > self.max_bytes:  # 丢旧保实时
                del self._buf[:len(self._buf) - self.max_bytes]

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            if on and not self._enabled:
                # 只保留最近 preroll 字节，丢弃更早的积压
                if len(self._buf) > self.preroll_bytes:
                    del self._buf[:len(self._buf) - self.preroll_bytes]
                try:
                    self._ensure_stream()
                except Exception as exc:
                    # 打开失败绝不向上抛——异常曾沿 on_hold_change → WS 处理器
                    # → 断线撤发，表现为"按下约 1 秒后发射自动停止"。
                    # 下次按下自动重试。
                    log.warning("发话输出声卡打开失败（下次 PTT 重试）: %s", exc)
                    return
                log.info("发话通路开启（预卷 %.0fms）",
                         len(self._buf) / self.rate / 2 * 1000)
            elif not on and self._enabled:
                self._buf.clear()
                log.info("发话通路关闭")
            self._enabled = on

    def _ensure_stream(self) -> None:
        """打开发话输出流。候选顺序：匹配命中的直接设备 → 该设备的 Pulse 封装
        （树莓派 PipeWire/PulseAudio 常占用直连设备）→ 系统默认。任一候选
        open/start 失败即尝试下一个，全部失败则抛出（由 set_enabled 捕获）。"""
        if self._stream is not None:
            return
        import sounddevice as sd
        devices = sd.query_devices()
        idx, note = pick_device(devices, self.device_name, is_input=False)
        candidates: list = []
        if idx is not None:
            candidates.append((idx, f"直接设备 ({note})"))
        for cand in ("pulse", "default"):
            for i, dev in enumerate(devices):
                if dev["name"].lower() == cand and dev["max_output_channels"] > 0:
                    candidates.append((i, f"{cand}（经音频服务器路由）"))
                    break
        last_exc: Optional[Exception] = None
        for dev_idx, label in candidates:
            try:
                stream = sd.OutputStream(
                    samplerate=self.rate, channels=1, dtype="int16",
                    blocksize=SAMPLES_PER_FRAME, device=dev_idx,
                    latency="low",
                    callback=self._on_playback)
                stream.start()
            except Exception as exc:
                log.warning("输出设备 [%d] 打开失败（%s）: %s", dev_idx, label, exc)
                last_exc = exc
                continue
            self._stream = stream
            log.info("发话输出设备: [%d] %s (%s)",
                     dev_idx, devices[dev_idx]["name"], label)
            return
        raise last_exc or RuntimeError("无可用输出设备")
        self._stream.start()

    def _on_playback(self, outdata, frames, time_info, status) -> None:
        if status:
            log.debug("播放欠载: %s", status)
        need = frames * 2
        out = bytearray(need)
        with self._lock:
            if self._enabled and self._buf:
                take = min(need, len(self._buf))
                out[:take] = self._buf[:take]
                del self._buf[:take]
        arr = np.frombuffer(bytes(out), dtype=np.int16)
        if self.gain != 1.0:
            arr = np.clip(arr.astype(np.float32) * self.gain,
                          -32768, 32767).astype(np.int16)
        outdata[:] = arr.reshape(-1, 1)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._enabled = False
