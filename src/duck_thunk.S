/*
 * Voice-start thunks: called in place of the two pad classes' voice-start functions. If the pad object is pad 1
 * (id 0 at +0x18) set the duck trigger flag; for every pad set its bit in the trigger mask; then continue into the original with every argument untouched.
 */
    .syntax unified
    .thumb
    .text

    .equ DUCK_BASE, 0x2405ff70

    .macro voice_start name, original
    .global \name
    .thumb_func
    .type \name, %function
\name:
    push    {r0-r3}
    ldr     r3, =DUCK_BASE
    ldr     r1, [r0, #0x18]         @ pad id: bank << 16 | row << 8 | col
    cbnz    r1, 2f
    movs    r2, #1
    strb    r2, [r3, #8]            @ pad 1: the duck trigger
2:  ubfx    r2, r1, #8, #2
    and     r1, r1, #3
    add     r1, r1, r2, lsl #2      @ row * 4 + col
    movs    r2, #1
    lsls    r2, r1
    ldrh    r0, [r3, #14]
    orrs    r0, r2
    strh    r0, [r3, #14]           @ every pad: its bit for the LFO retriggers (lfo.c)
    pop     {r0-r3}
    ldr     r12, =\original + 1
    bx      r12
    .ltorg
    .endm

    voice_start duck_voice_start_a, 0x08058548
    voice_start duck_voice_start_b, 0x08065908
