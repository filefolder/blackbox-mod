/*
 * The note list reload (FUN_080bc17c) highlights the current note at two sites with r0 = cell widget, r1 = 1 and
 * the view in r4: pass the view and which site it is to prk_reload_hl (prknobs.c).
 */
    .syntax unified
    .thumb
    .text

    .global prk_hl_keep
    .thumb_func
    .type prk_hl_keep, %function
prk_hl_keep:                    /* @0x080bc1ce: the old current note is still there */
    mov     r2, r4
    movs    r3, #1
    b.w     prk_reload_hl

    .global prk_hl_first
    .thumb_func
    .type prk_hl_first, %function
prk_hl_first:                   /* @0x080bc398: nothing to keep, the first note becomes current */
    mov     r2, r4
    movs    r3, #0
    b.w     prk_reload_hl
