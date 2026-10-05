# K5Viewer 串口协议参考

## 适用固件

| 变体 | 固件仓库 | 硬件 | 物理链路 | 波特率 | RF Log 扩展 |
|---|---|---|---|---|---|
| **v1（当前目标）** | `uv-k5-firmware-custom-remote` | UV-K5 V1（DP32G030） | K 型口编程线（CP2102/CH340 等 USB-UART 桥） | **38400** | 无 |
| v3（保留兼容） | `uv-k1-k5v3-firmware-custom` | UV-K1 / UV-K5 V3（PY32F071） | USB-C CDC-ACM | 任意（CDC 不依赖） | 有 |

v1 固件为用户在 Egzumer/F4HWN V1 固件基础上增加串口控制的版本（`k5viewer.c` 自 V3 移植）。
屏幕流与按键注入协议两者一致，差异仅在物理层、保活扩展位与 RF Log 帧（见 §5）。

## 1. 物理连接（v1）

- 对讲机 K 型两芯口 ← Baofeng/Kenwood 式编程线（USB-UART 桥）→ 主机，Linux 枚举为 `/dev/ttyUSB0`，Windows 为 `COM*`，**波特率 38400**（固件 `UART1->BAUD = 48M/39053`）。
- 触点-方向表（K1 双 TRS 插头，对讲机视角；依据 AIOC 原理图 `kicad/k1-aioc`）：

  | 触点 | 信号 | 方向 |
  |---|---|---|
  | 2.5mm Tip | SPK+ 喇叭音频 | 对讲机 → 主机 |
  | 2.5mm Ring | 对讲机 TXD = PA7（屏幕流数据） | 对讲机 → 主机 |
  | 2.5mm Sleeve | GND | — |
  | 3.5mm Tip | +V / PTT2 | 主机 → 对讲机 |
  | 3.5mm Ring | MIC+ 麦克风音频 | 主机 → 对讲机 |
  | 3.5mm Sleeve | **对讲机 RXD = PA8（保活/注入）+ PTT1**（split-pad 默认连通） | 主机 → 对讲机 |

  音频与数据/PTT 分处不同触点，互不混线；唯一共享是 Sleeve 上的 RXD 与 PTT1（见 §2 PTT 说明）。
- 线缆检测：固件靠"收到合法字节"判定连接（`UART_IsCableConnected` 即 DMA 环形缓冲解析），保活停止 ~15 个主循环后停止推流，重新收到保活即恢复并强制推整帧。
- CHIRP 占用串口时 `gUART_LockK5Viewer` 锁定推流，结束后自动恢复。

## 2. 主机 → 对讲机

| 帧 | 字节序列 | 说明 |
|---|---|---|
| 保活 | `55 AA 00 00` | 必须**周期发送**（建议 100ms），否则固件停止推流 |
| 注入短按键 | `AA 55 03 <keycode>` | 模拟一次短按 |
| 注入长按键 | `AA 55 04 <keycode>` | 模拟一次长按 |
| 串口 PTT（自研扩展） | `AA 55 06 <0\|1>` | **发射保持/释放**。仅 `uv-k5-firmware-custom-remote`（补丁后）支持；0x06 在两固件解析器中都空闲，发给 v3 无害。发射保持期间主机应静默保活，释放后再恢复 |

> ⚠️ **v3 专属**：`55 AA 05 <flags>` 为扩展保活（bit0=RF Log 实时流，bit1=历史分页，bit7=重启同步）。
> **v1 固件的解析器不认识此帧——它不算保活，发送它会导致推流停止。** v1 必须发纯 `55 AA 00 00`。

### keycode（`KEY_Code_e`，两固件一致）

| 值 | 键 | 值 | 键 |
|---|---|---|---|
| 0–9 | 数字键 `0`–`9` | 14 | `STAR`（*） |
| 10 | `MENU` | 15 | `F`（#） |
| 11 | `UP` | 16 | `PTT`（**固件屏蔽注入**） |
| 12 | `DOWN` | 17 | `SIDE2` |
| 13 | `EXIT` | 18 | `SIDE1` |

> `KEY_PTT` 在两个固件中都被显式丢弃（`KEYBOARD_InjectKey`，理由：串口无法保证松键时机）。
> 远程发射**不需要固件支持**：UV-K1 的 K 型口有外部 PTT 感测（3.5mm Sleeve 上的 PTT1 电平键控，已实测）。
> **定稿路径 = AIOC 电平 PTT（DTR=1 & RTS=0）+ 发射期暂停保活**：
> PTT1 与对讲机 RXD 共用 Sleeve 触点，AIOC 固件默认 `TXFRCPTT=PTT1`——每次串口数据发送前
> 强制释放 PTT1、UART 空闲后才重新吸合（`usb_serial.c`），所以 PTT 吸合期间主机必须静默
> （静默只停 bulk 数据，DTR/RTS 控制传输不受影响，看门狗随时可撤）。
> 串口 PTT 帧（上表第 4 行，自研固件补丁，已实现并存档）仅作"纯编程线"部署的备份。

解析器（`KEYBOARD_ProcessProtocolByte`）是不带长度字段的字节流状态机：不匹配即回 IDLE，乱码无害。保活帧与按键帧可任意交错。

