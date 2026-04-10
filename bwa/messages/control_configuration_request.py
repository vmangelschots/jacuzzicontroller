from bwa.message import Message


class ControlConfigurationRequest(Message):
    """Settings request sent to the spa.

    type=1 → Information request  (payload 02 00 00)
    type=2 → Panel request        (payload 00 00 01)
    type=3 → Filter cycles request(payload 01 00 00)
    """
    MESSAGE_TYPE = b"\xbf\x22"
    MESSAGE_LENGTH = 3

    _PAYLOADS = {
        1: b"\x02\x00\x00",
        2: b"\x00\x00\x01",
        3: b"\x01\x00\x00",
    }
    _REVERSE = {v: k for k, v in _PAYLOADS.items()}

    def __init__(self, request_type: int = 1):
        super().__init__()
        self.request_type = request_type

    def _parse(self, payload: bytes) -> None:
        self.request_type = self._REVERSE.get(payload, 0)

    def _payload(self) -> bytes:
        return self._PAYLOADS.get(self.request_type, b"\x00\x00\x00")

    def __repr__(self):
        return f"<ControlConfigurationRequest type={self.request_type}>"
