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

if args.sepfw:
    p.smp_start_secondaries()
macho = MachO(args.payload.read_bytes())

image = macho.prepare_image()

new_base = u.base

entry = macho.entry
entry -= macho.vmin
entry += new_base

image_size = align(len(image))
if args.sepfw:
    sepfw_start, sepfw_length = u.adt["chosen"]["memory-map"].SEPFW
    tc_start, tc_length = u.adt["chosen"]["memory-map"].TrustCache
    sepfw_off = image_size
    image_size += align(sepfw_length)
    tc_off = image_size
    image_size += align(tc_length)
else:
    sepfw_start, sepfw_length = 0, 0

bootargs_off = image_size
image_size += 0x4000

print(f"Total region size: 0x{image_size:x} bytes")
image_addr = u.malloc(image_size + 1024 * 1024)

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

tba = u.ba.copy()
del u.adt["cpus"]["cpu1"]
del u.adt["cpus"]["cpu2"]
del u.adt["cpus"]["cpu3"]
del u.adt["cpus"]["cpu4"]
del u.adt["cpus"]["cpu5"]
del u.adt["cpus"]["cpu6"]
u.adt["cpus"]["cpu7"].state = "busy"
u.adt["cpus"].max_cpus = 1
u.adt["chosen"]["memory-map"].BootArgs = (new_base + bootargs_off, 0x4000)
u.adt["chosen"]["memory-map"].DeviceTree = (u.ba.devtree, u.ba.devtree_size)
u.adt["chosen"].dram_size = 0x100000000
tba.cmdline = "-v cpus=1"
u.push_adt()
print(f"Setting up bootargs...")

if args.sepfw:
    tba.top_of_kernel_data = new_base + image_size
else:
    # SEP firmware is in here somewhere, keep top_of_kdata high so we hopefully don't clobber it
    tba.top_of_kernel_data = max(tba.top_of_kernel_data, new_base + image_size)
tba.top_of_kernel_data = new_base + image_size
# tba.mem_size_actual = 0x200000000
tba.mem_size_actual = 0x200000000
tba.mem_size = 0x300000000

iface.writemem(image_addr + bootargs_off, BootArgs.build(tba))

print(f"Copying stub...")

stub = asm.ARMAsm(f"""
1:
        ldr x4, [x1], #8
        str x4, [x2]
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

f = open("m1lli/asm-snippets/actual-vbar-2.S.elf.bin", "rb")
iface.writemem(0xa00000000, f.read(1024 * 1024))
f = open("m1lli/asm-snippets/inject3.S.elf.bin", "rb")
iface.writemem(0xa00002000, f.read(1024 * 1024))
f = open("m1lli/asm-snippets/reboot-physical.S.elf.bin", "rb")
iface.writemem(0xa00008000, f.read(1024 * 1024))
f = open("m1lli/asm-snippets/inject4.c.S.elf.bin", "rb")
iface.writemem(0xa00020000, f.read(1024 * 1024))
# p.smp_call(1, 0xa00000000)
# p.smp_call(2, 0xa00000000)
# p.smp_call(3, 0xa00000000)
# p.smp_call(4, 0xa00000000)
# p.smp_call(5, 0xa00000000)
# p.smp_call(6, 0xa00000000)
f = open("m1lli/asm-snippets/fadescreen.c.S.elf.bin", "rb")
iface.writemem(0xb00000000, f.read(1024 * 1024))
f = open("build/m1n1.macho", "rb")
iface.writemem(0xa20000000, f.read())
p.write64(0xa20000000, u.base)
if args.sepfw:
    time.sleep(1)
    # p.smp_call(1, 0xa00000000)
    # p.smp_call(2, 0xa00000000)
    # p.smp_call(3, 0xa00000000)
    # p.smp_call(4, 0xa00000000)
    # p.smp_call(5, 0xa00000000)
    # p.smp_call(6, 0xa00000000)
    p.smp_call(7, 0xb00000000)
    time.sleep(1)

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
