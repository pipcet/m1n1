#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import sys, pathlib
import time
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

import struct
from construct import *

from m1n1.setup import *
from m1n1.shell import run_shell
from m1n1 import asm
from m1n1.hw.dart import DART, DARTRegs
from m1n1.fw.dcp.client import DCPClient
from m1n1.fw.dcp.manager import DCPManager
from m1n1.fw.dcp.ipc import ByRef
from m1n1.proxyutils import RegMonitor

dart_addr = u.adt["arm-io/dart-dcp"].get_reg(0)[0]
dart = DART(iface, DARTRegs(u, dart_addr), u)

disp_dart_addr = u.adt["arm-io/dart-disp0"].get_reg(0)[0]
disp_dart = DART(iface, DARTRegs(u, disp_dart_addr), u)

dcp_addr = u.adt["arm-io/dcp"].get_reg(0)[0]
dcp = DCPClient(u, dcp_addr, dart, disp_dart)

dcp.start()
dcp.start_ep(0x37)
dcp.dcpep.initialize()

mgr = DCPManager(dcp.dcpep)

mgr.start_signal()

mgr.set_digital_out_mode(0x59, 0x43)

swapid = ByRef(0)

def start():
    # arg: IOUserClient
    ret = mgr.swap_start(swapid, {
        "addr": 0,
        "unk": 0,
        "flag1": 0,
        "flag2": 0
    })
    assert ret == 0
    print(f"swap ID: {swapid.val:#x}")

start()

surface_id = 3

swap_rec = Container(
    flags1 = 0,
    flags2 = 0,
    swap_id = swapid.val,
    surf_ids = [surface_id, surface_id, surface_id],
    src_rect = [[0, 0, 3840, 2160],[0,0, 3840,2160],[0,0,3840,2160]],
    surf_flags = [1, 1, 1],
    surf_unk = [1, 1, 1],
    dst_rect = [[0, 0, 3840, 2160],[0,0, 3840,2160],[0,0,3840,2160]],
    swap_enabled = 0x3,
    swap_completed = 0x3,
)

surf = Container(
    is_tiled = False,
    unk_1 = False,
    unk_2 = False,
    plane_cnt = 0,
    plane_cnt2 = 0,
    format = "RGBA",
    unk_13 = 13,
    unk_14 = 1,
    stride = 3840 * 4,
    pix_size = 4,
    pel_w = 1,
    pel_h = 1,
    offset = 0,
    width = 3840,
    height = 2160,
    buf_size = 1920 * 1080 * 4 * 4,
    surface_id = surface_id,
    has_comp = True,
    has_planes = True,
    has_compr_info = False,
    unk_1f5 = 0,
    unk_1f9 = 0,
)

iova = 0x420000

outB = ByRef(False)

swaps = mgr.swaps

buf = u.ba.mem_size - (32<<20) + 0x800000000

iface.writemem(buf, bytes([0xFF] * 3840*2160*4))

disp_dart.iomap_at(0, iova, buf, 32<<20)

def submit():
    start()
    swap_rec.swap_id = swapid.val
    ret = mgr.swap_submit_dcp(swap_rec=swap_rec, surf0=surf, surf1=surf, surf2=surf, surfInfo=[iova, iova, iova],
                            unkBool=False, unkFloat=0.0, unkInt=0, unkOutBool=outB)
    print(f"swap returned {ret} / {outB}")

    dcp.work()

    if ret == 0:
        while swaps == mgr.swaps:
            dcp.work()
        print("swap complete!")

mgr.set_digital_out_mode(0x59, 0x43)

submit()
mgr.set_digital_out_mode(0x59, 0x43)

run_shell(globals(), msg="Have fun!")
