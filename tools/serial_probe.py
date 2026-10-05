#!/usr/bin/env python3
"""K5Viewer 串口探测工具（M0/M2 验收工具，跨平台）。

在有对讲机（或 AIOC）的任何机器上直接运行，无需安装 k1remote 包：

  python tools/serial_probe.py --list                 # 列出并分类串口
  python tools/serial_probe.py                        # 自动识别，实时渲染屏幕流
  python tools/serial_probe.py -p COM7 --stats        # 指定端口 + 统计
  python tools/serial_probe.py --inject up            # 1s 后注入一次 ↑ 键
  python tools/serial_probe.py --inject menu:long     # 长按注入
  python tools/serial_probe.py --aioc COM8 --ptt-test 1.5   # AIOC 硬件 PTT 测试
  python tools/serial_probe.py --save capture         # 退出时保存原始流 + PBM 截图

PTT 测试可与屏幕流同时运行：注入后观察渲染画面里的 TX 指示与对讲机红灯。
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import serial  # noqa: E402

from k1remote.radio.aioc import AiocPtt  # noqa: E402
from k1remote.radio.protocol import (  # noqa: E402
    FLAG_LED_GREEN,
    FLAG_LED_RED,
    RfLogFrame,
    ScreenFrame,
    ScreenState,
    StreamParser,
    keepalive_frame,
    key_frame,
    keycode_from_name,
    ptt_frame,
)
from k1remote.radio.ports import (  # noqa: E402
    classify_ports,
    list_serial_ports,
    pick_aioc_port,
    pick_radio_port,
)

CLEAR = "\x1b[2J\x1b[H"


def render_screen(state: ScreenState, stats_line: str) -> str:
    fb = state.framebuffer
    lines = [CLEAR + stats_line]
    if state.led_tx:
        lines.append("** TX (红灯) **")
    if state.led_rx:
        lines.append("== RX (绿灯) ==")
    for y in range(64):
        row_bits = (y >> 3) * 128
        bit = 1 << (y & 7)
        line = "".join("#" if fb[row_bits + x] & bit else "." for x in range(128))
        lines.append(line)
        if y == 7:
            lines.append("-" * 128)  # 状态行与主屏分隔
    return "\n".join(lines)


def write_pbm(path: Path, fb: bytearray) -> None:
    """保存为 PBM (P4) 单色位图，看图软件可直接打开。"""
    data = bytearray(16 * 64)
    for y in range(64):
        row_bits = (y >> 3) * 128
        bit = 1 << (y & 7)
        for x in range(128):
            if fb[row_bits + x] & bit:
                data[y * 16 + x // 8] |= 0x80 >> (x % 8)
    path.write_bytes(b"P4\n128 64\n" + bytes(data))


def parse_inject(spec: str):
    name, _, longpart = spec.partition(":")
    key = keycode_from_name(name)
    if key is None:
        raise SystemExit(f"未知按键名: {name}（可用 0-9/menu/up/down/exit/star/f/side1/side2）")
    return key, longpart == "long"


def main() -> None:
    ap = argparse.ArgumentParser(description="K5Viewer 串口探测")
    ap.add_argument("-p", "--port", help="对讲机串口（默认自动识别）")
    ap.add_argument("-b", "--baud", type=int, default=38400,
                    help="波特率（v1 UART 固件=38400 缺省；v3 USB CDC 任意）")
    ap.add_argument("--list", action="store_true", help="列出串口分类后退出")
    ap.add_argument("-d", "--duration", type=float, default=0, help="运行秒数（0 = Ctrl-C 退出）")
    ap.add_argument("--keepalive-ms", type=int, default=100)
    ap.add_argument("--features", type=lambda x: int(x, 0), default=0,
                    help="保活扩展位（v3 固件 RF Log=0x1；v1 固件必须为 0）")
    ap.add_argument("--inject", help="注入按键，如 up 或 menu:long")
    ap.add_argument("--inject-delay", type=float, default=1.0)
    ap.add_argument("--aioc", help="AIOC 串口（PTT 测试用，默认自动识别）")
    ap.add_argument("--ptt-test", type=float, metavar="SEC",
                    help="电平 PTT 测试（DTR=1 & RTS=0，期间静默保活）")
    ap.add_argument("--no-mute", action="store_true",
                    help="电平 PTT 测试时不静默保活（对照实验：预期因 AIOC 固件"
                         "TXFRCPTT 数据优先仲裁而键不上发射）")
    ap.add_argument("--ptt-dry", action="store_true",
                    help="PTT 状态机演练：只打印动作不改电平（无射频，安全自测）")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="输出 DTR/RTS 控制线动作的带时间戳日志")
    ap.add_argument("--ptt-serial", type=float, metavar="SEC",
                    help="串口 PTT 测试（需刷入 AA 55 06 串口 PTT 补丁的固件）；"
                         "保持期间静默保活，释放后自动恢复屏幕流")
    ap.add_argument("--save", metavar="DIR", help="退出时保存 capture.bin 与 screen.pbm")
    ap.add_argument("--no-render", action="store_true", help="不渲染屏幕（配合 --save 抓原始流）")
    args = ap.parse_args()

    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list:
        for group, ports in classify_ports(list_serial_ports()).items():
            for p in ports:
                print(f"{group:6s} {p.device:20s} {p.description}")
        return

    port = args.port
    if not port:
        ports = list_serial_ports()
        picked = pick_radio_port(ports)
        if not picked:
            if not ports:
                raise SystemExit("未发现任何串口设备：请检查 USB 线与驱动")
            aioc = pick_aioc_port(ports)
            if aioc:
                raise SystemExit(
                    f"未自动识别到对讲机串口，只发现 AIOC({aioc.device})。"
                    f"若对讲机是经 AIOC 连接的（V1 单线方案），用 -p {aioc.device} 显式指定")
            raise SystemExit(
                "未自动识别到对讲机串口，发现: "
                + ", ".join(f"{p.device}({p.description})" for p in ports)
                + "；请用 -p 显式指定")
        port = picked.device
        print(f"自动识别对讲机串口: {port}")

    ptt: AiocPtt = None
    share_port = False
    if args.ptt_test:
        aioc_port = args.aioc
        if not aioc_port:
            picked = pick_aioc_port(list_serial_ports())
            if not picked:
                raise SystemExit("未找到 AIOC 串口，请用 --aioc 指定")
            aioc_port = picked.device
        share_port = (aioc_port == port)
        print(f"AIOC PTT: {aioc_port}"
              + ("（与对讲机串口共享句柄，V1 单线方案）" if share_port else ""))
        ptt = AiocPtt(aioc_port)

    inject_key = inject_long = None
    inject_at = None
    if args.inject:
        inject_key, inject_long = parse_inject(args.inject)
        inject_at = time.monotonic() + args.inject_delay

    serial_ptt_release_at = None
    if args.ptt_serial:
        serial_ptt_release_at = time.monotonic() + args.inject_delay + args.ptt_serial
        serial_ptt_at = time.monotonic() + args.inject_delay
    else:
        serial_ptt_at = None
    serial_ptt_holding = False
    # 电平 PTT 状态机：静默(0.25s) → 键上 → 盲重试一次 → 保持 → 释放
    ptt_mute = not args.no_mute
    ptt_holding = False       # 数据静默有效
    ptt_assert_at = None      # 计划键上时刻（静默缓冲后）
    ptt_reassert_at = None    # 盲重试的再键上时刻
    ptt_retry_done = False
    ptt_test_done = False
    ptt_off_at = None

    raw = bytearray()
    stats = {"bytes": 0, "frames": 0, "screen": 0, "rflog": 0, "t0": time.monotonic()}
    state = ScreenState()
    last_render = 0.0
    ptt_off_at = None

    deadline = time.monotonic() + args.duration if args.duration else None
    keepalive_s = args.keepalive_ms / 1000.0

    try:
        ser = serial.Serial(port, args.baud, timeout=0.05)
    except serial.SerialException as exc:
        if "拒绝访问" in str(exc) or "PermissionError" in str(exc):
            raise SystemExit(
                f"无法打开 {port}：端口被其他程序占用（如 CHIRP、K5Viewer、串口监视器、"
                "另一个 k1remote 实例）。关闭占用它的程序后重试。")
        raise SystemExit(f"无法打开 {port}: {exc}")
    with ser:
        # pyserial 打开串口时默认拉起 DTR/RTS（Windows），必须立即清除——
        # DTR=1 & RTS=0 即 PTT 键控，默认状态是潜在误发射源
        ser.setDTR(False)
        ser.setRTS(True)
        if ptt is not None and share_port:
            ptt.attach(ser)  # 同一 AIOC 串口：协议与 PTT 电平共用句柄
        try:
            print(f"已打开 {port} @ {args.baud}，开始收流（Ctrl-C 退出）…")
            parser = StreamParser()
            next_keepalive = 0.0
            while True:
                now = time.monotonic()
                if deadline and now >= deadline:
                    break
                # PTT 吸合期间静默串口数据：AIOC 固件 TXFRCPTT 默认会把与
                # UART 共触点的 PTT1 在每次数据发送前强制释放（数据优先），
                # 导致 PTT 反复通断、对讲机 30ms 防抖永远不满足。
                if now >= next_keepalive and not serial_ptt_holding and not ptt_holding:
                    ser.write(keepalive_frame(args.features))
                    next_keepalive = now + keepalive_s

                if inject_at and now >= inject_at:
                    ser.write(key_frame(inject_key, inject_long))
                    print(f">>> 注入按键 {inject_key}{' 长按' if inject_long else ''}")
                    inject_at = None
                if serial_ptt_at and now >= serial_ptt_at:
                    ser.write(ptt_frame(True))
                    serial_ptt_holding = True
                    print(f">>> 串口 PTT ON（{args.ptt_serial}s 后释放，期间保活静默）")
                    serial_ptt_at = None
                if serial_ptt_holding and now >= serial_ptt_release_at:
                    ser.write(ptt_frame(False))
                    serial_ptt_holding = False
                    next_keepalive = now  # 立即恢复保活，屏幕流会补整帧
                    print(">>> 串口 PTT OFF")
                if ptt and args.ptt_test and not ptt_test_done and ptt_off_at is None \
                        and ptt_assert_at is None \
                        and ptt_reassert_at is None and now >= args.inject_delay:
                    # 先静默再键上：AIOC 行状态回调在 UART 发送器忙时会丢弃 PTT
                    # 断言（usb_serial.c，TE 守卫），且数据发送会强制释放 PTT1
                    # （TXFRCPTT）。静默 0.25s 保证键上瞬间链路无数据。
                    ptt_holding = ptt_mute
                    ptt_assert_at = now if not ptt_mute else now + 0.25
                if ptt and ptt_assert_at is not None and now >= ptt_assert_at:
                    if not args.ptt_dry:
                        ptt.set(True)
                    print(f">>> 电平 PTT ON（{args.ptt_test}s 后释放"
                          + ("，静默保活" if ptt_mute else "，未静默（对照实验）")
                          + ("，DRY 不动电平）" if args.ptt_dry else "）"))
                    ptt_off_at = now + args.ptt_test
                    ptt_assert_at = None
                if ptt and ptt_reassert_at is not None and now >= ptt_reassert_at:
                    if not args.ptt_dry:
                        ptt.set(True)
                    print(">>> 电平 PTT 重试键上（盲重试）")
                    ptt_reassert_at = None
                if ptt and ptt_off_at is not None and not ptt_retry_done \
                        and ptt_reassert_at is None and now >= ptt_off_at - args.ptt_test / 2:
                    # 盲重试：若首次断言被 TE 忙丢弃，翻转线状态可触发新回调补上；
                    # 若已生效则表现为一次 0.15s 的发射间隙，无碍。
                    if not args.ptt_dry:
                        ptt.set(False)
                    ptt_reassert_at = now + 0.15
                    ptt_retry_done = True
                    print(">>> 电平 PTT 间隙（盲重试：释放 0.15s 后重新键上）")
                if ptt and ptt_off_at is not None and now >= ptt_off_at:
                    if not args.ptt_dry:
                        ptt.set(False)
                    ptt_holding = False
                    ptt_off_at = None
                    ptt_retry_done = False
                    ptt_test_done = True
                    next_keepalive = now
                    print(">>> 电平 PTT OFF")

                data = ser.read(512)
                if data:
                    raw.extend(data)
                    stats["bytes"] += len(data)
                    for frame in parser.feed(data):
                        stats["frames"] += 1
                        if isinstance(frame, ScreenFrame):
                            stats["screen"] += 1
                            state.apply(frame)
                        elif isinstance(frame, RfLogFrame):
                            stats["rflog"] += 1
                            for row in frame.rows:
                                print(f"[RFLOG] {'TX' if row.is_tx else 'RX'} "
                                      f"{row.frequency / 1e6:.4f}MHz ch={row.channel} "
                                      f"{row.duration_seconds}s S{row.meter} {row.channel_name}")

                if not args.no_render and state.received_any and now - last_render >= 0.1:
                    elapsed = now - stats["t0"]
                    stats_line = (f"{port} | {elapsed:5.1f}s | 收 {stats['bytes']}B | "
                                  f"帧 {stats['frames']} (屏 {stats['screen']}, RFLog {stats['rflog']}) | "
                                  f"~{stats['screen'] / max(elapsed, 0.1):.1f} fps")
                    print(render_screen(state, stats_line))
                    last_render = now
        finally:
            # 任何退出路径（含 Ctrl-C/异常）先撤 PTT 再关端口
            if ptt is not None:
                try:
                    ptt.close()
                except serial.SerialException:
                    pass
                print(">>> 已兜底释放电平 PTT")

    if args.save:
        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        (out / "capture.bin").write_bytes(raw)
        write_pbm(out / "screen.pbm", state.framebuffer)
        print(f"已保存: {out/'capture.bin'} ({len(raw)}B), {out/'screen.pbm'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
