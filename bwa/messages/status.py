from __future__ import annotations
from bwa.message import Message

NOTIFICATIONS: dict[int, str | None] = {
    0x00: None,
    0x04: "filter",
    0x09: "sanitizer",
    0x0A: "ph",
}
_NOTIFICATIONS_REVERSE: dict[str, int] = {v: k for k, v in NOTIFICATIONS.items() if v}

HEATING_MODES = ("ready", "rest", "ready_in_rest")


class Status(Message):
    """Spa status update — sent by the spa approximately once per second."""
    MESSAGE_TYPE = b"\xaf\x13"
    # later firmware versions may add bytes at the end
    MESSAGE_LENGTH = range(23, 33)

    def __init__(self):
        super().__init__()
        self.src = 0xFF

        self.hold: bool = False
        self.priming: bool = False
        self.notification: str | None = None
        self.heating_mode: str = "ready"      # "ready" | "rest" | "ready_in_rest"
        self._temperature_scale: str = "fahrenheit"
        self.twenty_four_hour_time: bool = False
        self.filter_cycles: list[bool] = [False, False]
        self.heating: bool = False
        self.temperature_range: str = "high"  # "high" | "low"
        self.hour: int = 0
        self.minute: int = 0
        self.circulation_pump: bool = False
        self.blower: int = 0
        self.pumps: list[int] = [0] * 6
        self.lights: list[bool] = [False, False]
        self.mister: bool = False
        self.aux: list[bool] = [False, False]
        self.current_temperature: float | None = None
        self.target_temperature: float = 100.0

    # ------------------------------------------------------------------
    # temperature_scale property — converts stored temperatures on change
    # ------------------------------------------------------------------

    @property
    def temperature_scale(self) -> str:
        return self._temperature_scale

    @temperature_scale.setter
    def temperature_scale(self, value: str) -> None:
        if value == self._temperature_scale:
            return
        if value == "fahrenheit":
            if self.current_temperature is not None:
                t = self.current_temperature * 9.0 / 5 + 32
                self.current_temperature = round(t)
            self.target_temperature = round(self.target_temperature * 9.0 / 5 + 32)
        else:  # celsius
            if self.current_temperature is not None:
                t = (self.current_temperature - 32) * 5.0 / 9
                self.current_temperature = round(t * 2) / 2.0
            t = (self.target_temperature - 32) * 5.0 / 9
            self.target_temperature = round(t * 2) / 2.0
        self._temperature_scale = value

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, payload: bytes) -> None:
        # byte 0 — flags 0
        f0 = payload[0]
        self.hold = bool(f0 & 0x05)

        # byte 1 — priming / notification flag
        self.priming = payload[1] == 0x01
        if payload[1] == 0x03:
            self.notification = NOTIFICATIONS.get(payload[6], None) if len(payload) > 6 else None
        else:
            self.notification = None

        # byte 2 — current temperature (0xff = unknown)
        raw_current = payload[2]
        raw_current_val: float | None = None if raw_current == 0xFF else float(raw_current)

        # bytes 3-4 — time
        self.hour = payload[3]
        self.minute = payload[4]

        # byte 5 — flags 2 (heating mode)
        f2 = payload[5]
        mode_index = f2 & 0x03
        self.heating_mode = HEATING_MODES[mode_index] if mode_index < len(HEATING_MODES) else "ready"

        # byte 9 — flags 3 (scale, 24h, filter mode)
        f3 = payload[9]
        new_scale = "celsius" if (f3 & 0x01) else "fahrenheit"
        self.twenty_four_hour_time = bool(f3 & 0x02)
        self.filter_cycles[0] = bool(f3 & 0x04)
        self.filter_cycles[1] = bool(f3 & 0x08)

        # byte 10 — flags 4 (heating, temperature range)
        f4 = payload[10]
        self.heating = bool(f4 & 0x30)
        self.temperature_range = "high" if (f4 & 0x04) else "low"

        # byte 11-12 — pump status
        f5 = payload[11]
        self.pumps[0] = f5 & 0x03
        self.pumps[1] = (f5 >> 2) & 0x03
        self.pumps[2] = (f5 >> 4) & 0x03
        self.pumps[3] = (f5 >> 6) & 0x03
        if len(payload) > 12:
            f6 = payload[12]
            self.pumps[4] = f6 & 0x03
            self.pumps[5] = (f6 >> 2) & 0x03

        # byte 13 — circulation pump + blower
        if len(payload) > 13:
            f7 = payload[13]
            self.circulation_pump = bool(f7 & 0x02)
            self.blower = (f7 >> 2) & 0x03

        # byte 14 — lights
        if len(payload) > 14:
            f8 = payload[14]
            self.lights[0] = bool(f8 & 0x03)
            self.lights[1] = bool((f8 >> 2) & 0x03)

        # byte 15 — mister + aux
        if len(payload) > 15:
            f9 = payload[15]
            self.mister = bool(f9 & 0x01)
            self.aux[0] = bool(f9 & 0x08)
            self.aux[1] = bool(f9 & 0x10)

        # byte 20 — set temperature
        raw_target = payload[20] if len(payload) > 20 else 100

        # Apply temperature scale (do this last, after we know the new scale)
        self._temperature_scale = new_scale  # set without conversion
        if new_scale == "celsius":
            self.current_temperature = raw_current_val / 2.0 if raw_current_val is not None else None
            self.target_temperature = raw_target / 2.0
        else:
            self.current_temperature = raw_current_val
            self.target_temperature = float(raw_target)

    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        cur = "--" if self.current_temperature is None else self.current_temperature
        scale_char = "C" if self._temperature_scale == "celsius" else "F"
        parts = [f"{cur}/{self.target_temperature}°{scale_char}"]
        parts.append(self.format_time(self.hour, self.minute, twenty_four_hour_time=self.twenty_four_hour_time))
        parts.append(self.heating_mode)
        if self.heating:
            parts.append("heating")
        parts.append(f"range={self.temperature_range}")
        if self.hold:
            parts.append("hold")
        if self.priming:
            parts.append("priming")
        parts.append(f"pumps={self.pumps}")
        parts.append(f"lights={self.lights}")
        if self.circulation_pump:
            parts.append("circ")
        if self.blower:
            parts.append(f"blower={self.blower}")
        if self.mister:
            parts.append("mister")
        if self.notification:
            parts.append(f"notification={self.notification}")
        return f"<Status {' '.join(parts)}>"
