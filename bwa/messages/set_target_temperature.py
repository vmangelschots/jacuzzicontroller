from bwa.message import Message


class SetTargetTemperature(Message):
    """Set the spa target temperature.

    The value is the raw integer sent on the wire:
    - Fahrenheit: the actual °F value (80–104 high range, 50–80 low range)
    - Celsius:    the value multiplied by 2 (caller is responsible for doubling)
    """
    MESSAGE_TYPE = b"\xbf\x20"
    MESSAGE_LENGTH = 1

    def __init__(self, temperature: int = 0):
        super().__init__()
        self.temperature = temperature

    def _parse(self, payload: bytes) -> None:
        self.temperature = payload[0]

    def _payload(self) -> bytes:
        return bytes([self.temperature & 0xFF])

    def __repr__(self):
        return f"<SetTargetTemperature {self.temperature}°>"
