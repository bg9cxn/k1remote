"""K1Remote 服务端配置（TOML，可选；缺省值即可直接运行）。

固件变体：
  v1   = uv-k5-firmware-custom-remote（UV-K5 V1 硬件，DP32G030，UART 38400，
         保活仅支持纯 `55 AA 00 00`，无 RF Log 扩展）
  v3   = uv-k1-k5v3-firmware-custom（UV-K1/UV-K5 V3 硬件，PY32F071，USB CDC，
         支持扩展保活与 RF Log 实时流）
  auto = 缺省：入口按自动识别的对讲机串口类型推断（Linux ttyACM→v3、
         ttyUSB→v1；Windows 的 COM 口区分不了 CDC 与 UART 桥，回落 v1）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .radio.protocol import FEATURE_RF_LOG

# 变体缺省表：新固件变体在此扩展一行即可（协议层一套代码通用，差异收敛为
# 波特率 + 保活扩展位）。v3 波特率仅名义值——USB CDC 不依赖波特率。
VARIANTS: dict = {
    "v1": {"baud": 38400, "features": 0},
    "v3": {"baud": 115200, "features": FEATURE_RF_LOG},
}


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    tls_cert: str = ""  # 配置后启用 HTTPS（浏览器麦克风必需）
    tls_key: str = ""
    token: str = ""  # 非空时 WebSocket 需带 ?token=...
    web_dir: str = ""  # 缺省用包内置相对路径 ../web


@dataclass
class RadioConfig:
    port: str = "auto"  # "auto" 自动识别（排除 AIOC），或显式 /dev/ttyUSB0 / COM5
    variant: str = "auto"  # "auto"|"v1"|"v3"：决定波特率与保活扩展位的缺省值
    baud: int = 0  # 0 = 按变体取缺省（v1=38400，v3=115200）
    keepalive_ms: int = 100
    features: int = -1  # -1 = 按变体取缺省（v1=0，v3=RF_LOG）
    reconnect_s: float = 2.0
    max_tx_s: float = 180.0  # 最长连续发射（看门狗强制释放）
    tx_tail_ms: int = 400  # 松开 PTT 后的尾音保护（覆盖在途语音再撤电平）

    def resolve_variant(self) -> str:
        """auto 落位后的具体变体名；未落位的 auto 与未知值都显式报错。

        auto → 具体变体的推断发生在入口（__main__，需要串口信息），
        这里只负责把关：调用方必须先落位再取缺省值。
        """
        if self.variant not in VARIANTS:
            raise ValueError(
                f"未知固件变体: {self.variant!r}（可选 {'/'.join(VARIANTS)} 或 auto）")
        return self.variant

    def resolved_baud(self) -> int:
        if self.baud:
            return self.baud
        return VARIANTS[self.resolve_variant()]["baud"]

    def resolved_features(self) -> int:
        if self.features >= 0:
            return self.features
        return VARIANTS[self.resolve_variant()]["features"]


@dataclass
class AiocConfig:
    port: str = "auto"  # AIOC 串口（PTT），M4 接入


@dataclass
class AudioConfig:
    device: str = ""  # 输入设备名称子串（如 "AIOC"）；空 = 优先匹配 AIOC，找不到用系统默认
    output_device: str = ""  # 发话输出设备（AIOC 播放）；空 = 优先匹配 AIOC
    rate: int = 48000  # AIOC 原生 48kHz 单声道
    rx_gain: float = 1.0  # 收听软件增益（1-8，过大放大底噪）
    tx_gain: float = 1.0  # 发话软件增益（影响对讲机调制深度，过大过调）
    capture_latency: float = 0.2  # 采集缓冲目标时长（秒）：深缓冲吸收调度扰动防 XRUN，
    # 不增加稳态延迟（消费仍按帧节奏跟随采集时钟）；0.05≈原 low latency 行为
    rx_fec_loss: int = 10  # 下行 Opus 带内 FEC 的预期丢包率（%）：0=关闭并回退
    # aiortc 原生编码器；系统 libopus 缺失时也自动回退


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    radio: RadioConfig = field(default_factory=RadioConfig)
    aioc: AiocConfig = field(default_factory=AiocConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)


def _apply(target: Any, section: dict) -> None:
    for key, value in section.items():
        if hasattr(target, key):
            setattr(target, key, type(getattr(target, key))(value))


def load_config(path: Optional[str]) -> AppConfig:
    cfg = AppConfig()
    if not path:
        return cfg
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    _apply(cfg.server, data.get("server", {}))
    _apply(cfg.radio, data.get("radio", {}))
    _apply(cfg.aioc, data.get("aioc", {}))
    _apply(cfg.audio, data.get("audio", {}))
    return cfg
