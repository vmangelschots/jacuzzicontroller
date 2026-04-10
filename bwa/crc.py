"""CRC-8 checksum matching the Balboa protocol.

Polynomial: 0x07 (CRC-8/SMBUS)
Initial value: 0x02
Final XOR: 0x02
"""


def crc8(data: bytes) -> int:
    crc = 0x02  # INIT_CRC
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0x02  # XOR_MASK
