#!/usr/bin/env python3
"""生成 k1remote 自签名 HTTPS 证书（跨平台，无需 openssl）。

  python tools/make_cert.py                        # 输出到 deploy/certs/
  python tools/make_cert.py --out certs --ip 192.168.6.13 --ip 10.8.0.1
  python tools/make_cert.py --dns k1remote.local --dns k1remote

SAN 自动包含：localhost、127.0.0.1、本机所有网卡地址、（Linux 上）
<m主机名>.local（Avahi/mDNS，同网段零配置访问），外加 --ip/--dns 指定的
条目。建议部署时把 WireGuard 隧道 IP 一并写入，换 IP/跨网段/走隧道
都不用再动证书。浏览器首次访问时接受一次"不安全"警告即可，之后
麦克风可用。

依赖：cryptography（aiortc 的依赖，已随服务端安装）。
"""

import argparse
import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def local_ips() -> list:
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))  # 不实际发包，仅取路由源地址
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def local_mdns_names() -> list:
    """Linux（树莓派）上 Avahi/mDNS 的 <主机名>.local；其他平台返回空。"""
    if not sys.platform.startswith("linux"):
        return []
    host = socket.gethostname().split(".")[0].strip()
    return [f"{host}.local"] if host else []


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 k1remote 自签名证书")
    ap.add_argument("--out", default="deploy/certs", help="输出目录")
    ap.add_argument("--ip", action="append", default=[],
                    help="额外写入证书的 IP（可多次，建议含 WireGuard 隧道 IP）")
    ap.add_argument("--dns", action="append", default=[],
                    help="额外写入证书的 DNS 名（可多次，如 k1remote.local）")
    ap.add_argument("--cn", default="k1remote")
    ap.add_argument("--days", type=int, default=3650)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    key_path, cert_path = out / "server.key", out / "server.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    ips = set()
    for raw in list(args.ip) + local_ips():
        try:
            ips.add(ipaddress.ip_address(raw))
        except ValueError:
            print(f"忽略无效地址: {raw}")
    dns_names = {"localhost"} | set(n.lower() for n in args.dns) | set(local_mdns_names())

    names = [x509.DNSName(n) for n in sorted(dns_names)] + \
            [x509.IPAddress(ip) for ip in sorted(ips)]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.cn)])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)  # 自签名
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=args.days))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"已生成:\n  {cert_path}\n  {key_path}")
    print(f"DNS 条目: {', '.join(sorted(dns_names))}")
    print(f"IP 条目: {', '.join(str(ip) for ip in sorted(ips))}")
    print("配置 server.toml:\n")
    print("  [server]\n  tls_cert = %r\n  tls_key  = %r\n"
          % (str(cert_path), str(key_path)))


if __name__ == "__main__":
    sys.exit(main())
