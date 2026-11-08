"""webapp.should_wake 单元测试：SetOff 深睡唤醒判定。

背景：main only 空闲时固件推流静默（进 PS 前无任何帧），若按"停滞"判定
会每 10s 误注入 EXIT，按键蜂鸣且反复重置省电倒计时。判定必须以固件上报的
FLAG_DEEP_SLEEP 为准。
"""

from k1remote.radio.protocol import FLAG_DEEP_SLEEP, FLAG_LED_GREEN
from k1remote.webapp import should_wake

NOW = 1000.0


def test_normal_idle_never_wakes():
    # 正常空闲：省电慢流不带深睡标志，流短暂静默也不得注入
    assert not should_wake(0, NOW - 60.0, 0.0, NOW)
    assert not should_wake(FLAG_LED_GREEN, NOW - 60.0, 0.0, NOW)


def test_deep_sleep_with_fresh_evidence_wakes():
    # 深睡标志 + 该帧晚于上次注入 → 唤醒
    assert should_wake(FLAG_DEEP_SLEEP, NOW - 30.0, NOW - 40.0, NOW)
    assert should_wake(FLAG_DEEP_SLEEP | FLAG_LED_GREEN, NOW - 0.5, NOW - 40.0, NOW)


def test_no_evidence_after_last_wake_does_not_rearm():
    # 注入后再无新帧（对讲机可能已醒但暂无流）：不得凭旧标志重复注入
    assert not should_wake(FLAG_DEEP_SLEEP, NOW - 30.0, NOW - 20.0, NOW)
    assert not should_wake(FLAG_DEEP_SLEEP, NOW - 30.0, NOW - 30.0, NOW)


def test_no_frame_at_all_does_not_wake():
    # 尚未收到过任何帧：无从判定深睡，不注入
    assert not should_wake(FLAG_DEEP_SLEEP, None, 0.0, NOW)


def test_min_interval_between_wakes():
    # 新证据存在但距上次注入不足 10s：等待间隔
    assert not should_wake(FLAG_DEEP_SLEEP, NOW - 1.0, NOW - 5.0, NOW)
    assert should_wake(FLAG_DEEP_SLEEP, NOW - 1.0, NOW - 10.5, NOW)


# ---- Hub：最近 RF Log 行缓存（hello 快照数据源，v3 专属） ----


def _wrap_frame(ftype: int, payload: bytes, flags: int = 0) -> bytes:
    return (bytes((0xF0 | flags, 0xAA, 0x55, ftype,
                   len(payload) >> 8, len(payload) & 0xFF))
            + payload + b"\x0a")


def _live_rflog_frame(seq: int):
    from k1remote.radio.protocol import (
        ROW_STRUCT,
        STATUS_ACTIVE,
        StreamParser,
    )
    row = ROW_STRUCT.pack(145500000, seq, 12, 3, 0x01, 42, 77, b"CH-03".ljust(10, b"\x00"))
    payload = bytes([2, STATUS_ACTIVE, 64, 0]) + row + b"\x00" * (64 * ROW_STRUCT.size)
    return StreamParser().feed(_wrap_frame(0x05, payload))[0]


def test_hub_caches_last_rf_row():
    from k1remote.webapp import Hub
    hub = Hub()  # 无 loop/客户端：广播为空操作，只剩缓存副作用
    hub._handle_frame(_live_rflog_frame(seq=100))
    assert hub.last_rf_row is not None
    assert hub.last_rf_row["seq"] == 100
    assert hub.last_rf_row["volts"] == 6.77
    hub._handle_frame(_live_rflog_frame(seq=101))
    assert hub.last_rf_row["seq"] == 101
