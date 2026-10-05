"""radio.protocol 单元测试：帧构建、流式解析、差分编解码、RF Log、端口识别。"""

import random

import pytest

from k1remote.radio.protocol import (
    FLAG_LED_GREEN,
    FLAG_LED_RED,
    FRAMEBUFFER_SIZE,
    FRAME_LINES,
    Key,
    RfLogFrame,
    ROW_STRUCT,
    ScreenFrame,
    ScreenState,
    StreamParser,
    apply_chunk,
    build_full_frame,
    keepalive_frame,
    key_frame,
    parse_row,
)
from k1remote.radio.ports import PortInfo, classify_ports, pick_aioc_port, pick_radio_port


# ---- 主机 → 对讲机 ----


def test_keepalive_frames():
    assert keepalive_frame() == bytes.fromhex("55aa0000")
    assert keepalive_frame(0x01) == bytes.fromhex("55aa0501")
    assert keepalive_frame(0x03 | 0x80) == bytes.fromhex("55aa0583")


def test_key_frames():
    assert key_frame(Key.UP) == bytes.fromhex("aa55030b")
    assert key_frame(Key.MENU, long_press=True) == bytes.fromhex("aa55040a")
    with pytest.raises(ValueError):
        key_frame(Key.PTT)  # 固件屏蔽 PTT 注入


def test_serial_ptt_frames():
    from k1remote.radio.protocol import ptt_frame
    assert ptt_frame(True) == bytes.fromhex("aa550601")
    assert ptt_frame(False) == bytes.fromhex("aa550600")


# ---- 差分块编码/解码 ----


def test_apply_chunk_bit_mapping():
    """帧缓冲为列主序：fb[行*128+列] 的 bit(y%8) = 像素 (x=列, y=行*8+位平面)。"""
    fb = bytearray(FRAMEBUFFER_SIZE)
    zeros = bytes(8)
    # idx=0 → line0, 位平面0, 左半行；payload[0] bit0 → 像素 (0, 0)
    apply_chunk(fb, 0, bytes([0x01]) + zeros[1:])
    assert fb[0] & 0x01
    # idx=1 → line0, 位平面0, 右半行；payload[0] bit0 → 像素 (64, 0)
    apply_chunk(fb, 1, bytes([0x01]) + zeros[1:])
    assert fb[64] & 0x01
    # idx=16 → line1, 位平面0, 左半行；payload[0] bit0 → 像素 (0, 8)
    apply_chunk(fb, 16, bytes([0x01]) + zeros[1:])
    assert fb[128] & 0x01
    # idx=17 → line1, 位平面0, 右半行；payload[7] bit7 → 像素 (127, 8)
    apply_chunk(fb, 17, bytes([0] * 7 + [0x80]))
    assert fb[128 + 127] & 0x01
    # idx=127 → line7, 位平面7, 右半行；payload[7] bit7 → 像素 (127, 63)
    apply_chunk(fb, 127, bytes([0] * 7 + [0x80]))
    assert fb[7 * 128 + 127] & 0x80


def test_chunk_overwrite_semantics():
    """块载荷是整块内容（非增量）：位由 1 变 0 时必须被清除。"""
    fb = bytearray(FRAMEBUFFER_SIZE)
    fb[0] = 0xFF
    # line0 列0 的 8 个位平面全写 0
    for plane in range(8):
        apply_chunk(fb, plane * 2, bytes(8))
    assert fb[0] == 0x00


def test_full_frame_roundtrip():
    rng = random.Random(42)
    original = bytes(rng.getrandbits(8) for _ in range(FRAMEBUFFER_SIZE))
    stream = build_full_frame(original, state_flags=FLAG_LED_RED | FLAG_LED_GREEN)
    parser = StreamParser()
    frames = parser.feed(stream)
    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame, ScreenFrame)
    assert frame.state_flags == FLAG_LED_RED | FLAG_LED_GREEN
    assert len(frame.chunks) == FRAME_LINES * 16
    state = ScreenState()
    state.apply(frame)
    assert bytes(state.framebuffer) == original


# ---- 流式解析 ----


def _wrap_frame(ftype: int, payload: bytes, flags: int = 0) -> bytes:
    return (bytes((0xF0 | flags, 0xAA, 0x55, ftype,
                   len(payload) >> 8, len(payload) & 0xFF))
            + payload + b"\x0a")


def test_parser_splits_across_feed_boundaries():
    fb = bytearray(FRAMEBUFFER_SIZE)
    fb[0] = 0xA5
    stream = build_full_frame(fb)
    parser = StreamParser()
    got = []
    for i in range(len(stream)):  # 逐字节喂入
        got.extend(parser.feed(stream[i:i + 1]))
    assert len(got) == 1 and len(got[0].chunks) == 128
    state = ScreenState()
    state.apply(got[0])
    assert bytes(state.framebuffer) == bytes(fb)


def test_parser_resync_on_garbage():
    stream = b"\x00\xff" * 10 + _wrap_frame(0x02, b"", flags=FLAG_LED_GREEN)
    parser = StreamParser()
    frames = parser.feed(stream)
    assert len(frames) == 1
    assert frames[0].state_flags == FLAG_LED_GREEN


