"""K5Viewer 串口协议编解码（纯逻辑，无 I/O，可完整单测）。

字节格式与字段语义见 docs/protocol-k5viewer.md，全部对照固件源码整理：
App/k5viewer.c（屏幕差分流）、App/driver/keyboard.c（按键注入）、
App/driver/vcp.c（主机→对讲机帧）、App/app/rxtx_log.*（RF Log 包）。
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

# ---- 屏幕几何（固件 driver/st7565.h）----
LCD_WIDTH = 128
LCD_HEIGHT = 64
FRAME_LINES = 8  # 状态行 + 7 主行
FRAMEBUFFER_SIZE = FRAME_LINES * LCD_WIDTH  # 1024 个垂直字节

# ---- 帧标记与状态位（App/k5viewer.c）----
MARKER_BASE = 0xF0  # 高 4 位固定，低 4 位为 stateFlags
MARKER_MASK = 0xF8

FLAG_DEEP_SLEEP = 1 << 0
FLAG_LED_RED = 1 << 1  # 红灯 = 正在发射 (TX)
FLAG_LED_GREEN = 1 << 2  # 绿灯 = 正在接收 (RX)

FRAME_SYNC0 = 0xAA
FRAME_SYNC1 = 0x55
FRAME_END = 0x0A

TYPE_DIFF = 0x02
TYPE_RXTX_LOG = 0x05
TYPE_RXTX_LOG_HISTORY = 0x06
KNOWN_TYPES = (TYPE_DIFF, TYPE_RXTX_LOG, TYPE_RXTX_LOG_HISTORY)

# 重同步时的帧长上限，防止乱码把解析器带偏
MAX_FRAME_PAYLOAD = 4096

# ---- 主机→对讲机（App/driver/keyboard.c / vcp.c）----
KEEPALIVE = bytes((0x55, 0xAA, 0x00, 0x00))

FEATURE_RF_LOG = 0x01
FEATURE_RF_LOG_HISTORY = 0x02
FEATURE_RF_LOG_RESTART = 0x80

KEY_SHORT = 0x03
KEY_LONG = 0x04
PTT_HOLD = 0x06  # 自研 v1 固件扩展：AA 55 06 <0|1> = 串口 PTT 保持/释放（v3 不识别，无害）


class Key(enum.IntEnum):
    """keycode，与固件 KEY_Code_e 一致。KEY_PTT 被固件屏蔽注入。"""

    K0 = 0
    K1 = 1
    K2 = 2
    K3 = 3
    K4 = 4
    K5 = 5
    K6 = 6
    K7 = 7
    K8 = 8
    K9 = 9
    MENU = 10
    UP = 11
    DOWN = 12
    EXIT = 13
    STAR = 14
    F = 15
    PTT = 16  # 注入被固件忽略，仅保留枚举完整性
    SIDE2 = 17
    SIDE1 = 18


KEY_NAMES: dict = {
    "0": Key.K0, "1": Key.K1, "2": Key.K2, "3": Key.K3, "4": Key.K4,
    "5": Key.K5, "6": Key.K6, "7": Key.K7, "8": Key.K8, "9": Key.K9,
    "menu": Key.MENU, "up": Key.UP, "down": Key.DOWN, "exit": Key.EXIT,
    "star": Key.STAR, "f": Key.F, "side1": Key.SIDE1, "side2": Key.SIDE2,
}


def keycode_from_name(name: str) -> Optional[Key]:
    return KEY_NAMES.get(name.strip().lower())


def keepalive_frame(features: int = 0) -> bytes:
    """保活帧；features 非零时使用扩展格式告知固件启用 RF Log 等扩展。"""
    if features:
        return bytes((0x55, 0xAA, 0x05, features & 0xFF))
    return KEEPALIVE


def key_frame(key: Key, long_press: bool = False) -> bytes:
    if key == Key.PTT:
        raise ValueError("固件屏蔽 KEY_PTT 注入，远程发射请走 ptt_frame()（串口 PTT）")
    return bytes((FRAME_SYNC0, FRAME_SYNC1, KEY_LONG if long_press else KEY_SHORT, int(key)))


def ptt_frame(hold: bool) -> bytes:
    """串口 PTT 帧（自研 v1 固件扩展）：hold=True 键上发射，False 释放。

    释放保障：服务端看门狗必须主动发送 False；固件侧 TxTOut 兜底；标志为
    RAM 变量，对讲机断电自然清除。发射期间应暂停保活（避免 UART 数据混入
    麦克通路），释放帧发送后再恢复。
    """
    return bytes((FRAME_SYNC0, FRAME_SYNC1, PTT_HOLD, 1 if hold else 0))


# ---- 屏幕差分块 ----

def apply_chunk(fb: bytearray, idx: int, payload: bytes) -> None:
    """把一个 9 字节块（索引 + 8 字节位平面）合并进帧缓冲。

    fb 布局与固件一致：fb[line*128 + col] 为垂直字节（bit = 行内 y）。
    块载荷是该 8x8 块的完整内容（非增量），因此按位覆盖而非 OR。
    """
    if not 0 <= idx < FRAME_LINES * 16:
        raise ValueError(f"chunk index out of range: {idx}")
    if len(payload) != 8:
        raise ValueError("chunk payload must be 8 bytes")
    line, rest = divmod(idx, 16)
    bit, half = divmod(rest, 2)
    bit_mask = 1 << bit
    col_base = half * (LCD_WIDTH // 2)
    base = line * LCD_WIDTH + col_base
    for j in range(8):
        b = payload[j]
        col = base + (j << 3)
        for k in range(8):
            if b & (1 << k):
                fb[col + k] |= bit_mask
            else:
                fb[col + k] &= ~bit_mask

def encode_chunk(chunk_idx: int, fb: bytes) -> bytes:
    """帧缓冲 → 8 字节块载荷，逐位对应固件 K5VIEWER_Chunk()（含反显由固件完成，此处不处理）。"""
    line, rest = divmod(chunk_idx, 16)
    bit, half = divmod(rest, 2)
    col_base = half * (LCD_WIDTH // 2)
    src = (line * LCD_WIDTH) + col_base
    out = bytearray(8)
    bit_mask = 1 << bit
    for j in range(8):
        acc = 0
        for k in range(8):
            if fb[src + j * 8 + k] & bit_mask:
                acc |= 1 << k
        out[j] = acc
    return bytes(out)


def build_full_frame(fb: bytes, state_flags: int = 0) -> bytes:
    """完整屏幕流帧（128 块全量），用于测试与演示模式。"""
    parts = [bytes((MARKER_BASE | (state_flags & 0x07),)),
             FRAME_SYNC0.to_bytes(1, "big"), FRAME_SYNC1.to_bytes(1, "big"),
             TYPE_DIFF.to_bytes(1, "big"), (128 * 9).to_bytes(2, "big")]
    for idx in range(FRAME_LINES * 16):
        parts.append(bytes((idx,)) + encode_chunk(idx, fb))
    parts.append(FRAME_END.to_bytes(1, "big"))
    return b"".join(parts)


# ---- RF Log 行（App/app/rxtx_log.h，packed 25 字节，小端）----
ROW_STRUCT = struct.Struct("<IIHHBBB10s")

RF_LOG_VERSION = 2
RF_LOG_ROW_COUNT = 64
RF_LOG_STATUS_PACKET_SIZE = 4
RF_LOG_LIVE_PACKET_SIZE = 4 + (RF_LOG_ROW_COUNT + 1) * ROW_STRUCT.size  # 1629
RF_LOG_HISTORY_PACKET_SIZE = RF_LOG_ROW_COUNT * ROW_STRUCT.size  # 1600

STATUS_ACTIVE = 1 << 0
STATUS_HAS_TRAFFIC = 1 << 1
STATUS_CLEARING = 1 << 2
STATUS_DISABLED = 1 << 3

ROW_FLAG_TX = 1 << 0
ROW_FLAG_MONITOR = 1 << 2
ROW_FLAG_SESSION = 1 << 3


@dataclass
class RfLogRow:
    frequency: int  # Hz
    traffic_seq: int
    duration_seconds: int
    channel: int
    flags: int
    meter: int
    batt_volt: int
    channel_name: str

    @property
    def is_tx(self) -> bool:
        return bool(self.flags & ROW_FLAG_TX)

    @property
    def is_session(self) -> bool:
        return bool(self.flags & ROW_FLAG_SESSION)

    def as_dict(self) -> dict:
        return {
            "freq": self.frequency, "seq": self.traffic_seq,
            "dur": self.duration_seconds, "ch": self.channel,
            "tx": self.is_tx, "meter": self.meter, "batt": self.batt_volt,
            "name": self.channel_name,
        }


def parse_row(buf: bytes, offset: int) -> Optional[RfLogRow]:
    (frequency, seq, duration, channel, flags,
     meter, batt, name) = ROW_STRUCT.unpack_from(buf, offset)
    if frequency == 0 and seq == 0:
        return None  # 无效槽位
    return RfLogRow(frequency, seq, duration, channel, flags, meter, batt,
                    name.split(b"\x00", 1)[0].decode("ascii", "replace"))


# ---- 解析出的帧对象 ----


@dataclass
class ScreenFrame:
    state_flags: int
    chunks: List[Tuple[int, bytes]]


@dataclass
class RfLogFrame:
    state_flags: int
    status_flags: int
    rows: List[RfLogRow]


@dataclass
class RfLogHistoryFrame:
    state_flags: int
    rows: List[RfLogRow]


Frame = Union[ScreenFrame, RfLogFrame, RfLogHistoryFrame]


class StreamParser:
    """增量字节流解析器。

    每帧以 `0xF0|stateFlags` 标记字节开头，随后 [AA 55 type lenHi lenLo payload 0x0A]。
    校验失败时丢弃标记字节并重新扫描（乱码自愈）；一次 feed 可产出多帧
    （固件一次更新可能连发 屏幕帧 + RF Log 帧，共用一个标记字节）。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._marker_flags = 0

    def feed(self, data: bytes) -> List[Frame]:
        self._buf.extend(data)
        frames: List[Frame] = []
        while True:
            frame = self._parse_one()
            if frame is None:
                break
            frames.append(frame)
        return frames

    def _parse_one(self):
        buf = self._buf
        while True:
            # 扫描标记字节
            marker_pos = -1
            for i, b in enumerate(buf):
                if b & MARKER_MASK == MARKER_BASE:
                    marker_pos = i
                    break
            if marker_pos < 0:
                buf.clear()
                return None
            if marker_pos > 0:
                del buf[:marker_pos]
            self._marker_flags = buf[0] & ~MARKER_MASK

            if len(buf) < 6:  # 标记 + 5 字节帧头
                return None
            sync0, sync1, ftype, len_hi, len_lo = buf[1:6]
            length = (len_hi << 8) | len_lo
            if (sync0 != FRAME_SYNC0 or sync1 != FRAME_SYNC1
                    or ftype not in KNOWN_TYPES or length > MAX_FRAME_PAYLOAD):
                del buf[:1]  # 假标记，继续扫描
                continue

            total = 6 + length + 1
            if len(buf) < total:
                return None
            payload = bytes(buf[6:6 + length])
            end = buf[6 + length]
            del buf[:total]
            if end != FRAME_END:
                continue  # 帧尾损坏，丢弃

            if ftype == TYPE_DIFF:
                return self._parse_diff(payload)
            if ftype == TYPE_RXTX_LOG:
                return self._parse_rflog(payload)
            return self._parse_rflog_history(payload)

    def _parse_diff(self, payload: bytes) -> Optional[ScreenFrame]:
        if len(payload) % 9 != 0:
            return None
        chunks: List[Tuple[int, bytes]] = []
        for off in range(0, len(payload), 9):
            idx = payload[off]
            if idx >= FRAME_LINES * 16:
                return None
            chunks.append((idx, payload[off + 1:off + 9]))
        return ScreenFrame(self._marker_flags, chunks)

    def _parse_rflog(self, payload: bytes) -> Optional[RfLogFrame]:
        if len(payload) < RF_LOG_STATUS_PACKET_SIZE:
            return None
        version = payload[0]
        if version != RF_LOG_VERSION:
            return None
        status_flags = payload[1]
        row_count = payload[2]
        expected = RF_LOG_STATUS_PACKET_SIZE + (row_count + 1) * ROW_STRUCT.size if row_count else RF_LOG_STATUS_PACKET_SIZE
        if len(payload) != expected:
            return None
        rows: List[RfLogRow] = []
        off = RF_LOG_STATUS_PACKET_SIZE
        # 启用包 = 头 + 当前活动行 + row_count 个历史槽位；停用包只有头
        slots = (row_count + 1) if row_count else 0
        for _ in range(slots):
            row = parse_row(payload, off)
            if row is not None:
                rows.append(row)
            off += ROW_STRUCT.size
        return RfLogFrame(self._marker_flags, status_flags, rows)

    def _parse_rflog_history(self, payload: bytes) -> Optional[RfLogHistoryFrame]:
        if len(payload) != RF_LOG_HISTORY_PACKET_SIZE:
            return None
        rows = []
        for off in range(0, len(payload), ROW_STRUCT.size):
            row = parse_row(payload, off)
            if row is not None:
                rows.append(row)
        return RfLogHistoryFrame(self._marker_flags, rows)


class ScreenState:
    """累积屏幕差分 → 完整 128×64 帧缓冲。重连后固件强制推整帧，自动纠偏。"""

    def __init__(self) -> None:
        self.framebuffer = bytearray(FRAMEBUFFER_SIZE)
        self.state_flags = 0
        self.received_any = False

    def apply(self, frame: ScreenFrame) -> None:
        self.state_flags = frame.state_flags
        self.received_any = True
        for idx, payload in frame.chunks:
            apply_chunk(self.framebuffer, idx, payload)

    @property
    def led_tx(self) -> bool:
        return bool(self.state_flags & FLAG_LED_RED)

    @property
    def led_rx(self) -> bool:
        return bool(self.state_flags & FLAG_LED_GREEN)
