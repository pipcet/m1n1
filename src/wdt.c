/* SPDX-License-Identifier: MIT */

#include "wdt.h"
#include "adt.h"
#include "types.h"
#include "utils.h"

static u32 wdt_control;
static u64 wdt_regs;
static u32 wdt_count;

void wdt_disable(void)
{
    int path[8];
    int node = adt_path_offset_trace(adt, "/arm-io/wdt", path);

    if (node < 0) {
        printf("WDT node not found!\n");
        return;
    }


    if (adt_get_reg(adt, path, "reg", 0, &wdt_regs, NULL)) {
        printf("Failed to get WDT reg property!\n");
        return;
    }

    printf("WDT registers @ 0x%lx\n", wdt_regs);
    printf("previous value %08x\n", wdt_count = read32(wdt_regs + 0x10));
    printf("previous value %08x\n", wdt_control = read32(wdt_regs + 0x1c));
    printf("previous value %08x\n", wdt_count = read32(wdt_regs + 0x10));

    write32(wdt_regs + 0x1c, 0);

    printf("WDT disabled\n");
}

void wdt_enable(void)
{
  write32(wdt_regs + 0x10, wdt_count);
  write32(wdt_regs + 0x1c, wdt_control);
}
