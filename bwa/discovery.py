"""UDP discovery for Balboa WiFi spa controllers.

Sends a broadcast to port 30303 and collects responses from devices
whose MAC address starts with 00-15-27 (Balboa Instruments).
"""

import asyncio
import logging
import socket

logger = logging.getLogger("bwa.discovery")

DISCOVERY_PORT = 30303
DISCOVERY_MESSAGE = b"Discovery: Who is out there?"
BALBOA_MAC_PREFIX = "00-15-27-"


async def discover(timeout: float = 5.0, exhaustive: bool = False) -> dict[str, str]:
    """Return a dict of {ip: hostname} for discovered spas."""
    loop = asyncio.get_event_loop()
    spas: dict[str, str] = {}
    found_event = asyncio.Event()

    class DiscoveryProtocol(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport
            sock = transport.get_extra_info("socket")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            transport.sendto(DISCOVERY_MESSAGE, ("255.255.255.255", DISCOVERY_PORT))

        def datagram_received(self, data, addr):
            ip = addr[0]
            try:
                text = data.decode("ascii", errors="ignore")
                lines = text.strip().split("\r\n")
                if len(lines) >= 2:
                    name = lines[0].strip()
                    mac = lines[1].strip()
                    if mac.startswith(BALBOA_MAC_PREFIX):
                        logger.info("Found spa at %s (%s, %s)", ip, name, mac)
                        spas[ip] = name
                        if not exhaustive:
                            found_event.set()
            except Exception as exc:
                logger.debug("Discovery parse error from %s: %s", ip, exc)

        def error_received(self, exc):
            logger.debug("Discovery error: %s", exc)

    transport, _ = await loop.create_datagram_endpoint(
        DiscoveryProtocol,
        local_addr=("0.0.0.0", 0),
        family=socket.AF_INET,
    )

    try:
        if exhaustive:
            await asyncio.sleep(timeout)
        else:
            try:
                await asyncio.wait_for(found_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
    finally:
        transport.close()

    return spas