## 3. 对讲机 → 主机（v1 只有屏幕差分帧）

固件推流总是以 **版本标记字节** 开头：`0xF0 | stateFlags`，随后每帧：

```
[AA 55] [type(1)] [lenHi lenLo] [payload(len)] [0x0A]
```

### stateFlags（`0xF0|flags` 低 3 位）

| bit | 含义 |
|---|---|
| 0 | 深睡眠（Deep sleep） |
| 1 | **红灯亮 = 正在发射 (TX)** |
| 2 | **绿灯亮 = 正在接收 (RX)** |

### type = 0x02：屏幕差分帧

payload 由 N 个 9 字节块组成（`len = 9×N`，可为 0）：

```
[chunkIdx(1)] [payload(8)] × N
```

- 全屏共 128 块：8 行（line 0 = 状态行，1..7 = 主屏行）× 每行 16 块。
- 块索引展开：`line = idx >> 4`，`bitPlane = (idx & 0xF) >> 1`，`colBase = (idx & 1) ? 64 : 0`。
- payload 第 `j` 字节（0..7）的第 `k` 位（0..7）= 像素 **`(x = colBase + 8j + k, y = line*8 + bitPlane)`**。
- 屏幕为 128×64 单色（ST7565，垂直字节布局）。显示反色由固件完成（`gSetting_set_inv` 取反），客户端无需处理。
- 固件在物理按键按住期间跳过推流（防阻塞丢键），画面瞬时停顿属正常。
- 仅变化块被发送；重连/重开保活后强制推整帧（~1.3KB）。

参考解码（与固件 `K5VIEWER_Chunk` 逐位对应）：

```python
def decode_chunk(fb, idx, payload):
    # fb: bytearray(1024)，fb[line*128+col] = 垂直字节（bit y&7）
    line, rest = divmod(idx, 16)
    bit, half = divmod(rest, 2)
    col_base = half * 64
    for j in range(8):
        b = payload[j]
        for k in range(8):
            if b & (1 << k):
                fb[line * 128 + col_base + j * 8 + k] |= 1 << bit
```

### type = 0x01（旧协议截图帧）与本固件的 screenshot.c

本固件保留了旧的整帧截图功能（`screenshot.c`，F4HWN 原版截图协议，锁定期间不发）。
我们的客户端只消费 `0xF0` 标记的新协议差分帧；若混入旧协议帧（无 0xF0 标记，帧头 `AA 55 01`），
解析器会因找不到标记字节而自动丢弃——无需处理，但抓包分析时注意区分。

## 4. 时序与实现要点

| 项 | 建议值 | 依据 |
|---|---|---|
| 波特率（v1） | **38400** | 固件 `UART1->BAUD = 48M/39053` ≈ 38400；配套 k5viewer.py 同值 |
| 保活周期 | 100ms | keepAlive=15 个主循环周期，需留裕量 |
| PTT 期间静默 UART | 发射时暂停保活与注入 | Sleeve 上数据与 PTT1 共触点，AIOC 固件 TXFRCPTT 数据优先会强制释放 PTT1；释放后恢复（对讲机自动补整帧） |
| 屏幕转发 | 变化即推，上限 ~20fps | 固件仅推差分，量极小 |
| 整帧大小 | 1 + 5 + 128×9 + 1 = 1155B | 重连后首帧；38400 波特下约 240ms（推流期间 UI 保持响应） |
| 重同步 | 扫描 `0xF0..0xF7` 标记字节 | 标记高 4 位固定，误码可自恢复 |
| 单连接独占 | 是 | CHIRP 等占用时 `gUART_LockK5Viewer` 锁定推流 |

## 5. v3 附录：RF Log 扩展（保留兼容）

v3 固件（USB CDC）在扩展保活 `55 AA 05 <flags>` 打开对应位后会追加两种帧
（v1 固件无此功能，解析库保留支持以便双固件兼容）：

### type = 0x05：RF Log 实时包

- 启用时 `len = 4 + 65×25 = 1629`；未启用时仅 4 字节头。
- 头（4 字节）：`[version=2] [statusFlags] [rowCount=64 或 0] [0]`
  - statusFlags：bit0=ACTIVE（会话进行中），bit1=HAS_TRAFFIC，bit2=CLEARING，bit3=DISABLED
- 行结构（packed，25 字节，小端）：

```c
struct {            // <I I H H B B B 10s>
  uint32 frequency;       // Hz
  uint32 trafficSeq;      // 单调递增序号
  uint16 durationSeconds;
  uint16 channel;         // 信道号
  uint8  flags;           // bit0=TX(否则RX), bit2=MONITOR, bit3=SESSION
  uint8  meter;           // S 表
  uint8  battVolt;        // 电池电压(编码值)
  char   channelName[10]; // NUL 填充
};
```

- 布局：头 + 1 条当前活动行 + 64 条历史行槽位（无效槽位全 0，`frequency==0` 跳过）。

### type = 0x06：RF Log 历史分页

`len = 64×25 = 1600`；`trafficSeq` 单调递增，客户端按序号分页拉取。
