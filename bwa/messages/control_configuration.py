from bwa.message import Message


class ControlConfiguration(Message):
    """Response to ControlConfigurationRequest type=1.

    Provides the spa model name and firmware version.
    """
    MESSAGE_TYPE = b"\xbf\x24"
    MESSAGE_LENGTH = 21

    def __init__(self):
        super().__init__()
        self.model: str = ""
        self.version: str = ""

    def _parse(self, payload: bytes) -> None:
        self.version = f"V{payload[2]}.{payload[3]}"
        self.model = payload[4:12].decode("ascii", errors="replace").strip()

    def __repr__(self):
        return f"<ControlConfiguration model={self.model!r} version={self.version}>"


class ControlConfiguration2(Message):
    """Response to ControlConfigurationRequest type=2 (panel/accessories config).

    Tells us which accessories are installed: number of pumps and their speeds,
    lights, blower, circulation pump, mister, aux outputs.
    """
    MESSAGE_TYPE = b"\xbf\x2e"
    MESSAGE_LENGTH = 6

    def __init__(self):
        super().__init__()
        self.pumps: list[int] = [0] * 6   # max speed per pump (0 = not present)
        self.lights: list[bool] = [False, False]
        self.circulation_pump: bool = False
        self.blower: int = 0               # 0=none, 1=on/off, 2+=multi-speed
        self.mister: bool = False
        self.aux: list[bool] = [False, False]

    def _parse(self, payload: bytes) -> None:
        f0 = payload[0]
        self.pumps[0] = f0 & 0x03
        self.pumps[1] = (f0 >> 2) & 0x03
        self.pumps[2] = (f0 >> 4) & 0x03
        self.pumps[3] = (f0 >> 6) & 0x03
        f1 = payload[1]
        self.pumps[4] = f1 & 0x03
        self.pumps[5] = (f1 >> 6) & 0x03
        f2 = payload[2]
        self.lights[0] = bool(f2 & 0x03)
        self.lights[1] = bool((f2 >> 6) & 0x03)
        f3 = payload[3]
        self.blower = f3 & 0x03
        self.circulation_pump = bool((f3 >> 6) & 0x03)
        f4 = payload[4]
        self.mister = bool(f4 & 0x30)
        self.aux[0] = bool(f4 & 0x01)
        self.aux[1] = bool(f4 & 0x02)

    def __repr__(self):
        parts = [f"pumps={self.pumps}"]
        parts.append(f"lights={self.lights}")
        if self.circulation_pump:
            parts.append("circulation_pump")
        if self.blower:
            parts.append(f"blower={self.blower}")
        if self.mister:
            parts.append("mister")
        parts.append(f"aux={self.aux}")
        return f"<ControlConfiguration2 {' '.join(parts)}>"
