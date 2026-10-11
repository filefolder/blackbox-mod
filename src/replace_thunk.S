/*
 * The recorder (FUN_080a02c0) calls the event list with r0 = list, r1 = the event in its frame. Pass its sp (the
 * frame) and the app (r7 at note-on, r4 at note-off) on to replace.c; at note-on also the MIDI message (r8).
 */
    .syntax unified
    .thumb
    .text

    .global rpl_on_thunk
    .thumb_func
    .type rpl_on_thunk, %function
rpl_on_thunk:                   /* @0x080a05fe: append the new note */
    push    {r4, lr}
    sub     sp, #8
    str     r8, [sp]            /* 5th argument */
    add     r2, sp, #16         /* the recorder's sp */
    mov     r3, r7
    bl      rpl_on
    add     sp, #8
    pop     {r4, pc}

    .global rpl_off_thunk
    .thumb_func
    .type rpl_off_thunk, %function
rpl_off_thunk:                  /* @0x080a073c: set the note's length */
    mov     r2, sp
    mov     r3, r4
    b.w     rpl_off
