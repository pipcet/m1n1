#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import sys, pathlib
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

print("DCP DART:")
dart.regs.dump_regs()
print("DISP DART:")
disp_dart.regs.dump_regs()

dcp_addr = u.adt["arm-io/dcp"].get_reg(0)[0]
dcp = DCPClient(u, dcp_addr, dart, disp_dart)

dcp.start()
dcp.start_ep(0x37)
dcp.dcpep.initialize()

qbuf = u.memalign(0x4000, 16 << 20)
mgr = DCPManager(dcp.dcpep, qbuf)

disp_dart.iomap_at(0, 0x8000000, qbuf, 16<<20)
disp_dart.iomap_at(4, 0x8000000, qbuf, 16<<20)
dart.iomap_at(0, 0x8000000, qbuf, 16<<20)

mgr.start_signal()

# import m1n1
# 
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A442', bytes([1,0,0,0]), 512)
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A103', bytes([0,0,0,0,0,0,0,0,0,0,0,0]), 512)
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A029', bytes([0,0,0,0,0,0,0,0,0,0,0,0]), 512)
# 
# mgr.apply_property(74, 1)
# 
# mgr.get_color_remap_mode(6)
# mgr.enable_disable_video_power_savings(0)
# 
# mgr.update_notify_clients_dcp([0,0,0,0,0,0,0,0,0,0,0,0,0])
# mgr.update_notify_clients_dcp([0,0,0,0,0,0,1,0,0,0,0,0,0])
# mgr.update_notify_clients_dcp([0,0,0,0,0,0,1,1,0,0,0,0,0])
# mgr.update_notify_clients_dcp([0,0,0,0,0,0,1,1,1,0,0,0,0])
# mgr.update_notify_clients_dcp([0,0,0,0,0,0,1,1,1,0,1,0,0])
# mgr.first_client_open(1,1,1,1,1,1,1,1,1)
# mgr.setPowerState(1, False, ByRef(0))
# 
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A459', bytes([1,0,0,0]), 512)
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A462', bytes([1,0,0,0]), 512)
# dcp.dcpep.ch_cmd.call(m1n1.fw.dcp.dcpep.CallContext.CMD, 'A446', bytes([0,0,0,0]), 512)
# write32(0x230850000 + 48, 0)

table_23087 = [0] * 65536
for i in range(0, 16384, 4):
    table_23087[i] = read32(0x230870000 + i)

table_23086 = [0] * 65536
for i in range(0, 65536, 4):
    table_23086[i] = read32(0x230860000 + i)

table_23085 = [0] * 65536
for i in range(0, 65536, 4):
    table_23085[i] = read32(0x230850000 + i)

mgr.set_digital_out_mode(0x59, 0x12)
mgr.set_contrast(0)
mgr.setBrightnessCorrection(4 * 65536)
mgr.set_display_device(2)
mgr.set_digital_out_mode(0x59, 0x13)
mgr.set_contrast(0)
mgr.setBrightnessCorrection(4 * 65536)
mgr.set_display_device(2)
mgr.set_digital_out_mode(0x59, 0x13)
mgr.set_contrast(0)
mgr.setBrightnessCorrection(4 * 65536)

mgr.A425(1,0,0,0,0,0,0,0)
mgr.A425(1,0,0,0,0,0,0,0)
mgr.A425(1,0,0,0,0,0,0,0)
mgr.A425(1,0,0,0,0,0,0,0)

time.sleep(2)

a0 = ByRef(Container())
mgr.get_dfb_info(a0)

write32(0x230850030, 0)
for r in [range(65532, 48, -4), [36]]:
    for i in r:
        print(f"{0x230850000 + i:x}: {table_23085[i]:x}")
        write32(0x230850000 + i, table_23085[i])

write32(0x230850000 + 4, 0)
write32(0x230850000 + 4, 0x80070001)
write32(0x230850000 + 48, 0x5220)
write32(0x230850000 + 44, 0x20002)
write32(0x230850000 + 40, 0x20002)
write32(0x230850000 + 32, 0x8)
write32(0x230850000 + 28, 0xa)
write32(0x230850000 + 24, 0xa)
write32(0x230850000 + 20, 0xa)
write32(0x230850000 + 4100, 0x80070001)
write32(0x230850000 + 4104, 0x5220)
write32(0x230850000 + 4120, 0x423c000)
write32(0x230850000 + 4124, 0x4a28000)

mgr.get_dfb_info(a0)

for i in range(0, 65536, 4):
    write32(0x230860000 + i, table_23086[i])

for i in range(0, 16384, 4):
    write32(0x230870000 + i, table_23087[i])

mgr.set_contrast(100.0)
mgr.setBrightnessCorrection(4 * 65536)

pass
