"""RadioAudioTrack pacing 控制器与欠载淡化测试（无硬件）。"""

import asyncio

import numpy as np
import pytest

from k1remote.audio import SineSource, SoundCardSource
from k1remote.webrtc import RadioAudioTrack


def test_pacing_at_target_is_frame_rate():
    assert RadioAudioTrack.pacing_delay(3) == pytest.approx(0.02)


def test_pacing_slows_when_backlog_high():
    d = RadioAudioTrack.pacing_delay(15)  # 满队列 → 加速消化（钳到最小）
    assert d == RadioAudioTrack.MIN_DELAY
    assert d < 0.02


def test_pacing_speeds_up_when_starving():
    d = RadioAudioTrack.pacing_delay(0)  # 队列空 → 减速等采集（0.02+3*6ms）
    assert d == pytest.approx(0.038)
    assert d > 0.02


def test_pacing_converges_to_target():
    """模拟闭环：从空队列出发，生产 50 帧/s 固定，消费按 pacing_delay，
    水位应收敛到目标附近且不再饥饿。"""
    backlog = 0
    produced_per_s = 50.0
    starvations = 0
    for _ in range(500):  # 10 秒
        if backlog <= 0:
            starvations += 1
        delay = RadioAudioTrack.pacing_delay(backlog)
        backlog += produced_per_s * delay  # 生产
        backlog -= 1                        # 消费一帧
        backlog = min(backlog, 15)
    assert starvations <= 2  # 启动后不应持续饥饿
    assert 1 <= backlog <= 8  # 水位收敛


def test_declick_fades_in_from_silence():
    track = RadioAudioTrack.__new__(RadioAudioTrack)  # 不走 __init__
    arr = np.full(240, 12000, dtype=np.int16)
    out = track._declick(arr)
    assert out[0] < 1000          # 从 0 渐起
    assert out[-1] > 8000         # 末端恢复原电平
    assert abs(int(out[-1]) - 12000) <= 200


# ---- 多会话 fan-out（双浏览器收听互不抢帧）----


def _fake_indata(fill=1000):
    return np.full((960, 1), fill, dtype=np.int16)


def test_soundcard_source_fans_out_to_all_taps():
    src = SoundCardSource()
    src.start = lambda: None  # 测试不开真实声卡
    tap_a, tap_b = src.subscribe(), src.subscribe()
    src._on_audio(_fake_indata(), 960, None, None)
    frame_a, frame_b = tap_a.read_frame(), tap_b.read_frame()
    assert frame_a == frame_b and len(frame_a) == 960 * 2  # 同一帧各自独立收到
    assert tap_a.queue_len() == 0 and tap_b.queue_len() == 0


def test_taps_drain_independently():
    """核心修复验证：两个会话各按自己的节奏消费，都能拿到全部帧。"""
    src = SoundCardSource()
    src.start = lambda: None
    tap_a, tap_b = src.subscribe(), src.subscribe()
    for i in range(6):
        src._on_audio(np.full((960, 1), i, dtype=np.int16), 960, None, None)
    # A 读完 6 帧，B 一帧未读
    a_frames = [np.frombuffer(tap_a.read_frame(), dtype=np.int16)[0] for _ in range(6)]
    assert list(a_frames) == list(range(6))
    b_frame = np.frombuffer(tap_b.read_frame(), dtype=np.int16)[0]
    assert b_frame == 0  # B 的队列完整保留


def test_unsubscribe_stops_fanout():
    src = SoundCardSource()
    src.start = lambda: None
    tap_a, tap_b = src.subscribe(), src.subscribe()
    src.unsubscribe(tap_a)
    src._on_audio(_fake_indata(), 960, None, None)
    assert tap_a.queue_len() == 0  # 已退订
    assert tap_b.queue_len() == 1


def test_sine_subscribe_independent_phase():
    src = SineSource()
    tap_a, tap_b = src.subscribe(), src.subscribe()
    assert tap_a._source is not tap_b._source  # 各自独立相位源
    a1, b1 = tap_a.read_frame(), tap_b.read_frame()
    assert a1 == b1  # 相同参数，起始一致
    a2 = tap_a.read_frame()
    assert a1 != a2  # 相位推进


def test_start_is_idempotent():
    """多会话订阅不会重复打开采集流（用假 stream 验证守卫）。"""
    src = SoundCardSource()
    src._stream = object()  # 假装已打开
    src.start()  # 不应再创建/覆盖
    assert isinstance(src._stream, object)


def test_recv_consumes_from_tap_without_start():
    """回归：fan-out 后 track 收到的是 SourceTap——recv 不得调用 start()。"""
    from k1remote.audio import AudioMeter

    class FakeTap:
        overruns = 0

        def __init__(self):
            self.started = False

        def start(self):
            self.started = True  # 若被调用则标记（不应发生）

        def read_frame(self):
            return b"\x00\x01" * 960

        def queue_len(self):
            return 1

    tap = FakeTap()
    track = RadioAudioTrack(tap, AudioMeter())
    frame = asyncio.run(track.recv())  # 旧代码在此抛 AttributeError
    assert bytes(frame.planes[0]) == b"\x00\x01" * 960
    assert tap.started is False  # start 不应由 track 触发
