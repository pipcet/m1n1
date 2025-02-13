# SPDX-License-Identifier: MIT
from .base import *
from ...utils import *

class KDebugMessage(Register64):
    TYPE    = 55, 48

class KDebugGetBufMessage(KDebugMessage):
    TYPE    = 55, 48, Constant(1)
    COUNT   = 47, 0

class KDebugSendBufMessage(KDebugMessage):
    TYPE    = 55, 48
    DVA     = 47, 0

class KDebugStart(KDebugMessage):
    TYPE    = 55, 48, Constant(8)

class ASCKDebugEndpoint(ASCBaseEndpoint):
    SHORT = "kdebug"
    BASE_MESSAGE = KDebugMessage

    @msg_handler(1, KDebugGetBufMessage)
    def GetBuf(self, msg):
        size = align_up(msg.COUNT * 0x20, 0x4000)
        self.iobuffer0, self.iobuffer0_iova = self.asc.ioalloc(size)
        self.send(KDebugSendBufMessage(TYPE=1, DVA=self.iobuffer0_iova))

        self.iobuffer1, self.iobuffer1_iova = self.asc.ioalloc(0x2000)
        self.send(KDebugSendBufMessage(TYPE=2, DVA=self.iobuffer1_iova))
        return True

    def start(self):
        self.iobuffer0 = None
        self.iobuffer1 = None
        self.iobuffer0_iova = None
        self.iobuffer1_iova = None
        self.send(KDebugStart())
