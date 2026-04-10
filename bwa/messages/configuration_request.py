from bwa.message import Message


class ConfigurationRequest(Message):
    """Sent by the client shortly after connecting to request spa configuration."""
    MESSAGE_TYPE = b"\xbf\x04"
    MESSAGE_LENGTH = 0

    def __repr__(self):
        return "<ConfigurationRequest>"
