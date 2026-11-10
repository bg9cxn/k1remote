"""带内 FEC 的 Opus 编码器：ctypes 直连系统 libopus（无新增依赖）。

背景：aiortc 的编码链（PyAV/FFmpeg 的 libopus 封装）不暴露 OPUS_SET_INBAND_FEC
——SDP 里声明了 useinbandfec=1，编码器却从未真正生成 FEC（浏览器端
fecPacketsReceived 恒为 0），公网丢包只能靠接收端 PLC 现场合成（听感"数码音"）。
本模块用系统 libopus（≥1.3，Debian 自带 libopus0）自建编码器并开启 LBRR 带内
前向纠错：丢帧由下一包携带的冗余恢复，替代 PLC 合成。

实现说明：
- 复用 aiortc OpusEncoder 的重采样（48kHz 立体声 s16，20ms 帧）与时间戳簿记，
  仅替换编码步骤；接口与 aiortc Encoder 完全一致（encode -> (payloads, ts)）。
- opus_encoder_ctl 是变参函数，Linux aarch64 整型变参走通用寄存器，ctypes 传
  c_int 可靠（opuslib 同款做法）。
- 系统 libopus 缺失/加载失败时抛 OpusFecError，由调用方回退 aiortc 原生编码器
  （仅无 FEC，无功能损失）；CDLL 的外部调用释放 GIL，回调线程特性不变。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
from typing import Optional

from av import AudioFrame

from aiortc.codecs.opus import OpusEncoder

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960
TIME_BASE_NUM = 1  # 时间基 1/48000，时间戳每帧 +960，与 aiortc 一致

# opus_defines.h（偶数 = SET 请求）
OPUS_APPLICATION_VOIP = 2048
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_INBAND_FEC_REQUEST = 4012
OPUS_SET_PACKET_LOSS_PERC_REQUEST = 4014

LIB_NAMES = ("libopus.so.0", "libopus.so", "libopus-0.dll", "libopus.dll")


class OpusFecError(Exception):
    """系统 libopus 不可用或初始化失败。"""


def load_libopus() -> Optional[ctypes.CDLL]:
    """按候选名加载系统 libopus；Windows 开发机找不到时返回 None。"""
    for name in LIB_NAMES:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    found = ctypes.util.find_library("opus")
    if found:
        try:
            return ctypes.CDLL(found)
        except OSError:
            pass
    return None


class FecOpusEncoder(OpusEncoder):
    """aiortc OpusEncoder 的带 FEC 变体。

    __init__ 在系统 libopus 不可用时抛 OpusFecError（调用方负责回退）；
    encode 的行为/返回与 aiortc Encoder 契约完全一致。
    """

    def __init__(self, loss_perc: int) -> None:
        super().__init__()  # 保留其重采样器（48k 立体声 s16，960 帧）
        lib = load_libopus()
        if lib is None:
            raise OpusFecError("未找到系统 libopus 共享库")
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = (
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int))
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encode.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int)

        err = ctypes.c_int(-1)
        enc = lib.opus_encoder_create(
            SAMPLE_RATE, 2, OPUS_APPLICATION_VOIP, ctypes.byref(err))
        if not enc or err.value != 0:
            raise OpusFecError("opus_encoder_create 失败: err=%d" % err.value)

        lib.opus_encoder_ctl(enc, OPUS_SET_BITRATE_REQUEST, 96000)
        lib.opus_encoder_ctl(enc, OPUS_SET_INBAND_FEC_REQUEST, 1)
        lib.opus_encoder_ctl(enc, OPUS_SET_PACKET_LOSS_PERC_REQUEST, loss_perc)

        self._lib = lib
        self._enc = ctypes.c_void_p(enc)
        self._buf = ctypes.create_string_buffer(4000)
        self._timestamp = 0
        log.info("Opus 带内 FEC 编码器就绪（voip，预期丢包 %d%%，系统 libopus）",
                 loss_perc)

    def encode(self, frame: AudioFrame, force_keyframe: bool = False):
        if self._enc is None:
            return super().encode(frame, force_keyframe)

        payloads = []
        for rf in self.resampler.resample(frame):
            pcm = bytes(rf.planes[0])
            n = self._lib.opus_encode(
                self._enc, pcm, rf.samples, self._buf, len(self._buf))
            if n < 0:
                log.warning("opus_encode 失败: %d（丢弃本帧）", n)
                continue
            payloads.append(self._buf.raw[:n])

        if not payloads:
            return [], None
        ts = self._timestamp
        self._timestamp += len(payloads) * SAMPLES_PER_FRAME
        return payloads, ts

    def __del__(self) -> None:
        try:
            if getattr(self, "_enc", None):
                self._lib.opus_encoder_destroy(self._enc)
                self._enc = ctypes.c_void_p(0)
        except Exception:
            pass  # 解释器退出时的清理，尽力而为
