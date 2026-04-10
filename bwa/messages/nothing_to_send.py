from bwa.message import Message


class NothingToSend(Message):
    MESSAGE_TYPE = b"\xbf\x07"
    MESSAGE_LENGTH = 0

    def __repr__(self):
        return "<NothingToSend>"
