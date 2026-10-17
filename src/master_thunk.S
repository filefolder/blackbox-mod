/*
 * INFO on the knob screen (FUN_080a2e60, case 0x23 @0x080a3064): stock loads the FX cell id from app+0x28 and only acts
 * on pad cells. The two instructions there (ldrh r3, [r0, #0x28]; ubfx r2, r3, #4, #4) are replaced by a bl here:
 * master_info_knob handles a master page and returns the id for the stock test (the global cell 0x40 = "do nothing").
 */
    .syntax unified
    .thumb
    .text

    .global master_info_thunk
    .thumb_func
    .type master_info_thunk, %function
master_info_thunk:
    push    {r0, lr}
    bl      master_info_knob
    mov     r3, r0
    pop     {r0, lr}
    ubfx    r2, r3, #4, #4
    bx      lr
