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
| 服务起不来：串口打不开（Permission denied） | 用户未在 dialout 组：`sudo usermod -aG dialout pi` 后重新登录 |
| 屏幕流没有 | 检查电台已开机且未深睡；`journalctl -u k1remote -f` 看保活；服务端有停滞自动唤醒 |
| 收听无声 | `journalctl` 找 "声卡采集已启动"；`tools/audio_loopback.py --meter` 验证 AIOC 输入 |
| 发话电台无声音 | 看日志"发话输出设备"行：Pi 上 AIOC 名为 "All-In-One-Cable: USB Audio"（服务端自动匹配）；仍不行用 `audio_loopback.py --sine` + 电平 PTT 验证硬件通路；`tx_gain`/aioc-util 调电平 |
| 收听有爆音（咔/哒声） | ① `python tools/audio_loopback.py --diag` 输出贴出（看服务端选中的设备与 USB 休眠状态）；② `journalctl -u k1remote | grep "音频诊断"` 看 积压裁剪/迟到/溢出 计数——裁剪增长=树莓派负载或事件循环停顿；溢出增长=USB/ALSA 层；③ 对照：电台自己喇叭是否同样爆（有=电台/EMI 侧）；④ 菜单导航时爆音变密 = 屏幕数据干扰（反馈给开发者） |
| USB 掉线/设备消失 | 禁用 USB 自动休眠：`sudo sh -c 'echo -1 > /sys/module/usbcore/parameters/autosuspend'`（永久：内核启动参数加 `usbcore.autosuspend=-1`）；磁环/地网/降功率 |
| 延迟大 | 确认浏览器较新（jitterBufferTarget）；确认走的是局域网/WG 隧道而非公网中转 |
| USB 掉线（发射时） | EMI：降功率、磁环、地网；掉线后服务端自动重连 |

## 6. 升级

```bash
cd ~/k1remote
# 同步新代码后：
venv/bin/pip install -e server
sudo systemctl restart k1remote
```
