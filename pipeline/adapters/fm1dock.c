/* SPDX-License-Identifier: GPL-3.0-only */
/* fm1-dock's host for one module (joakimeriksson/fm1-dock, firmware/src/dock.c), as the app of fm1-dock's own fake
 * FM-1: pipeline/recipes/fm1dock.py builds this file in place of the app recipes/dinghy.py finds in fm1-dock's tree,
 * so adapters/dinghy.c drives it as any Dinghy app, and links the module (its sources as fm1-dock's tools/hbmod.py
 * builds them, the descriptor `hb_module`) beside it.
 *
 * What dock.c does for a module, for the one module of a solo app (tools/build.py --modules DIR --name ..., which
 * starts it at a new install): the table of host calls (hb_host_t) on the fake's screen, clock and MIDI; the module's
 * render() as the audio interrupt; init(), then the main loop: the keys, buttons and encoders to ui() (a module with
 * HB_F_PANEL) or the keys as notes (one without), MIDI in to midi() (or note() / cc()), draw(). Not here: the
 * picker (HOME held 1.5 s; there is no other module to pick), the top line it draws over a module without
 * HB_F_SCREEN, the sine it falls back to, BLE, the keys' force sensing (sense_keys: none), the deploy and the
 * probes. The module's settings record and its banks are kept in RAM, as on an FM-1 whose flash holds none yet.
 *
 * The fake's clock moves in platform_poll (one millisecond a call): a module waiting on the clock (FM-1 SID's
 * dock_platform.h, fm1_delay_ms: H->ms() until it moved) gets a platform_poll when its main loop asks the time over
 * and over within one millisecond. The 24 MHz counter (H->ticks) follows the clock. */
#include "platform.c"
#include "hb_module.h"

extern const hb_module_t hb_module;          /* the module's descriptor (HB_DESC) */

static const hb_module_t *fd_m;
static volatile int fd_in_audio;             /* (render: the audio interrupt, on the engine's thread) */
static uint32_t fd_ms_at, fd_ms_asks;

