/* The master clipper: what comp.c (firmware glue) and clip.c (the clipper; also built for the Mac) share. */
#ifndef CLIP_H
#define CLIP_H
#include <stdint.h>

enum { CLIP_OFF, CLIP_CLEAN, CLIP_DIODE, CLIP_TYPES };

struct clip_state {
    uint32_t magic, pos;
    float gain;                  /* the push, smoothed across blocks */
    float in[2][8];              /* the last eight pushed samples, per channel */
    float cut[2][16];            /* what the curve took off, at 96 kHz */
    float dc_in[2], dc_out[2];   /* Diode: the blocker for the offset an uneven clip leaves */
};

/* One block, in place. amount and shape are the knobs, 0..1. r may be 0. */
void clip_block(struct clip_state *s, int type, float amount, float shape, float *l, float *r, int n);

#endif
