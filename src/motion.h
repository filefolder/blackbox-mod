/* "Motion": modulation on the delay's repeats. Shared by motion.c (the DSP) and munchi.cpp (the slot's glue). */
#ifndef MOTION_H
#define MOTION_H
#include <stdint.h>

enum { MOTION_OFF, MOTION_TREM, MOTION_FILTER, MOTION_DUCK, MOTION_SWELL, MOTION_TYPES };

struct motion_state {
    uint32_t magic, phase;
    float env, wet_env;          /* followers: what is going into the delay, and the repeats themselves */
    float ramp;                  /* Duck: how far out of the way; Swell: how far faded in */
    float g;                     /* the smoothed gain */
    float lp[2], lp2[2];         /* Filter: two poles per channel */
    uint8_t type, _r[3];
};

#ifdef __cplusplus
extern "C" {
#endif
/* One block, in place on the repeats. in_level = peak of what went into the delay this block. r may be 0. */
void motion_block(struct motion_state *s, int type, float rate, float depth, float in_level, float *l, float *r,
                  int n);
#ifdef __cplusplus
}
#endif

#endif
