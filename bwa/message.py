"""Base message class and frame parser/serializer for the Balboa protocol.

Frame format:
  0x7e  LENGTH  SRC  TYPE[0]  TYPE[1]  PAYLOAD...  CHECKSUM  0x7e

- LENGTH  = len(PAYLOAD) + 5  (covers SRC + TYPE×2 + LENGTH itself + CHECKSUM)
- CRC     = CRC-8 over [LENGTH, SRC, TYPE[0], TYPE[1], PAYLOAD...]
- 0x7e    = frame delimiter (start and end)
"""

from __future__ import annotations

import logging
from typing import Optional

from bwa.crc import crc8

logger = logging.getLogger("bwa.message")

# All concrete message subclasses register themselves here automatically.
_REGISTRY: list[type["Message"]] = []


class InvalidMessage(Exception):
    def __init__(self, msg: str, raw: bytes):
        super().__init__(msg)
        self.raw = raw


class Message:
    # Subclasses must define MESSAGE_TYPE as a 2-byte bytes literal, e.g. b"\xaf\x13"
    MESSAGE_TYPE: bytes = b""
    # MESSAGE_LENGTH: int or range; number of payload bytes expected.
    MESSAGE_LENGTH: int | range = 0

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "MESSAGE_TYPE") and cls.MESSAGE_TYPE:
            _REGISTRY.append(cls)

    def __init__(self):
        self.src: int = 0x0A  # default: client source address
        self.raw: bytes = b""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse_frame(cls, buf: bytes) -> tuple[Optional["Message"], int]:
        """Scan *buf* for a valid frame.

        Returns (message, bytes_consumed) on success, or (None, 0) if more
        data is needed, or raises InvalidMessage on a checksum/length error.
        """
        offset = 0
        while offset < len(buf):
            # Need at least 5 bytes for a minimal frame
            if len(buf) - offset < 5:
                return None, 0

            if buf[offset] != 0x7E:
                offset += 1
                continue

            length = buf[offset + 1]

            # Sanity-check length (must be ≥ 5, and < 0x7e to avoid ambiguity)
            if length < 5 or length >= 0x7E:
                offset += 1
                continue

            # Do we have the full frame?
            if len(buf) - offset < length + 2:
                return None, offset  # need more data; caller should keep buf[offset:]

            # Validate end delimiter
            if buf[offset + length + 1] != 0x7E:
                offset += 1
                continue

            # Validate checksum
            crc_data = buf[offset + 1: offset + length]      # [LENGTH, SRC, TYPE×2, PAYLOAD]
            expected_crc = crc8(crc_data)
            actual_crc = buf[offset + length]
            if expected_crc != actual_crc:
                offset += 1
                continue

            # Valid frame found — extract fields
            if offset > 0:
                logger.debug("Discarding %d bytes of invalid data before frame", offset)

            src = buf[offset + 2]
            msg_type = buf[offset + 3: offset + 5]
            payload = buf[offset + 5: offset + length]
            raw = buf[offset: offset + length + 2]
            bytes_consumed = offset + length + 2

            # Look up message class
            klass = next((k for k in _REGISTRY if k.MESSAGE_TYPE == msg_type), None)

            if klass is None:
                logger.info("Unrecognized message type %s: %s", msg_type.hex(), raw.hex())
                klass = UnrecognizedMessage
            else:
                expected = klass.MESSAGE_LENGTH
                payload_len = len(payload)
                valid = (
                    payload_len in expected
                    if isinstance(expected, range)
                    else payload_len == expected
                )
                if not valid:
                    raise InvalidMessage(
                        f"Bad payload length {payload_len} for {klass.__name__} "
                        f"(expected {expected})",
                        raw,
                    )

            instance = klass()
            instance.src = src
            instance.raw = raw
            instance._parse(payload)

            logger.debug("  read: %s", raw.hex())
            logger.debug("from spa: %s", instance)
            return instance, bytes_consumed

        # No start byte found at all — discard everything
        return None, len(buf)

    def _parse(self, payload: bytes) -> None:
        """Override in subclasses to decode the payload bytes."""

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _payload(self) -> bytes:
        """Override in subclasses to produce the payload bytes."""
        return b""

    def serialize(self) -> bytes:
        payload = self._payload()
        length = len(payload) + 5
        body = bytes([length, self.src]) + self.MESSAGE_TYPE + payload
        checksum = crc8(body)
        return bytes([0x7E]) + body + bytes([checksum, 0x7E])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_time(hour: int, minute: int, *, twenty_four_hour_time: bool = True) -> str:
        if twenty_four_hour_time:
            return f"{hour:02d}:{minute:02d}"
        display = hour % 12 or 12
        suffix = "PM" if hour >= 12 else "AM"
        return f"{display}:{minute:02d}{suffix}"

    @staticmethod
    def format_duration(total_minutes: int) -> str:
        return f"{total_minutes // 60}:{total_minutes % 60:02d}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} raw={self.raw.hex()}>"


class UnrecognizedMessage(Message):
    MESSAGE_TYPE = b""  # never registered (empty type)

    def __init_subclass__(cls, **kwargs):
        pass  # prevent auto-registration of UnrecognizedMessage itself
