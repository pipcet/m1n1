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

mon = RegMonitor(u)

#mon.add(0x230000000, 0x18000)
#mon.add(0x230018000, 0x4000)
#mon.add(0x230068000, 0x8000)
#mon.add(0x2300b0000, 0x8000)
#mon.add(0x2300f0000, 0x4000)
#mon.add(0x230100000, 0x10000)
#mon.add(0x230170000, 0x10000)
#mon.add(0x230180000, 0x1c000)
#mon.add(0x2301a0000, 0x10000)
#mon.add(0x2301d0000, 0x4000)
#mon.add(0x230230000, 0x10000)
#mon.add(0x23038c000, 0x10000)
#mon.add(0x230800000, 0x10000)
#mon.add(0x230840000, 0xc000)
#mon.add(0x230850000, 0x2000)
##mon.add(0x230852000, 0x5000) # big curve / gamma table
#mon.add(0x230858000, 0x18000)
#mon.add(0x230870000, 0x4000)
#mon.add(0x230880000, 0x8000)
#mon.add(0x230894000, 0x4000)
#mon.add(0x2308a8000, 0x8000)
#mon.add(0x2308b0000, 0x8000)
#mon.add(0x2308f0000, 0x4000)
##mon.add(0x2308fc000, 0x4000) # stats / RGB color histogram
#mon.add(0x230900000, 0x10000)
#mon.add(0x230970000, 0x10000)
#mon.add(0x230980000, 0x10000)
#mon.add(0x2309a0000, 0x10000)
#mon.add(0x2309d0000, 0x4000)
#mon.add(0x230a30000, 0x20000)
#mon.add(0x230b8c000, 0x10000)
#mon.add(0x231100000, 0x8000)
#mon.add(0x231180000, 0x4000)
#mon.add(0x2311bc000, 0x10000)
#mon.add(0x231300000, 0x8000)
##mon.add(0x23130c000, 0x4000) # - DCP dart
#mon.add(0x231310000, 0x8000)
#mon.add(0x231340000, 0x8000)
##mon.add(0x231800000, 0x8000) # breaks DCP
##mon.add(0x231840000, 0x8000) # breaks DCP
##mon.add(0x231850000, 0x8000) # something DCP?
##mon.add(0x231920000, 0x8000) # breaks DCP
##mon.add(0x231960000, 0x8000) # breaks DCP
##mon.add(0x231970000, 0x10000) # breaks DCP
##mon.add(0x231c00000, 0x10000) # DCP mailbox

mon.add(0x230845840, 0x40) # error regs

mon.poll()

dart_addr = u.adt["arm-io/dart-dcp"].get_reg(0)[0]
dart = DART(iface, DARTRegs(u, dart_addr), u)

disp_dart_addr = u.adt["arm-io/dart-disp0"].get_reg(0)[0]
disp_dart = DART(iface, DARTRegs(u, disp_dart_addr), u)

print("DCP DART:")
dart.regs.dump_regs()
print("DISP DART:")
disp_dart.regs.dump_regs()

dcp_addr = u.adt["arm-io/dcp"].get_reg(0)[0]
dcp = DCPClient(u, dcp_addr, dart, disp_dart)

dcp.start()
dcp.start_ep(0x37)
dcp.dcpep.initialize()

mgr = DCPManager(dcp.dcpep)

mon.poll()

mgr.start_signal()

mon.poll()

assert mgr.set_display_device(2) == 0
mgr.set_digital_out_mode(0x69, 0x45)

mon.poll()

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
    src_rect = [[0, 0, 1920, 1080],[0,0,1920,1080],[0,0,960,540]],
    surf_flags = [1, 1, 1],
    surf_unk = [1, 1, 1],
    dst_rect = [[0, 0, 1920, 1080],[0, 0, 960, 540],[0,0,480,270]],
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
    stride = 1920 * 4,
    pix_size = 4,
    pel_w = 1,
    pel_h = 1,
    offset = 0,
    width = 1920,
    height = 1080,
    buf_size = 1920 * 1080 * 4,
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

mon.poll()

buf = u.memalign(0x4000, 32<<20)

iface.writemem(buf, open("asahi.bin", "rb").read())

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

submit()
run_shell(globals(), msg="Have fun!")
