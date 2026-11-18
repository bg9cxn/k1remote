# 部署手册（树莓派 + 高点远端台）

## 0. 对讲机设置清单（上高点前在电台菜单里确认）

| 菜单项 | 设置 | 原因 |
|---|---|---|
| SetOff（自动深睡） | **OFF** | 深睡后串口推流静默，保活唤不醒（服务端有自动唤醒兜底，但远端台应关闭） |
| TxTOut（发射超时） | **180s**（与服务端 max_tx_s 一致或略长） | 卡发射的最终兜底 |
| 音量 | 60–80% | 决定送入 AIOC 的收音电平（过大削顶、过小底噪大） |
| 功率 | 按需（建议先低功率调通） | 发射时 RF 可能窜入 USB 导致掉线，先低功率验证 |
| 频率/信道 | 合法业余频段，写入信道 | 远端无法安全改频，上点前预置好 |

## 1. 硬件连接

- 对讲机 K 型口 ← AIOC ← USB 线 → 树莓派 USB 口（数据 + 音频 + PTT 一线）。
- 树莓派供电建议 UPS/充电宝方案；WiFi 或有线接入路由。
- EMI 防护：天线加地网（tiger tail）或磁环，避免发射时 USB 掉线。

## 1.5 K1 / K5 V3 硬件（k1k5v3 变体）

K 型口仍保留 38400 UART（与 k5v1 完全相同，CPS 写频与 K5Viewer 推流均可用，固件每个
主循环 tick 优先检测 K 头，`App/k5viewer.c`）；机身 Type-C 为新增的并行数据口
（USB CDC，Linux 枚举为 `/dev/ttyACM*`）。

**部署定稿双线：Type-C 传数据 + K 型口 AIOC 传音频/PTT**——带宽富余（RF Log 实时包
1.6KB 在 38400 下需 ~420ms，CDC 无压力）、数据与 PTT1 无共触点（无 TXFRCPTT 仲裁
约束）、`variant = "auto"` 按串口类型零参数识别（Windows 下 CDC 也枚举为 `COM*`
区分不了，需显式 `--variant k1k5v3`）。

**刷机（一次性）**——固件须为启用 K5VIEWER 的 F4HWN **Fusion** 版
（单镜像同时兼容 K1 与 K5 V3；成品在固件仓 `uv-k1-k5v3-firmware-custom/archive/`）：