def test_parser_marker_inside_payload():
    """差分载荷里出现 0xF0 系字节不应破坏后续帧解析。"""
    data = bytes([0xF5, 0xAA, 0x55, 0x02, 0x00, 0x03, 0x99, 0x42])
    payload = bytes([0x00]) + data  # 块 idx=0 + 8 字节载荷
    stream = (_wrap_frame(0x02, b"\x01" + bytes(8))
              + _wrap_frame(0x02, payload, flags=FLAG_LED_RED)
              + _wrap_frame(0x02, b"\x02" + bytes(8)))
    frames = StreamParser().feed(stream)
    assert [f.chunks[0][0] for f in frames] == [1, 0, 2]
    assert frames[1].chunks[0][1] == data
    assert frames[1].state_flags == FLAG_LED_RED


def test_parser_corrupt_end_byte_is_dropped():
    stream = _wrap_frame(0x02, b"")[:-1] + b"\xff" + _wrap_frame(0x02, b"")
    frames = StreamParser().feed(stream)
    assert len(frames) == 1


def test_parser_multiple_frames_single_feed():
    stream = _wrap_frame(0x02, b"") * 3
    assert len(StreamParser().feed(stream)) == 3


# ---- RF Log ----


def _make_row(freq=145500000, seq=1, dur=12, ch=3, flags=0x01, meter=42, batt=77, name=b"CH-03"):
    return ROW_STRUCT.pack(freq, seq, dur, ch, flags, meter, batt, name.ljust(10, b"\x00"))


def test_parse_row_fields():
    row = parse_row(_make_row(), 0)
    assert row.frequency == 145500000
    assert row.traffic_seq == 1
    assert row.duration_seconds == 12
    assert row.channel == 3
    assert row.is_tx and not row.is_session
    assert row.meter == 42 and row.batt_volt == 77
    assert row.channel_name == "CH-03"


def test_parse_row_empty_slot():
    assert parse_row(ROW_STRUCT.pack(0, 0, 0, 0, 0, 0, 0, bytes(10)), 0) is None


def test_rflog_disabled_status_packet():
    from k1remote.radio.protocol import STATUS_DISABLED
    frames = StreamParser().feed(_wrap_frame(0x05, bytes([2, STATUS_DISABLED, 0, 0])))
    assert len(frames) == 1
    assert isinstance(frames[0], RfLogFrame)
    assert frames[0].status_flags & STATUS_DISABLED
    assert frames[0].rows == []


def test_rflog_live_packet():
    from k1remote.radio.protocol import RF_LOG_LIVE_PACKET_SIZE, STATUS_ACTIVE
    payload = bytes([2, STATUS_ACTIVE, 64, 0]) + _make_row(seq=100) + b"\x00" * (64 * ROW_STRUCT.size)
    assert len(payload) == RF_LOG_LIVE_PACKET_SIZE
    frames = StreamParser().feed(_wrap_frame(0x05, payload))
    assert len(frames) == 1
    assert frames[0].rows[0].traffic_seq == 100
    assert len(frames[0].rows) == 1  # 空槽位被跳过


def test_rflog_version_mismatch_is_dropped():
    frames = StreamParser().feed(_wrap_frame(0x05, bytes([9, 0, 0, 0])))
    assert frames == []


# ---- 端口识别 ----


def test_port_classification():
    ports = [
        PortInfo("/dev/ttyACM0", 0x1209, 0x7388, "AIOC"),
        PortInfo("/dev/ttyACM1", 0x1234, 0x5678, "PY32 CDC"),
        PortInfo("/dev/ttyUSB0", 0x10C4, 0xEA60, "CP2102 编程线"),
        PortInfo("/dev/ttyAMA0", 0x0000, 0x0000, "板载 UART（蓝牙/控制台）"),
    ]
    grouped = classify_ports(ports)
    assert [p.device for p in grouped["aioc"]] == ["/dev/ttyACM0"]
    # radio 仅收录 USB 串口；板载 UART（vid=0）绝不触碰
    assert [p.device for p in grouped["radio"]] == ["/dev/ttyACM1", "/dev/ttyUSB0"]
    assert [p.device for p in grouped["other"]] == ["/dev/ttyAMA0"]
    assert pick_radio_port(ports).device == "/dev/ttyACM1"
    assert pick_aioc_port(ports).device == "/dev/ttyACM0"


def test_pick_radio_port_ignores_onboard_uart():
    """Pi 上只插了板载 UART（vid=0）时：绝不自动选中，宁可报错让人指定。"""
    ports = [PortInfo("/dev/ttyS0", 0x0000, 0x0000, "板载串口")]
    assert pick_radio_port(ports) is None
    assert classify_ports(ports)["other"][0].device == "/dev/ttyS0"


def test_pick_radio_port_excludes_aioc():
    ports = [PortInfo("COM5", 0x1209, 0x7388, "AIOC")]
    assert pick_radio_port(ports) is None
    assert pick_aioc_port(ports).device == "COM5"
