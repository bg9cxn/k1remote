<div align="center">

# 📻 K1Remote

**把对讲机放上信号好的高处 —— 在浏览器里远程收听、发射与控制**

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](server/pyproject.toml)
[![Hardware](https://img.shields.io/badge/hardware-UV--K1%20%2F%20K5%20%2F%20K6-C51A4A)](#-固件变体)
[![Audio](https://img.shields.io/badge/audio-WebRTC%20%2B%20Opus-00A0B0)](#-系统架构)
[![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%204B%20%7C%20Orange%20Pi%20Zero%202W%20%28%E5%AE%9E%E6%B5%8B%29-C51A4A)](#-硬件要求)
[![CI](https://github.com/bg9cxn/k1remote/actions/workflows/ci.yml/badge.svg)](https://github.com/bg9cxn/k1remote/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

*UV-K1 / K5 / K6 对讲机远程工作站：屏幕镜像 · 远程键盘 · WebRTC 双向语音 · PTT 闭环*

</div>

---

<!-- 📷 截图位：截一张浏览器操作界面（屏幕镜像 + PTT 按钮 + 状态面板）存为 docs/images/web-ui.png 后取消注释
![K1Remote Web 操作界面](docs/images/web-ui.png)
-->

把对讲机部署到信号好的高处，经 Linux 开发板（树莓派 / Orange Pi）+ 网络，在浏览器里完成 **远程收听、远程发射（PTT）、远程控制（看屏幕 / 按键 / 换信道）**。适配两组对讲机机型：**① UV-K1 与 UV-K5/K6（V3 硬件）；② UV-K5/K6（V1&V2 硬件）**（见[固件变体](#-固件变体)）。

## ⚠️ 安全与业余无线电合规

> **发射功能仅供持照业余无线电操作员使用**。请在合法业余频段内、遵守当地法规与操作规范进行通联。

- **防误发射**：UI 需勾选"发射使能"后按住 PTT；服务端最长发射时限（默认 180 s，可配）；WebSocket 断连立即撤 PTT；电台 TxTOut 超时兜底。
- **PTT 互斥**：同一时刻仅一个会话可持有 PTT，第二个会话键上会被拒绝（"其他操作员正在发射"）；虚拟键盘指令不互斥，多端接入时请自行协调。
- **鉴权**：HTTPS + 操作口令（token），数据不出局域网 / WireGuard 隧道，无第三方云依赖。
- 界面提供发射计时显示；建议预置信道时限制合法频段与功率档位。

## 🏠 使用场景

- **场景一 · 家中补盲（窗台 / 阳台台站）**：城市里钢筋水泥遮挡严重，手台往往要举到窗边、阳台才有信号。把对讲机 + AIOC + Linux 开发板固定在窗边或阳台，手机 / 电脑接入家中局域网，即可在家里**任何位置**保持良好的收发信号——**电台待在信号好的地方，人在家里任何地方**。
- **场景二 · 高点部署（跨局域网远程台）**：把设备架上楼顶、山顶等真正的高点，通联覆盖大幅提升。家中操作端与高点电台端通常不在同一局域网，用 **WireGuard 等隧道**穿透打通两端网络即可访问（部署与证书配置见[部署手册](deploy/README.md)），获得远超室内天线的通联效果。

## ✨ 功能特性

| | 功能 | 说明 |
|---|---|---|
| 🖥️ | **屏幕镜像** | 128×64 屏幕差分流实时推送，浏览器 Canvas 渲染（琥珀色磷光风格） |
| ⌨️ | **远程键盘** | 虚拟键盘注入短按 / 长按，远程换信道、进菜单、改设置 |
| 🎧 | **远程收听** | 对讲机收音 → AIOC 声卡 → WebRTC / Opus 推流，带内 FEC 抗网络丢包 |
| 🎙️ | **远程发射** | 浏览器麦克风 → WebRTC → 声卡 → 对讲机；PTT 大按钮 / 空格键，含预卷缓冲防吞字 |
| 🛡️ | **PTT 安全闭环** | 目标态 / 回读仲裁、WS 断连即撤发、最长发射时限看门狗、尾音保护、电台 TxTOut 兜底 |
| 🔐 | **HTTPS + Token** | 自签证书一键生成，操作口令认证，满足浏览器麦克风安全上下文要求 |
| 📊 | **状态面板** | k1k5v3 变体 RF Log 实时上报电池电压 / 频率 / 信道名 |
| 🍓 | **Linux 开发板部署包** | `install.sh` 一键安装 + systemd 托管 + 配置模板 + 部署手册（树莓派 / Orange Pi） |
| 🧪 | **演示模式** | 无硬件即可跑通全部 UI 与 WebRTC 链路（假屏幕流 + 正弦音源） |

## 🔌 固件变体

| 变体 | 适配机型 | 固件 | 数据链路 | RF Log |
|---|---|---|---|---|
| `k5v1`（已真机验证） | **UV-K5 / UV-K6（V1、V2 硬件）** | `uv-k5-firmware-custom-remote`（基于F4HWN固件移植串口控制功能） | K 型口 / AIOC（38400 UART），**一线全包**（数据 + 音频 + PTT） | — |
| `k1k5v3`（已真机验证） | **UV-K1 与 UV-K5 / UV-K6（V3 硬件）** | `uv-k1-k5v3-firmware-custom`（F4HWN Fusion，单镜像双硬件） | **Type-C CDC（推荐）** 或 K 头 UART；部署双线 = Type-C 数据 + K 头 AIOC 音频/PTT | ✅ 电池 / 频率 / 信道 |

- 配置缺省 `variant = "auto"`：按串口类型自动推断（Linux `ttyACM*`→k1k5v3、`ttyUSB*`→k5v1；Windows 的 CDC 与 UART 桥同为 `COM*`，回落 k5v1，请显式 `--variant k1k5v3`）。
- 变体 ID 沿用固件仓命名（`k5v1` / `k1k5v3`），实际覆盖机型以上表为准，不限于 ID 字面型号。
- 两种变体的屏幕流与按键注入协议完全一致，服务端一套代码通用；差异仅波特率缺省与保活扩展位。
- 2026-11 起变体 ID 由裸版本号 `v1` / `v3` 改名为 `k5v1` / `k1k5v3`（避免与项目名混淆），旧值不再接受。

## 🧰 硬件要求

> **这是一个真实的射频硬件项目：以下硬件为必需，不是纯软件方案。** 无硬件时可先用[演示模式](#-快速开始)预览界面与 WebRTC 链路，但屏幕镜像、收听、发射（PTT）都必须接真机才能工作。

| 硬件 | 数量 | 说明 |
|---|---|---|
| 对讲机 | 1 | 两组适配（见[固件变体](#-固件变体)）：**① UV-K1 与 UV-K5/K6（V3 硬件）；② UV-K5/K6（V1&V2 硬件）** |
| [AIOC](https://github.com/skuep/AIOC)（All-in-One-Cable） | 1 | K 型口一线三用：串口数据 + 音频 + 电平 PTT；开源硬件，可购成品或在嘉立创（JLCPCB）打板自制 |
| 树莓派 4B / Orange Pi Zero 2W | 1 | **实测平台**；其他型号或其他 Linux 开发板理论上同样可跑（需 Python 3.11+） |
| 供电（UPS / 大容量充电宝）、防雨外壳、天线地网（tiger tail）/ 磁环 | 若干 | 高点部署：供电保障与 EMI 防护 |

> 电台菜单必配：**SetOff（自动深睡）= OFF**（深睡后串口推流静默）、TxTOut 与服务端发射时限一致（卡发射最终兜底）。完整清单见 [deploy/README.md §0](deploy/README.md)。

## 🏗️ 系统架构

```text
┌────────────────── 浏览器（操作端）──────────────────┐
│  Canvas 屏幕镜像    虚拟键盘/信道控制    PTT 大按钮   │
│  听：WebRTC 接收     说：getUserMedia → WebRTC 发送  │
└────────┬─────────────────────────┬─────────────────┘
         │ WebSocket（控制/信令/屏幕帧，TLS）      │ WebRTC SRTP（双向语音）
         ▼                                       ▼
┌────────────── Linux 开发板（k1remote 服务端）───────┐
│  webapp  : HTTPS 静态页 + WebSocket（鉴权、屏幕帧、指令）      │
│  webrtc  : aiortc Peer —— 发"对讲机收音"轨，收"麦克风"轨       │
│  ptt     : PTT 状态机（目标态/回读/看门狗/发射期串口静默）      │
│  radio   : K5Viewer 串口协议（保活、差分屏解码、按键注入）      │
│  audio   : ALSA 采集/播放（声卡 ↔ 对讲机音频通路）             │
└────────┬────────────────────────────────────┬──────────────┘
         │ USB（AIOC：数据 + 音频 + PTT）       │ USB 声卡
         ▼                                    ▼
┌────────────── 对讲机（UV-K1 / K5 / K6，K5Viewer 固件）──────────┐
│   屏幕推流 / 按键注入 / RF Log          喇叭 ←── 音频通路 ──→ 麦克 │
└─────────────────────────────────────────────────────────────────┘
```

> **PTT 原理一句话**：DTR=1 & RTS=0 经 AIOC 拉低 K 型口 Sleeve 触点键控对讲机。AIOC 固件"数据优先"会在串口数据发送前强制释放 PTT，因此**发射期间服务端自动静默串口保活**（已内置联动，松键恢复）。

## 🚀 快速开始

### 环境要求

- Python **3.11+**
- 部署平台：**树莓派 4B / Orange Pi Zero 2W（实测）**，其他型号或其他 Linux 开发板理论上同样可跑；开发调试也可用任意 Linux / Windows 主机
- 服务端依赖：`pyserial` `aiohttp` `aiortc` `sounddevice` `numpy`（`pip install -e server` 一步到位）
- 可选：系统 `libopus`（Debian 自带 `libopus0`）启用下行带内 FEC，缺失时自动回退 aiortc 原生编码

### ① 演示模式（无需硬件，仅预览界面与链路）

```bash
git clone https://github.com/bg9cxn/k1remote.git
cd k1remote
pip install -e server
python -m k1remote --demo
```

浏览器打开 <http://127.0.0.1:8080>：**▶ 开始收听**（WebRTC 正弦音）→ 勾选**发射使能** → **按住 PTT**（空格键）→ 观察计时与状态。

> 演示模式只跑通服务端与前端链路（假屏幕流 + 正弦音源）；真实的屏幕镜像与射频收发需要完整硬件，见[硬件要求](#-硬件要求)。

### ② 手动启动（连接真机）

对讲机经 AIOC / 编程线接入后，先用自检工具验证硬件链路（可选但推荐）：

| 工具 | 用途 |
|---|---|
| `tools/serial_probe.py` | 串口分类（radio/aioc）、屏幕流实时渲染、远程按键注入、电平 PTT 测试 |
| `tools/audio_loopback.py` | 音频设备枚举、采集电平表、环录 WAV、正弦回放（配合 PTT 空中验证） |
| `tools/make_cert.py` | 生成自签证书（自动写入本机 IP / 主机名 / WireGuard IP 到 SAN） |

```bash
pip install pyserial
python tools/serial_probe.py --list           # 串口分类
python tools/serial_probe.py -p COM4          # 实时渲染对讲机屏幕
python tools/serial_probe.py -p COM4 --ptt-test 1.5   # 电平 PTT 测试

pip install sounddevice numpy
python tools/audio_loopback.py --meter -d 5   # 采集电平表（对讲机收信号应有读数）
```

启动服务：

```bash
python -m k1remote --list-ports              # 查看串口识别与变体推断结果
python -m k1remote                           # 串口与变体自动识别（Linux）
python -m k1remote --variant k1k5v3 -p COM7  # Windows 接 K1 / K5/K6 V3 需显式指定变体
python -m k1remote -p /dev/ttyUSB0 --host 0.0.0.0
```

操作流程：**▶ 开始收听**（同时请求麦克风，拒绝则仅收听）→ 勾选**发射使能** → **按住 PTT 说话**，松开结束发射。

#### HTTPS 局域网访问（麦克风必需）

浏览器规定麦克风只在 HTTPS（或本机 127.0.0.1）下可用，局域网其他设备操作必须启用 TLS：

```bash
# 生成自签证书（自动包含本机 IP；跨网段部署时加 --ip 指定）
python tools/make_cert.py --out certs

# 最小配置 server.toml
cat > server.toml <<'EOF'
[server]
port = 8443
tls_cert = "certs/server.pem"
tls_key = "certs/server.key"
token = "你的操作口令"
EOF

# 启动
python -m k1remote -c server.toml
```

操作端浏览器打开 `https://<服务端IP>:8443/?token=你的操作口令`，首次点击"高级 → 继续前往"接受自签证书（一次即可），token 之后自动记住。

### ③ 开机自启动（systemd 服务）

手动前台运行适合调试；长期值守 / 高点部署应注册为 systemd 服务——开机自启、崩溃自动拉起（拉起后服务端会重新打开串口并释放 PTT 状态）。

**方式一：一键部署脚本（推荐，Linux）**

```bash
bash deploy/install.sh
```

脚本自动完成：安装系统依赖（`python3-venv` / `libportaudio2`）→ 把用户加入 `dialout` 串口组 → 创建 venv 并装依赖 → 生成自签证书（mDNS 主机名与所有网卡 / WireGuard 地址写入 SAN）→ 生成配置文件 → 渲染并启用 `k1remote.service`（开机自启并立即启动）。可用 `CONFIG=`、`SERVICE_USER=` 环境变量定制；完整远程部署流程见[部署到 Linux 开发板](#-部署到-linux-开发板)。

**方式二：手动注册服务**

不跑脚本时，把模板里的占位符替换成实际值再安装：

```bash
sudo cp deploy/k1remote.service.template /etc/systemd/system/k1remote.service
sudo nano /etc/systemd/system/k1remote.service   # 替换 %USER% / %REPO_DIR% / %CONFIG%
sudo systemctl daemon-reload
sudo systemctl enable --now k1remote             # 开机自启并立即启动
```

常用运维命令：

```bash
systemctl status k1remote        # 服务状态
journalctl -u k1remote -f        # 实时日志（串口、接入、PTT 事件、网络质量）
sudo systemctl restart k1remote  # 改配置后重启
```

## ⌨️ 命令行参数

```text
python -m k1remote [-h] [-c CONFIG] [-p PORT] [--variant {k5v1,k1k5v3}]
                   [--baud BAUD] [--host HOST] [--http-port HTTP_PORT]
                   [--demo] [--list-ports] [-v]

  -c, --config       TOML 配置文件路径（缺省用内置默认值）
  -p, --port         对讲机串口（覆盖配置；auto 为自动识别）
  --variant          固件变体（覆盖配置与自动推断）
  --baud             串口波特率（覆盖配置与变体缺省）
  --host/--http-port Web 监听地址 / 端口（覆盖配置）
  --demo             演示模式：无硬件生成假屏幕流
  --list-ports       列出串口并按用途分类后退出
  -v, --verbose      调试日志
```

## ⚙️ 配置

所有配置项均有缺省值，最小配置只需 `[server]` 的 TLS 部分。完整模板见 [deploy/config.example.toml](deploy/config.example.toml)。

| 配置项 | 缺省 | 说明 |
|---|---|---|
| `server.port` | `8080` | Web 监听端口（TLS 示例常用 8443） |
| `server.tls_cert` / `tls_key` | 空 | 配置后启用 HTTPS（局域网麦克风必需） |
| `server.token` | 空 | 操作口令；非空时访问需 `?token=` 认证 |
| `radio.variant` | `auto` | 固件变体：`auto` / `k5v1` / `k1k5v3` |
| `radio.port` | `auto` | 对讲机串口；`auto` 自动识别 |
| `radio.max_tx_s` | `180` | 最长连续发射（秒），看门狗强制释放 |
| `radio.tx_tail_ms` | `400` | 松开 PTT 后的尾音保护 |
| `aioc.port` | `auto` | AIOC 串口（PTT 电平控制），按 USB VID:PID 识别 |
| `audio.device` / `output_device` | 空 | 收音 / 发话设备名子串；空 = 优先匹配 AIOC |
| `audio.rx_gain` / `tx_gain` | `1.0` | 收听 / 发话软件增益（音量小就调大，过大放大底噪/过调） |
| `audio.capture_latency` | `0.2` | 采集缓冲目标时长（秒）：深缓冲吸收调度扰动防周期性卡顿，不增延迟 |
| `audio.rx_fec_loss` | `10` | 下行 Opus 带内 FEC 预期丢包率（%），0 = 关闭 |

## 🍓 部署到 Linux 开发板

见 **[deploy/README.md](deploy/README.md)**（电台设置清单、刷机与接线、一键安装、验证、访问命名策略、常见问题排障、升级流程）。实测平台为树莓派 4B 与 Orange Pi Zero 2W，部署流程一致（以下以树莓派默认用户 `pi` 为例，Orange Pi 替换用户名与 IP 即可）。核心三步：

```bash
# ① 同步仓库到树莓派（rsync 或 scp，见部署手册）
rsync -av --exclude venv --exclude certs --exclude __pycache__ k1remote/ pi@<PI_IP>:~/k1remote/

# ② 一键安装（装依赖、串口权限、venv、自签证书、配置、注册并启用 systemd 服务）
ssh pi@<PI_IP> "cd ~/k1remote && bash deploy/install.sh"

# ③ 编辑配置后重启服务
ssh pi@<PI_IP> "cd ~/k1remote && nano server.toml && sudo systemctl restart k1remote"
```

## 📁 目录结构

```text
k1remote/
├─ docs/                     # 实现计划（PLAN.md）与 K5Viewer 串口协议文档
├─ server/
│  ├─ k1remote/              # 服务端包
│  │  ├─ webapp.py           #   HTTPS 静态页 + WebSocket 信令/控制/鉴权
│  │  ├─ webrtc.py           #   aiortc Peer 管理 + 下行音轨
│  │  ├─ ptt.py              #   PTT 状态机 + 看门狗（DTR 驱动 + 发射期静默联动）
│  │  ├─ audio.py            #   声卡采集/播放 + 电平表
│  │  ├─ opus_fec.py         #   libopus 带内 FEC 编码（ctypes 直连，可选）
│  │  ├─ config.py           #   TOML 配置与固件变体
│  │  └─ radio/              #   串口协议 / K5Viewer 客户端 / AIOC PTT / 端口识别 / demo
│  └─ tests/                 # pytest 单元测试
├─ web/                      # 前端（原生 ES Modules + Canvas，无构建步骤）
├─ tools/                    # serial_probe / audio_loopback / make_cert 开发与验收工具
└─ deploy/                   # Linux 开发板部署包：install.sh、systemd 模板、配置模板、部署手册
```

## 📚 文档

| 文档 | 内容 |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | 完整实现计划：方案评估、固件调研、PTT 定稿依据、里程碑与排查记录 |
| [docs/protocol-k5viewer.md](docs/protocol-k5viewer.md) | K5Viewer 串口协议参考（k5v1 主体 + k1k5v3 差异附录） |
| [deploy/README.md](deploy/README.md) | 部署手册：电台设置、刷机接线、安装验证、排障表、升级 |

## 🗺️ 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 串口协议库 + 探测工具 | ✅ 完成 |
| M1 | 屏幕镜像 + 远程键盘 | ✅ 完成 |
| M2 | 音频链路硬件验证（AIOC PTT / 环录） | ✅ 完成 |
| M3 | WebRTC 下行（远程收听） | ✅ 完成 |
| M4 | WebRTC 上行 + PTT 闭环（真机 QSO） | ✅ 完成 |
| M5 | HTTPS + Token + 软件增益 + Linux 开发板部署包 | ✅ 完成 |

> 项目范围内的里程碑（M0–M5）已全部完成。跨局域网高点部署的网络打通（WireGuard 等隧道）属部署环节而非本项目代码范围，见[部署手册](deploy/README.md)；后续功能方向见[未来展望](#-未来展望)。

## 🔮 未来展望

想法池（欢迎提 [Issue](https://github.com/bg9cxn/k1remote/issues) 讨论认领）——大多是在现有"服务端已有音频流 + 串口控制"基础上的增量玩法：

- 📡 **APRS 接收解码与测距**：对讲机音频已在服务端，接入 AFSK1200 解码（direwolf / multimon-ng）即可在状态面板显示附近电台的**呼号 / 位置 / 方位**；浏览器端用 Geolocation API 取操作端位置，实时计算**距离你手机的直线距离**（"东南方 3.2 km"）。进一步用 OpenStreetMap 画出台站地图与运动轨迹；远期支持反向发送自己的位置信标（需持照合规操作）。
- 🔔 **守候模式与信号告警**：指定信道 / CTCSS 守听，捕获到信号时浏览器通知或推送到手机（Telegram / Bark webhook）——高点远程台变身"区域耳朵"，静默期零打扰。
- 🛰️ **卫星过境追踪**：内置 TLE 预报，列出业余卫星 / ISS 未来过境的时间与仰角，自动守听 + 录音，ISS SSTV 活动期间自动收图。
- 📺 **SSTV 慢扫描电视收图**：收到 SSTV 信号直接解码成图片，在浏览器里看"从电波里浮出来的照片"。
- 📝 **自动 QSO 日志**：发射 / 换频 / 信号报告自动记录，一键导出 ADIF 接入日志软件；解码到的呼号自动查询（QRZ / hamDB）显示对方信息。

## 🧪 测试

```bash
cd server
pip install -e ".[dev]"
python -m pytest
```

覆盖串口协议解析、PTT 状态机、音频轨水位控制、Web 唤醒逻辑等核心模块；推送后由 [GitHub Actions](.github/workflows/ci.yml) 在 Ubuntu / Windows 双平台自动运行。

## 🙏 致谢

- [AIOC — All-in-One-Cable](https://github.com/skuep/AIOC)：一根线搞定串口数据 / 音频 / 电平 PTT，本项目硬件方案的基石；开源设计，可在嘉立创（JLCPCB）打板自制
- [F4HWN 固件](https://github.com/armel/uv-k5-firmware-custom)与 [UV Studio](https://armel.github.io/uvstudio/)（Egzumer 系）：两个变体固件的基座，以及刷机 / 校准工具
- [aiortc](https://github.com/aiortc/aiortc)、[aiohttp](https://github.com/aio-libs/aiohttp)、[sounddevice](https://github.com/spatialaudio/python-sounddevice)、[pyserial](https://github.com/pyserial/pyserial)：服务端核心依赖

## 📄 许可证

Copyright © 2026 BG9CXN

本项目以 [Apache License 2.0](LICENSE) 发布。二次开发与分发请遵循协议条款，保留版权与许可声明。
