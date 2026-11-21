from __future__ import annotations

import io
import ipaddress
import socket

import qrcode


def _is_usable_lan_ipv4(ip_str: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def get_lan_ipv4_addresses() -> list[str]:
    """
    Detect usable local LAN IPv4 addresses on this machine.
    Returns the primary routed LAN IP first, followed by any other non-loopback IPv4 addresses.
    """
    candidates: list[str] = []

    # 1. Primary outbound interface IP (no packets are actually sent for UDP connect)
    for probe_target in (("192.168.1.1", 80), ("10.0.0.1", 80), ("8.8.8.8", 80)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect(probe_target)
            ip = sock.getsockname()[0]
            if _is_usable_lan_ipv4(ip) and ip not in candidates:
                candidates.append(ip)
        except OSError:
            pass
        finally:
            sock.close()

    # 2. All IPv4 addresses bound to hostname
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if _is_usable_lan_ipv4(ip) and ip not in candidates:
                candidates.append(ip)
    except OSError:
        pass

    # Prefer RFC1918 private LAN addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) first
    def _sort_key(ip_str: str) -> tuple[int, int, str]:
        ip = ipaddress.IPv4Address(ip_str)
        is_192 = 0 if ip_str.startswith("192.168.") else 1
        is_private = 0 if ip.is_private else 1
        return (is_private, is_192, ip_str)

    # Keep first primary if private, otherwise sort nicely
    if len(candidates) > 1:
        primary = candidates[0]
        rest = sorted(candidates[1:], key=_sort_key)
        if ipaddress.IPv4Address(primary).is_private:
            candidates = [primary, *rest]
        else:
            candidates = sorted(candidates, key=_sort_key)

    return candidates


def generate_qr_png_bytes(data: str, box_size: int = 8, border: int = 3) -> bytes:
    """Render a crisp PNG QR code image for the given text/URL."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
