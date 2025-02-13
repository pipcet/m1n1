/* SPDX-License-Identifier: MIT */

#ifndef FB_H
#define FB_H

#include "types.h"

typedef struct {
    u32 *ptr;   /* pointer to the start of the framebuffer */
    u32 stride; /* framebuffer stride divided by four (i.e. stride in pixels) */
    u32 depth;  /* framebuffer depth (i.e. bits per pixel) */
    u32 width;  /* width of the framebuffer in pixels */
    u32 height; /* height of the framebuffer in pixels */
} fb_t;

typedef struct {
    u8 r;
    u8 g;
    u8 b;
} rgb_t;

extern fb_t fb;

static inline rgb_t int2rgb(u32 c)
{
    return (rgb_t){c >> 16, c >> 8, c};
}

void fb_init(void);
void fb_shutdown(bool restore_logo);

void fb_blit(u32 x, u32 y, u32 w, u32 h, void *data, u32 stride);
void fb_unblit(u32 x, u32 y, u32 w, u32 h, void *data, u32 stride);
void fb_fill(u32 x, u32 y, u32 w, u32 h, rgb_t color);
void fb_clear(rgb_t color);

void fb_display_logo(void);
void fb_restore_logo(void);
void fb_improve_logo(void);

void fb_console_scroll(u32 n);
void fb_console_reserve_lines(u32 n);
ssize_t fb_console_write(const char *bfr, size_t len);

#endif