static uint32_t h_ms(void)
{
    if (!fd_in_audio) {
        if (fd_ms_at != fm1_ms) {
            fd_ms_at = fm1_ms;
            fd_ms_asks = 0;
        } else if (++fd_ms_asks >= 256u) {   /* waiting for the clock: the millisecond it waits for */
            fd_ms_asks = 0;
            platform_poll();
        }
    }
    return fm1_ms;
}
static uint32_t h_ticks(void) { return fm1_ms * 24000u; }
static void h_log(const char *s) { (void)s; }
static void h_log_u32(const char *s, uint32_t v) { (void)s; (void)v; }
static void h_fill(int32_t x, int32_t y, int32_t w, int32_t h, uint16_t c)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > 240) w = 240 - x;
    if (y + h > 240) h = 240 - y;
    if (w > 0 && h > 0)
        lcd_fill((uint32_t)x, (uint32_t)y, (uint32_t)w, (uint32_t)h, c);
}
static void h_text(int32_t x, int32_t y, int32_t w, uint32_t big, const char *s, uint16_t fg, uint32_t center)
{
    const felucca_font_t *f = big ? &FONT_L : &FONT_S;
    if (x < 0 || y < 0 || w <= 0 || x + w > 240 || y + (int32_t)f->h > 240)
        return;
    draw_text_box((uint32_t)x, (uint32_t)y, (uint32_t)w, f, s, fg, center ? 1 : 0);
}
static void h_cv_begin(int32_t w, int32_t h, uint16_t bg)
{
    if (w < 1) w = 1;
    if (w > 240) w = 240;
    if (h < 1) h = 1;
    if ((uint32_t)(w * h) > CV_MAX) h = (int32_t)(CV_MAX / (uint32_t)w);
    cv_begin((uint32_t)w, (uint32_t)h, bg);
}
static void h_cv_rect(int32_t x, int32_t y, int32_t w, int32_t h, uint16_t c) { cv_rect(x, y, w, h, c); }
static void h_cv_line(int32_t x0, int32_t y0, int32_t x1, int32_t y1, uint16_t c) { cv_line(x0, y0, x1, y1, c); }
static void h_cv_pixel(int32_t x, int32_t y, uint16_t c) { cv_pset(x, y, c); }
static void h_cv_text(int32_t x, int32_t y, uint32_t big, const char *s, uint16_t fg)
{
    cv_text(x, y, big ? &FONT_L : &FONT_S, s, fg);
}
static void h_cv_blit(int32_t x, int32_t y)
{
    if (x >= 0 && y >= 0 && x + (int32_t)cv_w <= 240 && y + (int32_t)cv_h <= 240)
        cv_blit((uint32_t)x, (uint32_t)y);
}
static void h_led(uint32_t id, uint32_t on) { if (id < 41u) fm1_led_key(id, on == 1u); }
static uint32_t fd_rng = 0x2545F491u;
static uint32_t h_rand(void)
{
    fd_rng ^= fd_rng << 13;
    fd_rng ^= fd_rng >> 17;
    fd_rng ^= fd_rng << 5;
    return fd_rng;
}
static uint8_t fd_rec[240];                  /* the module's settings record */
static uint32_t fd_rec_n;
static int h_load(void *p, uint32_t n)
{
    if (!fd_rec_n || n > fd_rec_n) return -1;
    memcpy(p, fd_rec, n);
    return 0;
}
static void h_save(const void *p, uint32_t n)
{
    if (n > sizeof fd_rec) return;
    memcpy(fd_rec, p, n);
    fd_rec_n = n;
}
static void h_midi_send(uint32_t st, uint32_t d1, uint32_t d2) { midi_send(st, d1, d2); }
static uint32_t h_master(void) { return 4096u; }   /* (adapters/dinghy.c applies MASTER to what render made) */
static void h_blit(int32_t x, int32_t y, int32_t w, int32_t h, const uint16_t *px)
{
    if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 240 || y + h > 240)
        return;
    lcd_blit((uint32_t)x, (uint32_t)y, (uint32_t)w, (uint32_t)h, px);
}
static int32_t h_text_w(uint32_t big, const char *s) { return text_w(big ? &FONT_L : &FONT_S, s); }
/* the banks: dock.c's bank area holds 9 of 8 KiB for every module; bank 0 is bank_load / bank_save's */
#define FD_BANKS 9u
static struct { uint32_t idx, n; uint8_t used, b[HB_BANK_MAX]; } fd_bank[FD_BANKS];
static int fd_bank_find(uint32_t idx)
{
    uint32_t i;
    for (i = 0; i < FD_BANKS; i++)
        if (fd_bank[i].used && fd_bank[i].idx == idx) return (int)i;
    return -1;
}
static int h_bank_load_n(uint32_t idx, void *p, uint32_t n)
{
    int i = idx <= HB_BANK_IDX_MAX ? fd_bank_find(idx) : -1;
    if (i < 0) return -1;
    if (!n) return (int)fd_bank[i].n;
    if (n > fd_bank[i].n) n = fd_bank[i].n;
    memcpy(p, fd_bank[i].b, n);
    return (int)n;
}
static int h_bank_save_n(uint32_t idx, const void *p, uint32_t n)
{
    int i;
    if (idx > HB_BANK_IDX_MAX || n > HB_BANK_MAX) return -1;
    i = fd_bank_find(idx);
    if (!n) {                                 /* (n 0: the bank given back) */
        if (i >= 0) fd_bank[i].used = 0;
        return 0;
    }
    if (i < 0)
        for (i = 0; i < (int)FD_BANKS && fd_bank[i].used; i++)
            ;
    if (i >= (int)FD_BANKS) return -1;
    fd_bank[i].used = 1;
    fd_bank[i].idx = idx;
    fd_bank[i].n = n;
    memcpy(fd_bank[i].b, p, n);
    return 0;
}
static uint32_t h_bank_free(void)
{
    uint32_t i, k = 0;
    for (i = 0; i < FD_BANKS; i++) k += !fd_bank[i].used;
    return k;
}
static int h_bank_load(void *p, uint32_t n) { return h_bank_load_n(0, p, n) < 0 ? -1 : 0; }
static int h_bank_save(const void *p, uint32_t n) { return h_bank_save_n(0, p, n); }
static uint32_t h_sense_keys(void) { return 0; }
static int h_key_sense(uint32_t k, hb_key_sense_t *out) { (void)k; (void)out; return -1; }
static void h_sense_set(uint32_t on, uint32_t drive, uint32_t curve, uint32_t ceil)
{
    (void)on; (void)drive; (void)curve; (void)ceil;
}

