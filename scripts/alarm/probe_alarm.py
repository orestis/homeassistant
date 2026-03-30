"""
Probe the UltraSync+ alarm panel at 192.168.1.6 for open ports and services.

Scans all 65535 TCP ports using async sockets for speed, then attempts
HTTP(S) on any open ports. Outputs a summary suitable for sharing with
the alarm installer.

Usage:
    python scripts/alarm/probe_alarm.py [--host 192.168.1.6] [--top-ports]
"""

import argparse
import asyncio
import socket
import ssl
import struct
import sys
from datetime import datetime

HOST_DEFAULT = "192.168.1.6"
MAC = "20:97:27:78:3b:8a"
CONCURRENCY = 500  # parallel connection attempts
TIMEOUT = 1.5  # seconds per connection attempt

# Well-known ports relevant to alarm panels / IoT
KNOWN_PORTS = {
    22: "SSH",
    23: "Telnet",
    80: "HTTP",
    81: "HTTP-alt",
    443: "HTTPS",
    502: "Modbus",
    1025: "NFS/IIS",
    1883: "MQTT",
    4000: "ICQ/custom",
    4443: "HTTPS-alt",
    5000: "UPnP",
    5683: "CoAP",
    8080: "HTTP-proxy",
    8443: "HTTPS-alt",
    8883: "MQTT-TLS",
    8888: "HTTP-alt",
    9090: "HTTP-alt",
    10000: "Webmin",
}


async def check_port(sem: asyncio.Semaphore, host: str, port: int) -> int | None:
    async with sem:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=TIMEOUT
            )
            writer.close()
            await writer.wait_closed()
            return port
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return None


async def scan_ports(host: str, ports: list[int]) -> list[int]:
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [check_port(sem, host, p) for p in ports]
    results = await asyncio.gather(*tasks)
    return sorted(p for p in results if p is not None)


def try_http(host: str, port: int, use_tls: bool = False) -> str | None:
    """Attempt a simple HTTP GET and return the first few lines."""
    try:
        sock = socket.create_connection((host, port), timeout=3)
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.sendall(b"GET / HTTP/1.0\r\nHost: %b\r\n\r\n" % host.encode())
        data = sock.recv(2048)
        sock.close()
        return data.decode("utf-8", errors="replace")[:500]
    except Exception as e:
        return f"(error: {e})"


def try_banner(host: str, port: int) -> str | None:
    """Try to grab a banner by just connecting and reading."""
    try:
        sock = socket.create_connection((host, port), timeout=3)
        sock.settimeout(2)
        data = sock.recv(1024)
        sock.close()
        return data.decode("utf-8", errors="replace")[:200]
    except Exception:
        return None


def ping_check(host: str) -> bool:
    """Quick reachability check via TCP connect to see if host is up."""
    import subprocess

    result = subprocess.run(
        ["ping", "-c", "1", "-W", "2", host],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_arp_entry(host: str) -> str | None:
    import subprocess

    result = subprocess.run(["arp", "-a"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if host in line:
            return line.strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Probe alarm panel for open ports")
    parser.add_argument("--host", default=HOST_DEFAULT, help="Target IP address")
    parser.add_argument(
        "--top-ports",
        action="store_true",
        help="Only scan well-known ports (fast, ~100 ports)",
    )
    args = parser.parse_args()
    host = args.host

    print("=" * 60)
    print(f"  UltraSync+ Alarm Panel Network Probe")
    print(f"  Target: {host}  (MAC: {MAC})")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 60)

    # Reachability
    print("\n[1/4] Checking reachability...")
    if ping_check(host):
        print(f"  ✓ Host {host} responds to ping")
    else:
        print(f"  ✗ Host {host} does NOT respond to ping — is it powered on?")
        sys.exit(1)

    # ARP
    print("\n[2/4] Checking ARP table...")
    arp = get_arp_entry(host)
    if arp:
        print(f"  ✓ ARP entry: {arp}")
    else:
        print(f"  ✗ No ARP entry found for {host}")

    # Port scan
    if args.top_ports:
        ports = sorted(KNOWN_PORTS.keys())
        label = f"{len(ports)} well-known ports"
    else:
        ports = list(range(1, 65536))
        label = "all 65535 ports"

    print(f"\n[3/4] Scanning {label} on {host}...")
    print(f"  (concurrency={CONCURRENCY}, timeout={TIMEOUT}s per port)")
    open_ports = asyncio.run(scan_ports(host, ports))

    if open_ports:
        print(f"\n  ✓ Found {len(open_ports)} open port(s):")
        for port in open_ports:
            svc = KNOWN_PORTS.get(port, "unknown")
            print(f"    Port {port:>5}/tcp  OPEN  ({svc})")
    else:
        print(f"\n  ✗ No open TCP ports found")

    # Service probing
    print(f"\n[4/4] Probing services on open ports...")
    if not open_ports:
        print("  (nothing to probe)")
    else:
        for port in open_ports:
            print(f"\n  --- Port {port} ---")
            # Try banner grab
            banner = try_banner(host, port)
            if banner:
                print(f"  Banner: {banner}")

            # Try HTTP
            resp = try_http(host, port, use_tls=False)
            if resp and "HTTP" in resp:
                print(f"  HTTP response:\n    {resp[:300]}")
            else:
                # Try HTTPS
                resp = try_http(host, port, use_tls=True)
                if resp and "HTTP" in resp:
                    print(f"  HTTPS response:\n    {resp[:300]}")

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    if open_ports:
        print(f"  Open ports: {', '.join(str(p) for p in open_ports)}")
        print(f"  → Local API access may be possible!")
        print(f"  → Next step: try `pip install ultrasync` and connect.")
    else:
        print(f"  No open ports found. The panel's local web server is disabled.")
        print(f"  The device only makes outbound connections to the UltraSync cloud.")
        print()
        print(f"  To enable local access, ask your installer to:")
        print(f"    1. Enter the panel's programming mode")
        print(f"    2. Go to Feature Location 19")
        print(f"    3. Enable Option 6 (permanent local network access)")
        print()
        print(f"  Once enabled, port 80 should open and local control becomes")
        print(f"  possible via the 'ultrasync' Python library or the")
        print(f"  'ha-ultrasync' Home Assistant HACS integration.")
    print("=" * 60)


if __name__ == "__main__":
    main()
