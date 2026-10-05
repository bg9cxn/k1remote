#!/usr/bin/env python3
"""AIOC 音频链路验证工具（M2）。

  python tools/audio_loopback.py --list                  # 枚举音频设备
  python tools/audio_loopback.py --meter -d 5            # 采集电平表 5 秒
  python tools/audio_loopback.py --record -d 5 -o rx.wav # 环录 5 秒存 WAV
  python tools/audio_loopback.py --sine -d 3             # 播放 440Hz 正弦 3 秒
  python tools/audio_loopback.py --meter --sine -d 5     # 边放边录（环回自测）

设备选择：--device 传名称子串（如 "AIOC"），缺省自动找名字含 AIOC 的设备，
找不到则用系统默认输入/输出。采样 48kHz 单声道 s16（AIOC 原生参数）。

典型验证流程：
  1. --meter：对讲机收信号（或按住 PTT 发载波噪声），观察电平表有读数 → 采集链路通
  2. --sine + 对讲机电平 PTT：空中应听到纯音 → 播放→发射链路通（配 serial_probe --ptt-test）
  3. --record：录下接收音频存 WAV 复查质量（失真/噪声/UART 咔啦声）
"""

import argparse
import math
import struct
import sys
import time
import wave
from pathlib import Path

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    sys.exit("缺少依赖：pip install sounddevice numpy")

RATE = 48000
BLOCK = 960  # 20ms


def resolve_device(substring: str | None, is_input: bool) -> int | None:
    devices = sd.query_devices()
    if substring:
        for idx, dev in enumerate(devices):
            if substring.lower() in dev["name"].lower():
                key = "max_input_channels" if is_input else "max_output_channels"
                if dev[key] > 0:
                    return idx
        raise SystemExit(f"未找到名称含 {substring!r} 的{'输入' if is_input else '输出'}设备")
    for idx, dev in enumerate(devices):
        key = "max_input_channels" if is_input else "max_output_channels"
        if "aioc" in dev["name"].lower() and dev[key] > 0:
            return idx
    return None  # 用系统默认


def print_devices() -> None:
    print(f"默认输入: {sd.query_devices(kind='input')}")
    print(f"默认输出: {sd.query_devices(kind='output')}")
    print()
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] or dev["max_output_channels"]:
            api = sd.query_hostapis(dev["hostapi"])["name"]
            tags = []
            if dev["max_input_channels"]:
                tags.append(f"in={dev['max_input_channels']}")
            if dev["max_output_channels"]:
                tags.append(f"out={dev['max_output_channels']}")
            print(f"[{idx:2d}] {dev['name'][:48]:48s} ({api}) {' '.join(tags)}")


def dbfs(rms: float) -> float:
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms / 32768.0)


def run_meter(seconds: float, device: str | None, record_to: Path | None) -> None:
    dev = resolve_device(device, is_input=True)
    chunks: list[bytes] = []
    last_print = 0.0

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[overflow] {status}")
        chunks.append(bytes(indata))
        nonlocal last_print
        now = time.monotonic()
        if now - last_print >= 0.2:
            last_print = now
            arr = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr * arr)))
            level = dbfs(rms)
            bar = "#" * max(0, min(50, int((level + 60) / 60 * 50)))
            print(f"\r[{level:6.1f} dBFS] |{bar:<50}|", end="", flush=True)

    print(f"采集 {seconds}s @ {RATE}Hz（设备: {dev if dev is not None else '系统默认'}）…")
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=BLOCK, device=dev, callback=callback):
        time.sleep(seconds)
    print()

    if record_to:
        with wave.open(str(record_to), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b"".join(chunks))
        peak = max(dbfs(float(np.sqrt(np.mean(np.frombuffer(c, dtype=np.int16).astype(np.float32) ** 2))))
                   for c in chunks) if chunks else -120.0
        print(f"已保存 {record_to}（{len(chunks) * BLOCK / RATE:.1f}s，峰值 {peak:.1f} dBFS）")


