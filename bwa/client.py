"""High-level async client for Balboa WiFi spa controllers.

Supports three connection types (auto-detected from URI):
  tcp://192.168.x.x          — WiFi module (most common)
  /dev/ttyUSB0               — RS-485 serial (requires pyserial-asyncio)
  rfc2217://hostname:2217/   — Serial over TCP via ser2net (requires pyserial-asyncio)

RS-485 and RFC2217 connections use a send queue: messages are only transmitted
immediately after the spa broadcasts a Ready message, since RS-485 is a shared bus.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from bwa import messages as msgs
from bwa.message import Message, InvalidMessage

logger = logging.getLogger("bwa.client")

HEATING_MODES = ("ready", "rest", "ready_in_rest")


class BWAClient:
    def __init__(self, uri: str):
        self._uri = uri
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = bytearray()
        self._send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._is_serial = False

        # Cached state
        self.status: msgs.Status | None = None
        self.control_configuration: msgs.ControlConfiguration | None = None
        self.configuration: msgs.ControlConfiguration2 | None = None
        self.filter_cycles: msgs.FilterCycles | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        parsed = urlparse(self._uri)

        if parsed.scheme == "tcp":
            host = parsed.hostname
            port = parsed.port or 4257
            self._reader, self._writer = await asyncio.open_connection(host, port)
            self._is_serial = False
            logger.info("Connected to %s:%s via TCP", host, port)

        elif parsed.scheme in ("rfc2217", "telnet") or not parsed.scheme:
            try:
                import serial_asyncio  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "pyserial-asyncio is required for RFC2217/serial connections. "
                    "Install it with: pip install pyserial-asyncio"
                ) from exc
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._uri, baudrate=115200
            )
            self._is_serial = True
            logger.info("Connected to %s via RFC2217/serial", self._uri)

        else:
            # Bare path like /dev/ttyUSB0, or unknown scheme — treat as serial
            try:
                import serial_asyncio  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "pyserial-asyncio is required for serial connections. "
                    "Install it with: pip install pyserial-asyncio"
                ) from exc
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._uri, baudrate=115200
            )
            self._is_serial = True
            logger.info("Connected to %s via serial", self._uri)

    @property
    def full_configuration(self) -> bool:
        return all([
            self.status is not None,
            self.control_configuration is not None,
            self.configuration is not None,
            self.filter_cycles is not None,
        ])

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    async def poll(self) -> Message:
        """Read the next valid message from the spa.

        For RS-485/RFC2217 connections, sends the next queued outgoing message
        immediately upon receiving a Ready signal.
        """
        while True:
            message, consumed = Message.parse_frame(bytes(self._buffer))

            if message is not None:
                del self._buffer[:consumed]

                # RS-485: send queued message right after Ready
                if self._is_serial and isinstance(message, msgs.Ready):
                    if not self._send_queue.empty():
                        raw = self._send_queue.get_nowait()
                        self._writer.write(raw)
                        await self._writer.drain()

                # Update cached state
                if isinstance(message, msgs.Status):
                    self.status = message
                elif isinstance(message, msgs.ControlConfiguration):
                    self.control_configuration = message
                elif isinstance(message, msgs.ControlConfiguration2):
                    self.configuration = message
                elif isinstance(message, msgs.FilterCycles):
                    self.filter_cycles = message

                return message

            elif consumed > 0:
                # parse_frame found no start byte — discard consumed bytes
                del self._buffer[:consumed]

            # Need more data
            try:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise ConnectionError("Spa connection closed (EOF)")
                self._buffer.extend(chunk)
            except (ConnectionResetError, OSError) as exc:
                raise ConnectionError(f"Spa connection error: {exc}") from exc

    async def _send(self, message: Message) -> None:
        message.src = 0x0A
        raw = message.serialize()
        logger.debug("  send: %s", raw.hex())
        logger.info("to spa: %s", message)
        if self._is_serial:
            await self._send_queue.put(raw)
        else:
            self._writer.write(raw)
            await self._writer.drain()

    # ------------------------------------------------------------------
    # Configuration requests
    # ------------------------------------------------------------------

    async def request_configuration(self) -> None:
        await self._send(msgs.ConfigurationRequest())

    async def request_control_info(self) -> None:
        await self._send(msgs.ControlConfigurationRequest(1))

    async def request_control_info2(self) -> None:
        await self._send(msgs.ControlConfigurationRequest(2))

    async def request_filter_configuration(self) -> None:
        await self._send(msgs.ControlConfigurationRequest(3))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def toggle_item(self, item: str | int) -> None:
        await self._send(msgs.ToggleItem(item))

    async def toggle_pump(self, index: int) -> None:
        pump_items = ["pump1", "pump2", "pump3", "pump4", "pump5", "pump6"]
        await self.toggle_item(pump_items[index])

    async def toggle_light(self, index: int) -> None:
        await self.toggle_item(f"light{index + 1}")

    async def toggle_aux(self, index: int) -> None:
        await self.toggle_item(f"aux{index + 1}")

    async def toggle_mister(self) -> None:
        await self.toggle_item("mister")

    async def toggle_blower(self) -> None:
        await self.toggle_item("blower")

    async def toggle_hold(self) -> None:
        await self.toggle_item("hold")

    async def toggle_temperature_range(self) -> None:
        await self.toggle_item("temperature_range")

    async def toggle_heating_mode(self) -> None:
        await self.toggle_item("heating_mode")

    async def set_pump(self, index: int, desired: int | bool) -> None:
        if self.status is None or self.configuration is None:
            return

        if desired is False:
            desired = 0
        max_speed = self.configuration.pumps[index]
        if desired is True:
            desired = max_speed
        desired = min(int(desired), max_speed)

        # Single pump with multiple speeds: use soak command to turn off all at once
        active_pumps = [s for s in self.configuration.pumps if s != 0]
        if desired == 0 and len(active_pumps) == 1 and max_speed != 1:
            await self.toggle_item("soak")
            return

        current = min(self.status.pumps[index], max_speed)
        times = (desired - current) % (max_speed + 1)
        for i in range(times):
            await self.toggle_pump(index)
            if i < times - 1:
                await asyncio.sleep(0.1)

    async def set_light(self, index: int, desired: bool) -> None:
        if self.status is None:
            return
        if self.status.lights[index] != desired:
            await self.toggle_light(index)

    async def set_aux(self, index: int, desired: bool) -> None:
        if self.status is None:
            return
        if self.status.aux[index] != desired:
            await self.toggle_aux(index)

    async def set_mister(self, desired: bool) -> None:
        if self.status is None:
            return
        if self.status.mister != desired:
            await self.toggle_mister()

    async def set_blower(self, desired: int | bool) -> None:
        if self.status is None or self.configuration is None:
            return
        if desired is False:
            desired = 0
        max_speed = self.configuration.blower
        if desired is True:
            desired = max_speed
        desired = min(int(desired), max_speed)
        times = (desired - self.status.blower) % (max_speed + 1)
        for i in range(times):
            await self.toggle_blower()
            if i < times - 1:
                await asyncio.sleep(0.1)

    async def set_hold(self, desired: bool) -> None:
        if self.status is None:
            return
        if self.status.hold != desired:
            await self.toggle_hold()

    async def set_target_temperature(self, desired: float) -> None:
        if self.status is None:
            return
        if self.status.target_temperature == desired:
            return
        raw = desired
        if self.status.temperature_scale == "celsius" or desired < 50:
            raw = desired * 2
        await self._send(msgs.SetTargetTemperature(round(raw)))

    async def set_time(self, hour: int, minute: int, *, twenty_four_hour_time: bool = False) -> None:
        await self._send(msgs.SetTime(hour, minute, twenty_four_hour_time))

    async def set_temperature_scale(self, scale: str) -> None:
        if scale not in ("fahrenheit", "celsius"):
            raise ValueError(f"scale must be 'fahrenheit' or 'celsius', got {scale!r}")
        await self._send(msgs.SetTemperatureScale(scale))

    async def set_temperature_range(self, desired: str) -> None:
        if self.status is None:
            return
        if self.status.temperature_range != desired:
            await self.toggle_temperature_range()

    async def set_heating_mode(self, desired: str) -> None:
        if desired not in ("ready", "rest"):
            raise ValueError(f"heating_mode must be 'ready' or 'rest', got {desired!r}")
        if self.status is None:
            return
        current = self.status.heating_mode
        if current == desired:
            return
        if (current == "ready" and desired == "rest") or \
           (current == "rest" and desired == "ready") or \
           (current == "ready_in_rest" and desired == "rest"):
            times = 1
        elif current == "ready_in_rest" and desired == "ready":
            times = 2
        else:
            return
        for i in range(times):
            await self.toggle_heating_mode()
            if i < times - 1:
                await asyncio.sleep(0.1)

    async def update_filter_cycles(self, new_filter_cycles: msgs.FilterCycles) -> None:
        await self._send(new_filter_cycles)
        self.filter_cycles = new_filter_cycles
        await self.request_filter_configuration()

    # ------------------------------------------------------------------
    # Convenience properties (delegate to cached status)
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self.control_configuration.model if self.control_configuration else "Unknown"

    def __getattr__(self, name: str):
        # Delegate unknown attributes to status for convenience
        if name.startswith("_"):
            raise AttributeError(name)
        if self.status is not None and hasattr(self.status, name):
            return getattr(self.status, name)
        raise AttributeError(f"BWAClient has no attribute {name!r}")
