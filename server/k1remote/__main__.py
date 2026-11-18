"""k1remote 服务端入口：`python -m k1remote`。"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import signal
import sys
from typing import Optional

from .config import load_config
from .audio import SineSource, SoundCardSink, SoundCardSource
from .radio.aioc import AiocPtt
from .radio.demo import DemoRadio
from .radio.k5viewer import K5ViewerClient
from .radio.ports import guess_variant, list_serial_ports, pick_aioc_port, pick_radio_port
from .webrtc import WebRTCService, set_fec_loss_perc
from .webapp import run


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="k1remote", description="UV-K5 远程工作站服务端")
    parser.add_argument("-c", "--config", help="TOML 配置文件路径（缺省用内置默认值）")
    parser.add_argument("-p", "--port", help="对讲机串口（覆盖配置；auto 为自动识别）")
    parser.add_argument("--variant", choices=("k5v1", "k1k5v3"),
                        help="固件变体：k5v1=uv-k5-firmware-custom-remote(UART 38400)，"
                             "k1k5v3=uv-k1-k5v3(USB CDC+RF Log，K1/K5 V3 双硬件)；覆盖配置。"
                             "缺省 auto：按串口类型推断（Linux ttyACM→k1k5v3、ttyUSB→k5v1；"
                             "Windows 区分不了 COM 口的 CDC/桥，回落 k5v1）")
    parser.add_argument("--baud", type=int, help="串口波特率（覆盖配置与变体缺省）")
    parser.add_argument("--host", help="Web 监听地址（覆盖配置）")
    parser.add_argument("--http-port", type=int, help="Web 监听端口（覆盖配置）")
    parser.add_argument("--demo", action="store_true", help="演示模式：无硬件生成假屏幕流")
    parser.add_argument("--list-ports", action="store_true", help="列出串口并按用途分类后退出")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    faulthandler.enable()
    try:
        # Windows: Ctrl+Break 转储所有线程栈（诊断"卡住不退出"类问题的现场）
        faulthandler.register(signal.SIGBREAK, file=sys.stderr)
    except (AttributeError, ValueError, OSError):
        pass

    if args.list_ports:
        from .radio.ports import classify_ports
        for group, ports in classify_ports(list_serial_ports()).items():
            for p in ports:
                hint = ""
                if group == "radio":
                    g = guess_variant(p.device)
                    hint = {"k1k5v3": "  ← CDC，疑似 K1/K5 V3",
                            "k5v1": "  ← UART 桥，疑似 UV-K5 V1"}.get(g, "")
                print(f"{group:6s} {p.device:20s} {p.description}{hint}")
        return

    cfg = load_config(args.config)
    if args.variant:
        cfg.radio.variant = args.variant
    if args.baud:
        cfg.radio.baud = args.baud
    if args.host:
        cfg.server.host = args.host
    if args.http_port:
        cfg.server.port = args.http_port

    if args.demo:
        radio = DemoRadio()
        audio_source = SineSource()  # 无硬件：正弦源验证 WebRTC 链路
        sink = None  # 演示模式不播放发话音频（避免本地回声）
    else:
        aioc_port = cfg.aioc.port
        if aioc_port == "auto":
            picked = pick_aioc_port(list_serial_ports())
            aioc_port = picked.device if picked else None

        port = args.port or cfg.radio.port
        if port == "auto":
            picked = pick_radio_port(list_serial_ports())
            if picked is None:
                # k5v1 单线方案：对讲机与 AIOC 共用串口。仅 k5v1 适用——k1k5v3 推荐
                # Type-C 传数据（K 头让给 AIOC 音频/PTT，规避 Sleeve 上数据与
                # PTT1 的仲裁）；K 头 UART（38400）虽也可推流，但 auto 无法按
                # 设备名区分，故 AIOC 同口回退仅限 k5v1。
                if aioc_port and cfg.radio.variant in ("auto", "k5v1"):
                    port = aioc_port
                    if cfg.radio.variant == "auto":
                        cfg.radio.variant = "k5v1"  # 单线即 k5v1；AIOC 自己也是 CDC，不能按设备名推断
                    print(f"未发现独立对讲机串口，使用 AIOC 同口（k5v1 单线）: {port}")
                elif cfg.radio.variant == "k1k5v3":
                    parser.error("未发现 K1/K5 V3 的 USB CDC 串口：双线部署请插机身 "
                                 "USB-C 数据线（Linux 枚举为 /dev/ttyACM*）；应急可走 "
                                 "K 头 AIOC 单线：--port <AIOC口> --variant k1k5v3 --baud 38400")
                else:
                    parser.error("未找到对讲机串口：请插入设备或用 --port 指定"
                                 "（--list-ports 查看识别结果）")
            else:
                port = picked.device
                print(f"自动识别对讲机串口: {port}")
        if cfg.radio.variant == "auto":
            guessed = guess_variant(port)
            cfg.radio.variant = guessed or "k5v1"
            if guessed:
                print(f"按串口类型推断固件变体: {cfg.radio.variant}")
            else:
                print("无法从串口类型判断固件变体（Windows 下 CDC 与 UART 桥同为 COM*），"
                      "按 k5v1 处理；K1/K5 V3 请 --variant k1k5v3")
        baud = cfg.radio.resolved_baud()
        features = cfg.radio.resolved_features()
        ptt_shared = bool(aioc_port and aioc_port == port)
        print(f"固件变体: {cfg.radio.variant} | 波特率: {baud} | 保活扩展: 0x{features:02x}"
              + (f" | AIOC PTT: {aioc_port}" + ("（共享串口）" if ptt_shared else "")
                 if aioc_port else " | AIOC 未接入，PTT 不可用"))
        aioc = AiocPtt(aioc_port) if aioc_port else None
        radio = K5ViewerClient(
            port,
            baud=baud,
            keepalive_interval=cfg.radio.keepalive_ms / 1000.0,
            features=features,
            reconnect_delay=cfg.radio.reconnect_s,
            ptt=aioc,
            ptt_shared=ptt_shared,
        )
        audio_source = SoundCardSource(device=cfg.audio.device or None, rate=cfg.audio.rate,
                                       gain=cfg.audio.rx_gain,
                                       latency=cfg.audio.capture_latency)
        sink = SoundCardSink(device=cfg.audio.output_device or None, rate=cfg.audio.rate,
                             gain=cfg.audio.tx_gain)

    webrtc = WebRTCService(audio_source, sink=sink)
    set_fec_loss_perc(cfg.audio.rx_fec_loss)
    run(cfg, radio, webrtc)

    # run() 返回时串口/声卡/PTT/任务均已显式清理。残留的 C 线程
    # （PortAudio/ffmpeg）在 Windows 上偶尔会阻塞解释器退出，这里确保
    # 进程确定性地结束。
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
