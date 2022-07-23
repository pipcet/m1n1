/* SPDX-License-Identifier: MIT */

#include "cpu_regs.h"
#include "utils.h"

static void init_common_avalanche(void)
{
    reg_mask(SYS_IMP_APL_HID1, HID1_ZCL_RF_MISPREDICT_THRESHOLD_MASK,
             HID1_ZCL_RF_MISPREDICT_THRESHOLD(1));
    reg_mask(SYS_IMP_APL_HID1, HID1_ZCL_RF_RESTART_THRESHOLD_MASK,
             HID1_ZCL_RF_RESTART_THRESHOLD(3));

    reg_set(SYS_IMP_APL_HID11, HID11_DISABLE_LD_NT_WIDGET);

    reg_set(SYS_IMP_APL_HID9, HID9_TSO_ALLOW_DC_ZVA_WC | HID9_AVL_UNK17);

    // "configure dummy cycles to work around incorrect temp sensor readings on
    // NEX power gating" (maybe)
    reg_mask(SYS_IMP_APL_HID13,
             HID13_POST_OFF_CYCLES_MASK | HID13_POST_ON_CYCLES_MASK | HID13_PRE_CYCLES_MASK |
                 HID13_GROUP0_FF1_DELAY_MASK | HID13_GROUP0_FF2_DELAY_MASK |
                 HID13_GROUP0_FF3_DELAY_MASK | HID13_GROUP0_FF4_DELAY_MASK |
                 HID13_GROUP0_FF5_DELAY_MASK | HID13_GROUP0_FF6_DELAY_MASK |
                 HID13_GROUP0_FF7_DELAY_MASK | HID13_RESET_CYCLES_MASK,
             HID13_POST_OFF_CYCLES(8) | HID13_POST_ON_CYCLES(8) | HID13_PRE_CYCLES(1) |
                 HID13_GROUP0_FF1_DELAY(4) | HID13_GROUP0_FF2_DELAY(4) | HID13_GROUP0_FF3_DELAY(4) |
                 HID13_GROUP0_FF4_DELAY(4) | HID13_GROUP0_FF5_DELAY(4) | HID13_GROUP0_FF6_DELAY(4) |
                 HID13_GROUP0_FF7_DELAY(4) | HID13_RESET_CYCLES(0));

    reg_mask(SYS_IMP_APL_HID26, HID26_GROUP1_OFFSET_MASK | HID26_GROUP2_OFFSET_MASK,
             HID26_GROUP1_OFFSET(26) | HID26_GROUP2_OFFSET(31));
    reg_mask(SYS_IMP_APL_HID27, HID27_GROUP3_OFFSET_MASK, HID27_GROUP3_OFFSET(31));
}

static void init_m2_avalanche(void)
{
    init_common_avalanche();

    reg_mask(SYS_IMP_APL_HID3, HID3_DEV_PCIE_THROTTLE_LIMIT_MASK, HID3_DEV_PCIE_THROTTLE_LIMIT(60));
    reg_set(SYS_IMP_APL_HID3, HID3_DEV_PCIE_THROTTLE_ENABLE);
    reg_set(SYS_IMP_APL_HID18, HID18_AVL_UNK27 | HID18_AVL_UNK29);
    reg_set(SYS_IMP_APL_HID16, HID16_AVL_UNK12);
}

void init_t8112_avalanche(int rev)
{
    UNUSED(rev);

    init_m2_avalanche();
}
