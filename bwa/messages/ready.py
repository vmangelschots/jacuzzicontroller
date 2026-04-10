from bwa.message import Message


class Ready(Message):
    """RS-485 only: signals that a message can be sent onto the bus immediately."""
    MESSAGE_TYPE = b"\xbf\x06"
    MESSAGE_LENGTH = 0

    def __repr__(self):
        return "<Ready>"
