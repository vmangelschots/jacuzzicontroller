from bwa.message import Message


class SetTemperatureScale(Message):
    """Switch between Fahrenheit and Celsius."""
    MESSAGE_TYPE = b"\xbf\x27"
    MESSAGE_LENGTH = 2

    def __init__(self, scale: str = "fahrenheit"):
        super().__init__()
        self.scale = scale  # "fahrenheit" or "celsius"

    def _parse(self, payload: bytes) -> None:
        self.scale = "celsius" if payload[1] else "fahrenheit"

    def _payload(self) -> bytes:
        return bytes([0x01, 0x01 if self.scale == "celsius" else 0x00])

    def __repr__(self):
        return f"<SetTemperatureScale {self.scale}>"