def run_sine(seconds: float, device: str | None, freq: float, gain: float) -> None:
    dev = resolve_device(device, is_input=False)
    print(f"播放 {freq}Hz 正弦 {seconds}s @ gain={gain}（设备: {dev if dev is not None else '系统默认'}）…")
    phase = 0.0
    step = 2 * math.pi * freq / RATE

    def callback(outdata, frames, time_info, status):
        nonlocal phase
        buf = np.empty(frames, dtype=np.int16)
        for i in range(frames):
            buf[i] = int(gain * 32767 * math.sin(phase))
            phase += step
        outdata[:] = buf.reshape(-1, 1)

    with sd.OutputStream(samplerate=RATE, channels=1, dtype="int16",
                         blocksize=BLOCK, device=dev, callback=callback):
        time.sleep(seconds)
    print("播放完毕")


def run_diag() -> None:
    """一键诊断包：设备表 + 服务端匹配结果 + 平台信息 + USB 休眠状态。"""
    import platform
    import sounddevice as sd
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    from k1remote.audio import pick_device

    print(f"平台: {platform.platform()}")
    print(f"Python: {platform.python_version()} | sounddevice {sd.__version__} "
          f"| PortAudio {sd.get_portaudio_version()[1]}")
    print()

    devices = sd.query_devices()
    print("== 设备表 ==")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] or dev["max_output_channels"]:
            api = sd.query_hostapis(dev["hostapi"])["name"]
            print(f"[{idx:2d}] {dev['name'][:52]:52s} ({api}) "
                  f"in={dev['max_input_channels']} out={dev['max_output_channels']} "
                  f"lat_in={dev['default_low_input_latency']*1000:.0f}ms "
                  f"lat_out={dev['default_low_output_latency']*1000:.0f}ms")

    print("\n== 服务端设备选择（模拟匹配逻辑）==")
    for role, is_in in (("收音输入", True), ("发话输出", False)):
        idx, note = pick_device(devices, None, is_input=is_in)
        if idx is None:
            print(f"{role}: 系统默认（{note}）")
        else:
            print(f"{role}: [{idx}] {devices[idx]['name']} ({note})")

    print("\n== USB 自动休眠 ==")
    autosuspend = Path("/sys/module/usbcore/parameters/autosuspend")
    if autosuspend.exists():
        val = autosuspend.read_text().strip()
        print(f"usbcore.autosuspend = {val}" + ("  ← 建议设为 -1" if val != "-1" else "  (已禁用)"))
        for p in Path("/sys/bus/usb/devices").glob("*/power/control"):
            try:
                product = (p.parent / "product")
                pname = product.read_text().strip() if product.exists() else "?"
                if "cable" in pname.lower() or "audio" in pname.lower():
                    print(f"  {p.parent.name}: {pname} → power/control={p.read_text().strip()}")
            except OSError:
                pass
    else:
        print("(非 Linux，跳过)")
    print("\n把以上全部输出 + journalctl -u k1remote -n 200 一起反馈用于定位。")


def main() -> None:
    ap = argparse.ArgumentParser(description="AIOC 音频链路验证")
    ap.add_argument("--list", action="store_true", help="枚举音频设备后退出")
    ap.add_argument("--diag", action="store_true", help="输出一键诊断包")
    ap.add_argument("--device", help="设备名称子串（缺省自动找 AIOC，否则系统默认）")
    ap.add_argument("-d", "--duration", type=float, default=5.0)
    ap.add_argument("--meter", action="store_true", help="采集电平表")
    ap.add_argument("--record", metavar="WAV", help="采集同时保存 WAV")
    ap.add_argument("--sine", action="store_true", help="播放正弦音")
    ap.add_argument("--freq", type=float, default=440.0)
    ap.add_argument("--gain", type=float, default=0.3)
    args = ap.parse_args()

    if args.list:
        print_devices()
        return
    if args.diag:
        run_diag()
        return
    if not (args.meter or args.record or args.sine):
        ap.print_help()
        return
    if args.meter or args.record:
        run_meter(args.duration, args.device, Path(args.record) if args.record else None)
    if args.sine:
        run_sine(args.duration, args.device, args.freq, args.gain)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
