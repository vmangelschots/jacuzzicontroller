from bwa.message import Message


class Error(Message):
    MESSAGE_TYPE = b"\xbf\xe1"
    MESSAGE_LENGTH = 1

    def __repr__(self):
        return f"<Error raw={self.raw.hex()}>"
