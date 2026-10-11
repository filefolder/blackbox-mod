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

    .global rpl_snap_fp
    .thumb_func
    .type rpl_snap_fp, %function
rpl_snap_fp:                    /* @0x080a029e, replaces subs.w r2, fp, #0 (then: it ne; movne r2, #1): a new Step
                                   Mode value fp snaps to steps unless it is OFF (0) or REPLACE (2) */
    movs    r2, #0
    cmp     fp, #0
    beq     1f
    cmp     fp, #2
    beq     1f
    movs    r2, #1
1:  cmp     r2, #0
    bx      lr

    .global rpl_snap_r3
    .thumb_func
    .type rpl_snap_r3, %function
rpl_snap_r3:                    /* engine, sequence parameter update, case Step Mode (@0x0805ef8e, replaces
                                   subs r3, #0; it ne; movne r3, #1): seq + 0x5e = "event times are steps" */
    cmp     r3, #2
    it      eq
    moveq   r3, #0
    cmp     r3, #0
    it      ne
    movne   r3, #1
    bx      lr