1. **备份校准数据（强烈建议）**：正常开机，浏览器打开
   [UV Studio → Dump Calibration](https://armel.github.io/uvstudio/#dump-calib)，
   按 SN 保存 `calibration.dat`（换固件/回原厂前先恢复）；
2. **刷固件**：对讲机进 **DFU 模式（刷机模式）**，打开
   [UV Studio → Flash Firmware](https://armel.github.io/uvstudio/#flash)，
   加载本地 `f4hwn.fusion.v6.0.0.bin`（或直接选官方 Fusion 目录版）→ 选串口 → 等进度条完成；
   无浏览器 Web Serial 时也可用命令行：
   `python uv-k1-k5v3-firmware-custom/tools/serialtool/cli.py flash -p <口> <固件.bin>`；
3. **刷后配置**：按 §0 清单确认 SetOff=OFF、TxTOut 等（Fusion 固件菜单项一致）；
4. 顺手验证：`python -m k1remote --list-ports` 应看到 radio 组的 `ttyACM*`（标注"疑似 K1/K5 V3"）。

连接（定稿双线）：机身 USB-C → 树莓派（数据），K 型口 ← AIOC → 树莓派（音频 + PTT）。
应急单线：手头没有 USB-C 数据线时，也可 K 型口 AIOC 一线全包（数据走 K 头 38400 UART）——
`python -m k1remote --port <AIOC口> --variant k1k5v3 --baud 38400`
（屏幕较慢，且依赖发射期静默机制；双口同时插不打架，固件只认收到保活的那个口，K 头优先）。

**上电验证顺序**（K1 到手后逐项过）：

```bash
python -m k1remote --list-ports                    # ① radio 组出现 ttyACM*（标注"疑似 K1/K5 V3"）
python tools/serial_probe.py -p /dev/ttyACM0 \
    --features 0x01                                # ② 屏幕流 + 注入（--features 缺省 0，k1k5v3 必须显式给 RF Log 位）
python tools/serial_probe.py -p /dev/ttyACM0 --features 0x01 \
    --save capture --no-render                      # ③ 存原始流，检查 capture.bin 中 55 AA 05 帧头确认 RF Log 在发
python tools/serial_probe.py --ptt-test 1.5        # ④ AIOC 电平 PTT 回归（K 型口）
python -m k1remote                                 # ⑤ 全链路：variant=auto 应打印"推断固件变体: k1k5v3"
```

⑤ 的验收：屏幕镜像 ≥3fps、虚拟键盘生效、收发记录出现 RF Log 行、状态条显示电池电压与
当前信道、PTT 键上/释放干净；服务端检测到深睡标志会自动注入 EXIT 唤醒（部署仍建议 SetOff=OFF）。

## 2. 软件部署

**Windows（PowerShell，系统自带 ssh/scp）：**

```powershell
ssh pi@<PI_IP> "mkdir -p ~/k1remote"
cd D:\development\hamremote\k1remote
scp -r docs server web tools deploy README.md pi@<PI_IP>:~/k1remote/
ssh -t pi@<PI_IP> "cd ~/k1remote && bash deploy/install.sh"
```

**Linux/macOS（rsync）：**

```bash
rsync -av --exclude venv --exclude certs --exclude __pycache__ k1remote/ pi@<PI_IP>:~/k1remote/
ssh pi@<PI_IP> "cd ~/k1remote && bash deploy/install.sh"
```

（图形界面党：WinSCP 拖拽同样可以。）

然后在树莓派上：

```bash
nano server.toml            # 编辑 token、按需调 rx_gain/tx_gain
sudo systemctl restart k1remote
```

## 3. 验证

```bash
systemctl status k1remote          # active (running)
journalctl -u k1remote -f          # 实时日志：串口打开、客户端接入、PTT 事件
ls /dev/ttyACM* /dev/ttyUSB*       # 串口已枚举
python -m k1remote --list-ports    # （在 venv 外跑会缺依赖，用 venv/bin/python）
```

浏览器打开 `https://<PI_IP>:8443/?token=<口令>`（接受自签证书）→ 看到屏幕镜像 → 收听 → 发射。

## 3.5 访问命名策略与更换 IP

自签证书的 SAN 在**部署时一次性写入所有预期名字**（mDNS 主机名 + 各网卡 IP +
WireGuard 隧道 IP——install.sh 自动处理），之后换 IP、跨网段、走隧道都**不需要再动证书**。

| 访问路径 | 用什么地址 | 说明 |
|---|---|---|
| 同网段（家中局域网） | `https://<主机名>.local:8443` | mDNS 零配置（Win10+/iOS/Android 原生支持），树莓派 IP 随便换 |
| 跨网段 | 操作端 hosts 文件固定名，或路由器 **DHCP 静态租约** + 直接用 IP | hosts 方法：操作端加一行 `<IP> k1remote`（证书需含 `--dns k1remote`），改 IP 只改 hosts 一行 |
| WireGuard 隧道（家中↔高点） | WG 隧道 IP（如 `https://10.8.0.1:8443`） | 隧道地址天然静态；安装脚本自动把 wg0 的 IP 写进证书 |

**换 IP 后的处理（按成本从低到高）：**

1. **什么都不做**：新 IP 访问时浏览器警告页会多一条"域名不匹配"，点"高级 → 继续前往"后一切正常（含麦克风）；
2. **重签证书**（消除警告）：
   ```bash
   cd ~/k1remote
   venv/bin/python tools/make_cert.py --out certs \
       --dns "$(hostname -s).local" --dns k1remote --ip <新IP> --ip <WG_IP>
   sudo systemctl restart k1remote
   ```
3. **一劳永逸**：路由器给树莓派做 DHCP 静态租约（IP 不再变化），或统一走 WireGuard 隧道 IP 访问。

> 提示：浏览器对 token 的记忆按"协议+主机+端口"隔离——换了访问地址后，首次需要带 `?token=<口令>` 打开一次。
> 服务端配置（`host = "0.0.0.0"`）绑定所有网卡，与 IP 无关，无需修改。

## 4. 操作端（家中电脑/手机）

- 同一局域网：直接访问上述地址（或 mDNS 主机名）。
- 跨网段：任一侧起 WireGuard，隧道内互访（隧道 IP 已随 install.sh 写入证书 SAN；
  手动补签用 `python tools/make_cert.py --out certs --ip 10.8.0.x` 后重启服务）。
- 手机浏览器同样可用（触屏 PTT 按钮）。

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| PTT 按了没反应（屏幕流正常、UI 显示发射中） | ① 先排除电台侧：拔下 AIOC 用镊子短接 3.5mm 插头 sleeve↔GND（2.5mm 杆部），红灯应亮——不亮再试 tip；② 排除驱动层：`python -c "..."` 用 TIOCMGET 确认断言时 DTR=1/RTS=0；③ 排除线缆固件：AIOC 固件损坏时 DTR→PTT 路径整体失效（2026-11-04 实例），重刷官方固件即可修复：`sudo apt install dfu-util && sudo dfu-util -d 1209:7388 -a 0 -s 0x08000000:leave -D aioc-fw-1.4.1.bin`（固件从 [AIOC Releases](https://github.com/skuep/AIOC/releases) 下载；hw1.1 实测可用），刷后配置恢复默认无需重配 |
| 服务起不来：串口打不开（Permission denied） | 用户未在 dialout 组：`sudo usermod -aG dialout pi` 后重新登录 |
| 屏幕流没有 | 检查电台已开机且未深睡；`journalctl -u k1remote -f` 看保活；服务端检测到深睡标志会自动注入 EXIT 唤醒 |
| 收听无声 | `journalctl` 找 "声卡采集已启动"；`tools/audio_loopback.py --meter` 验证 AIOC 输入 |
| 发话电台无声音 | 看日志"发话输出设备"行：Pi 上 AIOC 名为 "All-In-One-Cable: USB Audio"（服务端自动匹配）；仍不行用 `audio_loopback.py --sine` + 电平 PTT 验证硬件通路；`tx_gain`/aioc-util 调电平 |
| 收听有爆音（咔/哒声） | ① `python tools/audio_loopback.py --diag` 输出贴出（看服务端选中的设备与 USB 休眠状态）；② `journalctl -u k1remote \| grep "音频诊断"` 看 积压裁剪/迟到/溢出 计数——裁剪增长=树莓派负载或事件循环停顿；溢出增长=USB/ALSA 层；③ 对照：电台自己喇叭是否同样爆（有=电台/EMI 侧）；④ 菜单导航时爆音变密 = 屏幕数据干扰（反馈给开发者）；⑤ **溢出持续增长的已定位根因与修复（2026-11-08）**：40ms ALSA 小缓冲 + 调度扰动超窗 → XRUN 自维持（升频实测无效，详见 PLAN.md 排查记录）——已默认修复（`capture_latency=0.2` 深缓冲 + aiortc 电平计算向量化）；"音频诊断"行仅在异常时打印，**完全消失即健康** |
| USB 掉线/设备消失 | 禁用 USB 自动休眠：`sudo sh -c 'echo -1 > /sys/module/usbcore/parameters/autosuspend'`（永久：内核启动参数加 `usbcore.autosuspend=-1`）；磁环/地网/降功率 |
| 延迟大 | 确认浏览器较新（jitterBufferTarget）；确认走的是局域网/WG 隧道而非公网中转 |
| 偶发卡顿/数码音（网络侧） | `journalctl -u k1remote \| grep "网络:"` 看 RTCP 实测（下行丢包率/抖动/RTT 每 30s 一行）；丢包已由 Opus 带内 FEC 冗余恢复（`rx_fec_loss`，2026-11-10）；延迟尖峰（手机 Wi-Fi 省电休眠可达 700ms+）FEC 救不了——插电/关省电/5GHz/近 AP；浏览器侧量化：`edge://webrtc-internals` 的 concealedSamples/fecPacketsReceived |
| USB 掉线（发射时） | EMI：降功率、磁环、地网；掉线后服务端自动重连 |

## 6. 升级

```bash
cd ~/k1remote
# 同步新代码后：
venv/bin/pip install -e server
sudo systemctl restart k1remote
```

> **2026-11 变体改名**：固件变体 ID 由 `v1`/`v3` 改为 `k5v1`/`k1k5v3`（裸版本号与项目名
> K1Remote 易混淆）。若 `server.toml` 仍写旧值，升级后启动会报"未知固件变体"并提示
> 新写法——把 `variant = "v1"` 改为 `"k5v1"`、`"v3"` 改为 `"k1k5v3"` 即可。
