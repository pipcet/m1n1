#!/usr/bin/env python3

import argparse, pathlib, time

parser = argparse.ArgumentParser(description='Mach-O loader for m1n1')
parser.add_argument('payload', type=pathlib.Path)
parser.add_argument('-s', '--sepfw', action="store_true")
parser.add_argument('-c', '--call', action="store_true")
args = parser.parse_args()

from setup import *
from tgtypes import BootArgs
from macho import MachO
import asm

macho = MachO(args.payload.read_bytes())

image = macho.prepare_image()

new_base = u.base

entry = macho.entry
entry -= macho.vmin
entry += new_base

if args.sepfw:
    sepfw_start, sepfw_length = u.adt["chosen"]["memory-map"].SEPFW
    tc_start, tc_length = u.adt["chosen"]["memory-map"].TrustCache
else:
    sepfw_start, sepfw_length = 0, 0

image_size = align(len(image))
sepfw_off = image_size
image_size += align(sepfw_length)
tc_off = image_size
image_size += align(tc_length)
bootargs_off = image_size
image_size += 0x4000

print(f"Total region size: 0x{image_size:x} bytes")
image_addr = u.malloc(image_size)

print(f"Loading kernel image (0x{len(image):x} bytes)...")
u.compressed_writemem(image_addr, image, True)
p.dc_cvau(image_addr, len(image))

if args.sepfw:
    print(f"Copying SEPFW (0x{sepfw_length:x} bytes)...")
    p.memcpy8(image_addr + sepfw_off, sepfw_start, sepfw_length)
    print(f"Adjusting addresses in ADT...")
    u.adt["chosen"]["memory-map"].SEPFW = (new_base + sepfw_off, sepfw_length)
    u.push_adt()
    print(f"Copying TrustCache (0x{tc_length:x} bytes) from 0x{tc_start:x}...")
    p.memcpy8(image_addr + tc_off, tc_start, tc_length)
    print(f"Adjusting addresses in ADT...")
    u.adt["chosen"]["memory-map"].TrustCache = (new_base + tc_off, tc_length)

del u.adt["cpus"]["cpu1"]
del u.adt["cpus"]["cpu2"]
del u.adt["cpus"]["cpu3"]
del u.adt["cpus"]["cpu4"]
del u.adt["cpus"]["cpu5"]
del u.adt["cpus"]["cpu6"]
del u.adt["cpus"]["cpu7"]
u.adt["chosen"]["memory-map"].BootArgs = (new_base + bootargs_off, 0x4000)
u.adt["chosen"]["memory-map"].DeviceTree = (u.ba.devtree, u.ba.devtree_size)
u.push_adt()
print(f"Setting up bootargs...")
tba = u.ba.copy()

if args.sepfw:
    tba.top_of_kernel_data = new_base + image_size
else:
    # SEP firmware is in here somewhere, keep top_of_kdata high so we hopefully don't clobber it
    tba.top_of_kernel_data = max(tba.top_of_kernel_data, new_base + image_size)
tba.top_of_kernel_data = new_base + image_size

iface.writemem(image_addr + bootargs_off, BootArgs.build(tba))

print(f"Copying stub...")

stub = asm.ARMAsm(f"""
1:
        ldp x4, x5, [x1], #8
        stp x4, x5, [x2]
        dc cvau, x2
        ic ivau, x2
        add x2, x2, #8
        sub x3, x3, #8
        cbnz x3, 1b

        ldr x1, ={entry}
        br x1
""", image_addr + image_size)

iface.writemem(stub.addr, stub.data)
p.dc_cvau(stub.addr, stub.len)
p.ic_ivau(stub.addr, stub.len)

print(f"Entry point: 0x{entry:x}")

if args.call:
    print(f"Shutting down MMU...")
    try:
        p.mmu_shutdown()
    except ProxyCommandError:
        pass
    print(f"Jumping to stub at 0x{stub.addr:x}")
    p.call(stub.addr, new_base + bootargs_off, image_addr, new_base, image_size, reboot=True)
else:
    print(f"Rebooting into stub at 0x{stub.addr:x}")
    p.reboot(stub.addr, new_base + bootargs_off, image_addr, new_base, image_size)

time.sleep(1)
iface.nop()
print("Proxy is alive again")
