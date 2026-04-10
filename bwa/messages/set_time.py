from bwa.message import Message


class SetTime(Message):
    """Set the spa's clock.

    The high bit of the hour byte enables 24-hour display mode.
    """
    MESSAGE_TYPE = b"\xbf\x21"
    MESSAGE_LENGTH = 2

    def __init__(self, hour: int = 0, minute: int = 0, twenty_four_hour_time: bool = False):
        super().__init__()
        self.hour = hour
        self.minute = minute
        self.twenty_four_hour_time = twenty_four_hour_time

    def _parse(self, payload: bytes) -> None:
        self.twenty_four_hour_time = bool(payload[0] & 0x80)
        self.hour = payload[0] & 0x7F
        self.minute = payload[1]

    def _payload(self) -> bytes:
        hour_byte = self.hour | (0x80 if self.twenty_four_hour_time else 0x00)
        return bytes([hour_byte, self.minute])

    def __repr__(self):
        return f"<SetTime {self.format_time(self.hour, self.minute, twenty_four_hour_time=self.twenty_four_hour_time)}>"
