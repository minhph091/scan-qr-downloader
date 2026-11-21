from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn

from backend.network_utils import get_lan_ipv4_addresses


def _open_browser_later(url: str) -> None:
    time.sleep(1.2)
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy ứng dụng ScanQRDownloader (Local + LAN Web App)")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Địa chỉ lắng nghe (mặc định: 0.0.0.0 để truy cập trong mạng LAN)",
    )
    parser.add_argument("--port", type=int, default=8765, help="Cổng chạy server (mặc định: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    parser.add_argument("--reload", action="store_true", help="Bật chế độ tự reload khi phát triển")
    args = parser.parse_args()

    browser_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    local_url = f"http://{browser_host}:{args.port}"
    lan_ips = get_lan_ipv4_addresses()

    print("=" * 64)
    print(f"  ScanQRDownloader (PC):         {local_url}")
    if args.host in ("0.0.0.0", "::") and lan_ips:
        for ip in lan_ips:
            print(f"  Chup anh qua LAN (Dien thoai): http://{ip}:{args.port}/capture")
    else:
        print(f"  Trang chup anh:                {local_url}/capture")
    print("=" * 64)

    if not args.no_browser:
        threading.Thread(target=_open_browser_later, args=(local_url,), daemon=True).start()

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
