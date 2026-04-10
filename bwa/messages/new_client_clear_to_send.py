from bwa.message import Message


class NewClientClearToSend(Message):
    MESSAGE_TYPE = b"\xbf\x00"
    MESSAGE_LENGTH = 0

    def __repr__(self):
        return "<NewClientClearToSend>"
