/*
 * Pads view message handler, knob case, after the column stepper moved (0x080b43a4): r6 = the view. The stock
 * show / hide loop there is replaced by `bl pads_panels_thunk` + `b` to the handler's exit.
 */
    .syntax unified
    .thumb
    .text

    .global pads_panels_thunk
    .thumb_func
    .type pads_panels_thunk, %function
pads_panels_thunk:
    mov     r0, r6
    b.w     pads_panels
