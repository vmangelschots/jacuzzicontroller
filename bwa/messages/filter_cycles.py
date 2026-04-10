from bwa.message import Message


class FilterCycles(Message):
    """Filter cycle schedule (sent by spa, and sent back to update it)."""
    MESSAGE_TYPE = b"\xbf\x23"
    MESSAGE_LENGTH = 8

    def __init__(self):
        super().__init__()
        self.cycle1_start_hour: int = 0
        self.cycle1_start_minute: int = 0
        self.cycle1_duration: int = 0    # total minutes
        self.cycle2_enabled: bool = False
        self.cycle2_start_hour: int = 0
        self.cycle2_start_minute: int = 0
        self.cycle2_duration: int = 0    # total minutes

    def _parse(self, payload: bytes) -> None:
        self.cycle1_start_hour = payload[0]
        self.cycle1_start_minute = payload[1]
        self.cycle1_duration = payload[2] * 60 + payload[3]

        c2h = payload[4]
        self.cycle2_enabled = bool(c2h & 0x80)
        self.cycle2_start_hour = c2h & 0x7F
        self.cycle2_start_minute = payload[5]
        self.cycle2_duration = payload[6] * 60 + payload[7]

    def _payload(self) -> bytes:
        c1h = self.cycle1_duration // 60
        c1m = self.cycle1_duration % 60
        c2h_byte = self.cycle2_start_hour | (0x80 if self.cycle2_enabled else 0x00)
        c2h = self.cycle2_duration // 60
        c2m = self.cycle2_duration % 60
        return bytes([
            self.cycle1_start_hour,
            self.cycle1_start_minute,
            c1h, c1m,
            c2h_byte,
            self.cycle2_start_minute,
            c2h, c2m,
        ])

    def __repr__(self):
        c1 = self.format_duration(self.cycle1_duration)
        c1t = self.format_time(self.cycle1_start_hour, self.cycle1_start_minute)
        c2 = self.format_duration(self.cycle2_duration)
        c2t = self.format_time(self.cycle2_start_hour, self.cycle2_start_minute)
        enabled = "enabled" if self.cycle2_enabled else "disabled"
        return f"<FilterCycles cycle1 {c1}@{c1t} cycle2({enabled}) {c2}@{c2t}>"
