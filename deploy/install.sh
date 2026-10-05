#!/usr/bin/env bash
# K1Remote 树莓派一键部署脚本（在树莓派上、于仓库目录内执行）：
#   bash deploy/install.sh
# 可选环境变量：
#   CONFIG=server.toml     服务端配置文件（相对仓库目录）
#   SERVICE_USER=pi        systemd 运行用户（缺省当前用户）
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${CONFIG:-server.toml}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

echo "== K1Remote 部署 =="
echo "仓库目录: $REPO_DIR"
echo "运行用户: $SERVICE_USER"
echo "配置文件: $CONFIG"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "错误：请在树莓派（Linux）上运行本脚本"; exit 1
fi

SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; fi

# ---- 1. 系统依赖 ----
echo "== 安装系统依赖 =="
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq python3-venv python3-pip libportaudio2

# ---- 2. 串口权限 ----
echo "== 串口权限（dialout 组）=="
if ! id -nG "$SERVICE_USER" | grep -qw dialout; then
    $SUDO usermod -aG dialout "$SERVICE_USER"
    echo "已将 $SERVICE_USER 加入 dialout 组（重新登录后生效；本脚本继续用当前会话）"
fi

# ---- 3. Python 虚拟环境与服务端依赖 ----
echo "== 创建虚拟环境并安装依赖（首次较慢）=="
python3 -m venv "$REPO_DIR/venv"
"$REPO_DIR/venv/bin/pip" install -q --upgrade pip
"$REPO_DIR/venv/bin/pip" install -q -e "$REPO_DIR/server"

# ---- 4. 证书与配置 ----
cd "$REPO_DIR"
if [[ ! -f certs/server.pem ]]; then
    echo "== 生成自签 HTTPS 证书（含 mDNS 名与所有网卡/WG 地址）=="
    CERT_ARGS=(--out "$REPO_DIR/certs" --dns "$(hostname -s).local")
    # WireGuard 隧道 IP 一并写入证书（跨网段/隧道访问不再依赖证书更新）
    if command -v wg >/dev/null 2>&1 && wg show 2>/dev/null | grep -q interface; then
        for ip in $(wg show wg0 2>/dev/null | awk '/interface/ {print $3}'); do
            CERT_ARGS+=(--ip "$ip")
        done
    fi
    for ip in $(hostname -I 2>/dev/null); do
        case "$ip" in
            10.*) CERT_ARGS+=(--ip "$ip") ;;  # 常见 WG/内网段预埋
        esac
    done
    "$REPO_DIR/venv/bin/python" tools/make_cert.py "${CERT_ARGS[@]}"
fi
if [[ ! -f "$CONFIG" ]]; then
    echo "== 生成配置文件 $CONFIG（请编辑 token 等项）=="
    sed -e "s|tls_cert = \"certs/server.pem\"|tls_cert = \"$REPO_DIR/certs/server.pem\"|" \
        -e "s|tls_key = \"certs/server.key\"|tls_key = \"$REPO_DIR/certs/server.key\"|" \
        deploy/config.example.toml > "$CONFIG"
fi

# ---- 5. systemd 服务 ----
echo "== 安装 systemd 服务 =="
sed -e "s|%USER%|$SERVICE_USER|" \
    -e "s|%REPO_DIR%|$REPO_DIR|g" \
    -e "s|%CONFIG%|$REPO_DIR/$CONFIG|g" \
    deploy/k1remote.service.template | $SUDO tee /etc/systemd/system/k1remote.service > /dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable k1remote.service
$SUDO systemctl restart k1remote.service
sleep 2
$SUDO systemctl --no-pager -l status k1remote.service | head -12 || true

# ---- 6. 访问信息 ----
PORT=$(grep -oP '^\s*port\s*=\s*\K[0-9]+' "$CONFIG" || echo "8443")
TOKEN=$(grep -oP '^\s*token\s*=\s*"\K[^"]+' "$CONFIG" || echo "")
IP=$(hostname -I | awk '{print $1}')
echo
echo "== 部署完成 =="
echo "服务状态: systemctl status k1remote"
echo "实时日志: journalctl -u k1remote -f"
echo "访问地址: https://$IP:$PORT/?token=$TOKEN"
echo "  （首次访问接受自签证书；电台菜单建议：SetOff=OFF、TxTOut=180s、音量 60-80%）"
echo "升级流程: 更新仓库文件 → venv/bin/pip install -e server → systemctl restart k1remote"
