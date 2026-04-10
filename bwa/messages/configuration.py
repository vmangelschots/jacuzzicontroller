from bwa.message import Message


class Configuration(Message):
    """Response to a ConfigurationRequest (0x0a 0xbf 0x94).

    Currently treated as an acknowledgement only; raw bytes are preserved
    but not decoded further.
    """
    MESSAGE_TYPE = b"\xbf\x94"
    MESSAGE_LENGTH = 25

    def __repr__(self):
        return "<Configuration>"
