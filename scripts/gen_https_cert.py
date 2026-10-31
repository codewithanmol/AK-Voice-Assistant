"""
AK self-signed HTTPS certificate generator (free, no domain needed).

Generates certs/https_cert.pem (cert) + certs/https_key.pem (key)
with SANs for localhost, 127.0.0.1 and current LAN/Tailscale IPs.

Usage:  python gen_https_cert.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import ipaddress
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(os.path.dirname(_HERE), "certs")
CERT_PEM = os.path.join(CERT_DIR, "https_cert.pem")
KEY_PEM = os.path.join(CERT_DIR, "https_key.pem")

DAYS_VALID = 3650  # ~10 saal, free health check ka jhanjhat nahi


def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    # Tailscale / virtual adapters bhi try karo
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            sockaddr = info[4][0]
            if not sockaddr.startswith("127."):
                ips.add(sockaddr)
    except OSError:
        pass
    # Tailscale 100.x IP (college/remote se https certificate match kare)
    try:
        exe = r"C:\Program Files\Tailscale\tailscale.exe"
        if not os.path.isfile(exe):
            exe = "tailscale"
        out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                             text=True, timeout=4)
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("100."):
                ips.add(line)
    except Exception:
        pass
    return sorted(ips)


def main() -> None:
    os.makedirs(CERT_DIR, exist_ok=True)
    ips = _local_ips()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    names = [x509.NameAttribute(NameOID.COMMON_NAME, "AK Local HTTPS")]
    subject = issuer = x509.Name(names)

    now = datetime.now(timezone.utc)
    san = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    for ip in ips:
        if ip not in ("127.0.0.1",):
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=DAYS_VALID))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(CERT_PEM, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(KEY_PEM, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    print(f"[cert] Certificate: {CERT_PEM}")
    print(f"[cert] Key:         {KEY_PEM}")
    print(f"[cert] SANs (IPs):  {', '.join(ips)}")
    print(f"[cert] Valid:       {DAYS_VALID} days (self-signed, free)")


if __name__ == "__main__":
    main()