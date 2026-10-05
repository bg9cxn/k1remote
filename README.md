# K1Remote

把 **UV-K5**（自研固件 `uv-k5-firmware-custom-remote`，V1 固件 + 串口控制）放到信号好的高处，通过树莓派在浏览器里完成远程收听、发射（PTT）与控制。
实现计划、协议文档见 `docs/`；同时以"固件变体"配置保留对 UV-K1 / UV-K5 V3 硬件（`uv-k1-k5v3-firmware-custom`）的兼容。

## 当前状态

- **M0-M4 完成（真机验证）**：屏幕镜像、远程键盘、WebRTC 收听/发话、PTT 闭环（尾音保护/看门狗/静默联动），实机 QSO 基本无问题
- **M5 完成**：HTTPS + token 认证、软件增益（rx_gain/tx_gain）、树莓派部署包（`deploy/`：install.sh + systemd + 配置模板 + 部署手册）
- **M6（用户主导）**：WireGuard 打通两端网络 → 树莓派上高点部署

## 快速开始

### 1. 探测串口与 PTT（任何装了 Python 的机器，含 Windows 开发机）

对讲机 K 型口经 AIOC 连电脑：

> **先在电台菜单里把 SetOff（自动深睡）设为 OFF**——固件的深睡倒计时只认射频收发，
> 串口保活不能阻止入睡，睡后推流会静默（服务端有停滞自动唤醒兜底，但部署电台应直接关闭）。

```bash
pip install pyserial
python tools/serial_probe.py --list                    # 查看串口分类（radio / aioc）
python tools/serial_probe.py -p COM4                   # 实时渲染对讲机屏幕（默认 38400）
python tools/serial_probe.py -p COM4 --inject up       # 远程按一下 ↑ 键
python tools/serial_probe.py -p COM4 --ptt-test 1.5    # 电平 PTT 测试（静默保活 + 退出兜底释放）
python tools/serial_probe.py -p COM4 --ptt-test 1.5 --no-mute   # 对照实验：不静默则键不上
```

> PTT 原理：DTR=1 & RTS=0 经 AIOC 拉低 3.5mm Sleeve 键控对讲机（该触点与主机→对讲机数据共用，
> AIOC 固件数据优先会强制释放 PTT，因此**发射期间必须静默保活**——服务端已内置联动）。

### 2. 音频链路验证（M2）

```bash
pip install sounddevice numpy
python tools/audio_loopback.py --list              # 枚举音频设备（找 AIOC）
python tools/audio_loopback.py --meter -d 5        # 采集电平表（对讲机收信号应有读数）
python tools/audio_loopback.py --record rx.wav -d 5      # 环录 5 秒存 WAV
python tools/audio_loopback.py --sine -d 3         # 播放 440Hz（配合 --ptt-test 空中应听到纯音）
```

### 3. 跑 Web 服务（演示模式，无需硬件）

```bash
cd server
pip install aiohttp pyserial aiortc sounddevice numpy
python -m k1remote --demo
# 浏览器打开 http://127.0.0.1:8080
# ▶ 开始收听（WebRTC 正弦音）→ 勾选发射使能 → 按住 PTT（空格键）→ 计时/状态
```

### 4. 跑 Web 服务（接真机，完整 QSO）

```bash
python -m k1remote                # 串口与 AIOC 自动识别（V1 单线共用串口）
python -m k1remote -p /dev/ttyUSB0 --host 0.0.0.0
python -m k1remote --variant v3   # UV-K1 / UV-K5 V3 硬件（USB CDC + RF Log）
python -m k1remote --list-ports   # 查看串口识别结果
```

操作流程：**▶ 开始收听**（会同时请求麦克风；拒绝则仅收听）→ **勾选发射使能** → **按住 PTT 说话**（松开结束发射）。

### 5. 局域网访问（麦克风必需 HTTPS）

浏览器规定：**麦克风只在 HTTPS（或本机 127.0.0.1）下可用**。从局域网其他设备操作时必须启用 TLS：

```bash
# ① 生成自签证书（自动包含本机 IP；跨网段部署时加 --ip 树莓派的 IP）
python tools/make_cert.py --out certs

# ② 配置（如 server.toml）
[server]
host = "0.0.0.0"
port = 8443
tls_cert = "certs/server.pem"
tls_key = "certs/server.key"
token = "你的操作口令"          # 可选：启用后 WS 需 ?token= 认证

[radio]
variant = "v1"

[audio]
device = "AIOC"        # 收音输入设备（缺省自动匹配 AIOC）
output_device = "AIOC" # 发话输出设备
rx_gain = 3.0          # 收听软件增益（音量小就调大，1-8；过大放大底噪）
tx_gain = 1.0          # 发话软件增益（对方电台听着小就调大，过大过调）

# ③ 启动
python -m k1remote -c server.toml
```

操作端浏览器打开 `https://<服务端IP>:8443/?token=你的操作口令`：
- 首次访问点击"高级 → 继续前往"接受自签证书（一次即可）；
- 之后麦克风可用，token 会记住（存 sessionStorage），刷新自动携带；
- 若页面显示"仅收听"，说明当前不是安全上下文或麦克风被拒——检查地址是否 https:// 。

## 目录

```
docs/                  计划与协议文档（PLAN.md、protocol-k5viewer.md）
server/                Python 服务端（k1remote 包 + pytest 测试）
web/                   前端（无构建步骤，原生 ES Modules）
tools/                 serial_probe / audio_loopback / make_cert 等开发与验收工具
deploy/                树莓派部署包：install.sh、systemd 模板、配置模板、部署手册
```

## 树莓派部署

见 [deploy/README.md](deploy/README.md)（电台设置清单、一键安装、验证、常见问题、升级流程）。
核心三步：同步仓库到树莓派 → `bash deploy/install.sh` → 编辑 `server.toml` 后重启服务。
