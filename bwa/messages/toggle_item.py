from bwa.message import Message

ITEMS: dict[str, int] = {
    "normal_operation": 0x01,
    "clear_notification": 0x03,
    "pump1": 0x04,
    "pump2": 0x05,
    "pump3": 0x06,
    "pump4": 0x07,
    "pump5": 0x08,
    "pump6": 0x09,
    "blower": 0x0C,
    "mister": 0x0E,
    "light1": 0x11,
    "light2": 0x12,
    "aux1": 0x16,
    "aux2": 0x17,
    "soak": 0x1D,
    "hold": 0x3C,
    "temperature_range": 0x50,
    "heating_mode": 0x51,
}
_ITEMS_REVERSE: dict[int, str] = {v: k for k, v in ITEMS.items()}


class ToggleItem(Message):
    """Toggle (cycle) a spa component."""
    MESSAGE_TYPE = b"\xbf\x11"
    MESSAGE_LENGTH = 2

    def __init__(self, item: str | int | None = None):
        super().__init__()
        self.item: str | int | None = item

    def _parse(self, payload: bytes) -> None:
        code = payload[0]
        self.item = _ITEMS_REVERSE.get(code, code)

    def _payload(self) -> bytes:
        if isinstance(self.item, int):
            code = self.item
        else:
            code = ITEMS[self.item]
        return bytes([code, 0x00])

    def __repr__(self):
        return f"<ToggleItem {self.item}>"