static const hb_host_t HOST = {
    HB_ABI, sizeof(hb_host_t), h_ms, h_ticks, h_log, h_log_u32,
    h_fill, h_text, h_cv_begin, h_cv_rect, h_cv_line, h_cv_pixel, h_cv_text, h_cv_blit,
    h_led, h_rand, h_load, h_save, h_midi_send, h_master, h_blit, h_text_w, h_bank_load, h_bank_save,
    h_sense_keys, h_key_sense, h_sense_set, h_bank_load_n, h_bank_save_n, h_bank_free,
};

/* the sound: the module renders (dock.c's app_audio) */
static void app_audio(int32_t *out, uint32_t n)
{
    const hb_module_t *m = fd_m;
    if (m && m->render) {
        fd_in_audio = 1;
        m->render(out, n);
        fd_in_audio = 0;
    }
}

static void app_main(void)
{
    const hb_module_t *m = &hb_module;
    uint32_t prev = 0, prevb = 0, full = 1, k, keys, down, up, b, rel, panel;
    uint8_t s, d1, d2, held[27] = {0};

    lcd_fill(0, 0, 240, 240, 0);
    for (k = 0; k < 60u; k++)                 /* (dock.c: 60 ms for the key scan to see what is held at power-on) */
        platform_poll();
    if (m->abi != HB_ABI || !m->init || m->init(&HOST))
        for (;;)
            platform_poll();                  /* (dock.c falls back to its sine: not here) */
    fd_m = m;
    panel = (m->flags & HB_F_PANEL) && m->ui;
    for (;;) {
        platform_poll();
        keys = fm1_in.notes;                  /* bit k: key k (0 = F3 .. 26 = G5) */
        down = keys & ~prev;
        up = prev & ~keys;
        prev = keys;
        for (k = 0; k < 27u; k++) {
            if (panel) {                      /* the module plays its keys */
                if (down >> k & 1u) m->ui(HB_EV_KEY_DOWN | k, 0);
                if (up >> k & 1u) m->ui(HB_EV_KEY_UP | k, 0);
                continue;
            }
            if (down >> k & 1u) { held[k] = (uint8_t)(53u + k); m->note(0, held[k], 100); midi_send(0x90, held[k], 100); }
            if (up >> k & 1u) { m->note(0, held[k], 0); midi_send(0x80, held[k], 0); }
        }
        while (midi_get(&s, &d1, &d2)) {
            if (m->midi) { m->midi(s, d1, d2); continue; }
            switch (s & 0xF0u) {
            case 0x90: m->note(s & 15u, d1, d2); break;
            case 0x80: m->note(s & 15u, d1, 0); break;
            case 0xB0: if (m->cc) m->cc(s & 15u, d1, d2); break;
            default: break;
            }
        }
        b = fm1_input_edges(&rel);            /* (the fake's releases: from the buttons held) */
        b |= fm1_in.buttons & ~prevb;
        rel = (prevb | b) & ~fm1_in.buttons;
        prevb = fm1_in.buttons;
        if (m->ui)
            for (k = panel ? 0u : 2u; k < 14u; k++) {   /* (OCT- / OCT+ / HOME are the host's without HB_F_PANEL) */
                if (!panel && k == BTN_HOME) continue;
                if (b >> k & 1u) m->ui(HB_EV_BTN_DOWN | k, 0);
                if (rel >> k & 1u) m->ui(HB_EV_BTN_UP | k, 0);
            }
        for (k = 0; k < 7u; k++) {
            int32_t c = fm1_enc_take(k);
            if (c && m->ui) m->ui(HB_EV_ENC | k, c);
        }
        if (m->draw) m->draw(full);
        full = 0;
    }
}
