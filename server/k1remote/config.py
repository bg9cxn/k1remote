"""K1Remote 服务端配置（TOML，可选；缺省值即可直接运行）。

固件变体：
  v1  = uv-k5-firmware-custom-remote（UV-K5 V1 硬件，DP32G030，UART 38400，
        保活仅支持纯 `55 AA 00 00`，无 RF Log 扩展）
  v3  = uv-k1-k5v3-firmware-custom（UV-K1/UV-K5 V3 硬件，PY32F071，USB CDC，
        支持扩展保活与 RF Log 实时流）
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .radio.protocol import FEATURE_RF_LOG


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
    variant: str = "v1"  # "v1" | "v3"：决定波特率与保活扩展位的缺省值
    baud: int = 0  # 0 = 按变体取缺省（v1=38400，v3=115200）
    keepalive_ms: int = 100
    features: int = -1  # -1 = 按变体取缺省（v1=0，v3=RF_LOG）
    reconnect_s: float = 2.0
    max_tx_s: float = 180.0  # 最长连续发射（看门狗强制释放）
    tx_tail_ms: int = 400  # 松开 PTT 后的尾音保护（覆盖在途语音再撤电平）

    def resolved_baud(self) -> int:
        return self.baud if self.baud else (38400 if self.variant == "v1" else 115200)

    def resolved_features(self) -> int:
        if self.features >= 0:
            return self.features
        return FEATURE_RF_LOG if self.variant == "v3" else 0


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
