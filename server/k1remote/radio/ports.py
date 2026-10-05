"""串口设备识别：区分对讲机线缆与 AIOC 一体线。

对讲机侧：
  v1 硬件（uv-k5-firmware-custom-remote）经 K 型口编程线（CP2102/CH340/FTDI
  等 USB-UART 桥，38400 波特）连接，枚举为 ttyUSB*/COM*；
  v3 硬件（uv-k1-k5v3）为原生 USB CDC，枚举为 ttyACM*。
自动识别策略：排除 AIOC 后取第一个 USB 串口（vid != 0）；板载 UART
（/dev/ttyAMA0、/dev/ttyS* 等 vid == 0 的端口）不会自动选中；主机上有
多个串口设备时用 --port 显式指定。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional

# AIOC (github.com/skuep/AIOC) 的 USB VID:PID
AIOC_VID = 0x1209
AIOC_PID = 0x7388


@dataclass
class PortInfo:
    device: str
    vid: int = 0
    pid: int = 0
    description: str = ""

    @property
    def is_aioc(self) -> bool:
        return self.vid == AIOC_VID and self.pid == AIOC_PID


def classify_ports(ports: Iterable[PortInfo]) -> dict:
    """按用途分组：{"radio": [...], "aioc": [...], "other": [...]}。

    radio 仅收录 USB 串口（vid != 0）；板载 UART（/dev/ttyAMA0、/dev/ttyS*）
    vid == 0，不可能是对讲机编程线，全部归入 other。
    """
    result = {"radio": [], "aioc": [], "other": []}
    for p in ports:
        if p.is_aioc:
            result["aioc"].append(p)
        elif p.vid == 0:                 # 非 USB 端口：板载/平台串口，绝不触碰
            result["other"].append(p)
        else:
            result["radio"].append(p)
    return result


def pick_radio_port(ports: Iterable[PortInfo]) -> Optional[PortInfo]:
    """选对讲机串口：AIOC 之外的第一个 USB 串口（vid != 0）。

    板载 UART（vid == 0，如 /dev/ttyAMA0）被 classify_ports 归入 other，
    绝不自动选中——宁可让调用方提示用户用 --port 显式指定。
    """
    classified = classify_ports(ports)
    return classified["radio"][0] if classified["radio"] else None


def pick_aioc_port(ports: Iterable[PortInfo]) -> Optional[PortInfo]:
    classified = classify_ports(ports)
    return classified["aioc"][0] if classified["aioc"] else None


def list_serial_ports() -> List[PortInfo]:
    """枚举本机串口（依赖 pyserial，仅在真实环境调用）。

    pyserial 的 comports() 在部分系统/内核下不枚举板载 UART；Linux 上
    补扫 /dev/ttyAMA*、/dev/ttyS*、/dev/serial0（realpath 去重，避免
    serial0 符号链接重复），保证板载口在 --list 中可见（归入 other，
    仅展示不自动选用）。
    """
    from serial.tools import list_ports

    ports = [
        PortInfo(p.device, p.vid or 0, p.pid or 0, p.description or "")
        for p in sorted(list_ports.comports(), key=lambda x: x.device)
    ]
    if sys.platform.startswith("linux"):
        import glob

        known = {p.device for p in ports}
        known_real = {os.path.realpath(p.device) for p in ports}
        for dev in sorted(glob.glob("/dev/ttyAMA*") + glob.glob("/dev/ttyS*")
                          + glob.glob("/dev/serial0")):
            if dev in known or os.path.realpath(dev) in known_real:
                continue
            ports.append(PortInfo(dev, 0, 0, "板载 UART（vid=0，不自动选用）"))
    return ports
