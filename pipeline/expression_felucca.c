/* SPDX-License-Identifier: GPL-3.0-only
 * Real Skiff/Caravel input queues, ARP and DSP through their host harness.
 * No audio device. Included by expression_verify.py's offline acceptance run. */
#define UI_TEST_NO_MAIN 1
#if __has_include("ui_harness.h")
#include "ui_harness.h"
#else
#include "ui_test.c"
#endif

static void message(uint32_t st, uint32_t note, uint32_t value)
{
    midi_enqueue((st >> 4) | st << 8 | note << 16 | value << 24, 1);
    events_block(CTL);
}

static track_t *reset(uint32_t mode)
{
    ui_power_on();
    kb_boot_hold = 0;
    fm1_in.notes = 0;
    kb_prev = 0;
    memset(midi_ch, 0, sizeof midi_ch);
    memset(midi_notes, 0, sizeof midi_notes);
    memset(midi_owners, 0, sizeof midi_owners);
    memset(expr_depth, 0, sizeof expr_depth);
    for (uint32_t i = 0; i < NTRK; i++) expr_forget(&trk[i]);
    track_t *t = &trk[0];
    t->p[P_VOICE] = mode;
    return t;
}

static double level(track_t *t)
{
    int32_t out[CTL];
    double sum = 0;
    for (uint32_t b = 0; b < 1024; b++) {
        track_render(t, out, CTL);
        if (b >= 768)
            for (uint32_t i = 0; i < CTL; i++) sum += (double)out[i] * out[i];
    }
    return sqrt(sum / (256 * CTL));
}

static int verify(const char *name, int ok, double actual, double reference)
{
    printf("%s: %s (level %.4f, reference %.4f)\n", name, ok ? "PASS" : "FAIL", actual, reference);
    return !ok;
}

int main(void)
{
    int bad = 0;
    for (uint32_t mode = V_MONO; mode <= V_UNISON; mode++) {
        track_t *t = reset(mode);
        message(0x90, 60, 127);
        double normal = level(t);
        message(0xA0, 60, 0);
        double muted = level(t);
        bad += verify("MONO/LEGATO/UNISON mute", muted < normal * .0001, muted, normal);
        message(0x90, 66, 127);
        double next = level(t);
        bad += verify("new key does not inherit muted gain", next > normal * .25, next, normal);
        message(0x80, 66, 0);
        double fallback = level(t);
        bad += verify("fallback restores held key's muted gain", fallback < normal * .0001, fallback, normal);
        message(0xA0, 60, 127);
        double restored = level(t);
        bad += verify("held fallback responds again", restored > normal * .5 && restored < normal * 2,
                      restored, normal);
    }

    track_t *t = reset(V_POLY);
    message(0x90, 60, 127);
    double normal = level(t);
    message(0xB0, 64, 127);
    message(0xA0, 60, 0);
    message(0x80, 60, 0);
    double held = level(t);
    bad += verify("pedal retains muted key", held < normal * .0001, held, normal);
    message(0xA0, 60, 127);
    held = level(t);
    bad += verify("pedal-held key still responds", held > normal * .5, held, normal);
    message(0xB0, 64, 0);
    level(t);
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
    message(0xB0, 121, 0);
    double reset_level = level(t);
    bad += verify("CC121 restores a held key", reset_level > normal * .5, reset_level, normal);

    t = reset(V_POLY);
    t->p[P_AMODE] = AM_UP;
    t->p[P_AOCT] = 2;
    t->p[P_APROB] = t->p[P_AGATE] = 127;
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
    arp_silence(t);
    t->arp_idx = 0xFFFFFFFFu;
    arp_fire(t, FS / 4);
    double root = level(t);
    arp_silence(t);
    arp_fire(t, FS / 4);
    double octave = level(t);
    bad += verify("ARP root inherits held pressure", root < normal * .0001, root, normal);
    bad += verify("ARP octave inherits source-key pressure", octave < normal * .0001, octave, normal);
    message(0xA0, 60, 127);
    octave = level(t);
    bad += verify("pressure addresses sounding ARP octave", octave > normal * .25, octave, normal);
    message(0xA0, 60, 0);
    octave = level(t);
    bad += verify("ARP octave can be silenced live", octave < normal * .0001, octave, normal);

    t = reset(V_POLY);
    t->p[P_CHRD] = CH_MAJ;
    message(0x90, 60, 127);
    double chord = level(t);
    message(0xA0, 60, 0);
    double quiet_chord = level(t);
    bad += verify("source-key pressure controls every generated chord tone",
                  chord > normal && quiet_chord < chord * .0001, quiet_chord, chord);
    message(0xA0, 60, 127);
    double restored_chord = level(t);
    bad += verify("generated chord restores its own level",
                  restored_chord > chord * .5 && restored_chord < chord * 2, restored_chord, chord);

    t = reset(V_POLY);
    t->p[P_AMODE] = AM_UP;
    t->p[P_AOCT] = 2;
    t->p[P_APROB] = t->p[P_AGATE] = 127;
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
#ifdef ENGI_SEM
    t->eng_req = ENGI_SEM;
#else
    t->eng_req = 3; /* LOFI: let an octave note queue during the real engine fade. */
#endif
    engine_block(t);
    t->arp_idx = 0;
    arp_fire(t, FS / 4);
    for (uint32_t b = 0; b < 16; b++) {
        int32_t out[CTL];
        engine_block(t);
        track_render(t, out, CTL);
    }
    double deferred = level(t);
    bad += verify("deferred engine-switch note retains ARP source",
                  deferred < normal * .0001, deferred, normal);
    message(0xA0, 60, 127);
    double deferred_full = level(t);
    bad += verify("deferred octave responds after engine switch", deferred_full > 100,
                  deferred_full, normal);

    t = reset(V_POLY);
    message(0x90, 66, 127);
    double clean = level(t);
    t = reset(V_POLY);
    message(0x90, 84, 127);
    message(0xA0, 84, 0);
    for (uint32_t n = 40; n < 40 + NVOICE + 5; n++) message(0x90, n, 127);
    message(0x90, 66, 127);
    for (uint32_t n = 40; n < 40 + NVOICE + 5; n++) message(0x80, n, 0);
    level(t);
    message(0xA0, 84, 0);
    double stolen = level(t);
    bad += verify("stolen slot forgets previous key's pressure", stolen > clean * .5 && stolen < clean * 2,
                  stolen, clean);

    /* Bug fix 1: sequencer notes on a held, muted key's pitch must keep unity gain (never inherit input pressure). */
    t = reset(V_POLY);
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
    double held_muted = level(t);
    bad += verify("held key muted before sequencer step", held_muted < normal * .0001, held_muted, normal);
    {
        uint8_t sn = 60;
        put_step(t, 0, 1, &sn, ST_NOTE, 0);
        t->p[P_SLEN] = 1;
        t->p[P_SDIV] = 6; /* 1BAR: gate stays open across level(t) */
        t->p[P_SGATE] = 127;
        seq_start();
        events_block(CTL);
        double seq_same_pitch = level(t);
        bad += verify("sequencer note on held key's pitch keeps unity gain",
                      seq_same_pitch > normal * .5 && seq_same_pitch < normal * 2, seq_same_pitch, normal);
        seq_stop();
    }

    t = reset(V_LEGATO);
    message(0x90, 60, 127);
    double leg_normal = level(t);
    message(0xA0, 60, 0);
    {
        uint8_t sn = 67;
        put_step(t, 0, 1, &sn, ST_NOTE, 0);
        t->p[P_SLEN] = 1;
        t->p[P_SDIV] = 6;
        t->p[P_SGATE] = 127;
        seq_start();
        events_block(CTL);
        double seq_leg = level(t);
        bad += verify("LEGATO sequencer step plays at unity over muted key",
                      seq_leg > leg_normal * .5 && seq_leg < leg_normal * 2, seq_leg, leg_normal);
        seq_stop(); /* releases step 67, falls back to held key 60 */
        double leg_back = level(t);
        bad += verify("LEGATO fallback after sequencer step restores muted key",
                      leg_back < leg_normal * .0001, leg_back, leg_normal);
    }

    for (uint32_t mode = V_MONO; mode <= V_UNISON; mode++) {
        t = reset(mode);
        message(0x90, 60, 127);
        double m_ref = level(t);
        message(0xA0, 60, 0);
        uint8_t sn = 60;
        put_step(t, 0, 1, &sn, ST_NOTE, 0);
        t->p[P_SLEN] = 1;
        t->p[P_SDIV] = 6;
        t->p[P_SGATE] = 127;
        seq_start();
        events_block(CTL);
        double m_same = level(t);
        bad += verify("MONO/LEGATO/UNISON same-pitch sequencer step plays at unity",
                      m_same > m_ref * .5 && m_same < m_ref * 2, m_same, m_ref);
        seq_stop();
    }

    /* Panel key tap on same pitch as held muted MIDI key: unity while down, muted again on release. */
    t = reset(V_POLY);
    message(0x90, 60, 127);
    double pk_ref = level(t);
    message(0xA0, 60, 0);
    fm1_in.notes = 1u << 7; /* C4 panel key */
    events_block(CTL);
    double pk_down = level(t);
    bad += verify("POLY panel key on held muted MIDI pitch plays at unity",
                  pk_down > pk_ref * .5 && pk_down < pk_ref * 2, pk_down, pk_ref);
    fm1_in.notes = 0;
    events_block(CTL);
    double pk_up = level(t);
    bad += verify("POLY panel key release restores held MIDI key's muted pressure",
                  pk_up < pk_ref * .0001, pk_up, pk_ref);
    message(0xA0, 60, 127);
    double pk_live = level(t);
    bad += verify("POLY held MIDI key responds after panel tap",
                  pk_live > pk_ref * .5 && pk_live < pk_ref * 2, pk_live, pk_ref);

    t = reset(V_MONO);
    message(0x90, 60, 127);
    double mk_ref = level(t);
    message(0xA0, 60, 0);
    fm1_in.notes = 1u << 7;
    events_block(CTL);
    double mk_down = level(t);
    bad += verify("MONO panel key on held muted MIDI pitch plays at unity",
                  mk_down > mk_ref * .5 && mk_down < mk_ref * 2, mk_down, mk_ref);
    fm1_in.notes = 0;
    events_block(CTL);
    double mk_up = level(t);
    bad += verify("MONO panel key release restores held MIDI key's muted pressure",
                  mk_up < mk_ref * .0001, mk_up, mk_ref);

    t = reset(V_POLY);
    t->p[P_AMODE] = AM_UP;
    t->p[P_AOCT] = 1;
    t->p[P_APROB] = t->p[P_AGATE] = 127;
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
    fm1_in.notes = 1u << 7;
    events_block(CTL);
    arp_silence(t);
    t->arp_idx = 0xFFFFFFFFu;
    arp_fire(t, FS / 4);
    double ak_down = level(t);
    bad += verify("ARP panel key on held muted MIDI pitch switches step to unity",
                  ak_down > pk_ref * .25, ak_down, pk_ref);
    fm1_in.notes = 0;
    events_block(CTL);
    arp_silence(t);
    t->arp_idx = 0xFFFFFFFFu;
    arp_fire(t, FS / 4);
    double ak_up = level(t);
    bad += verify("ARP panel key release restores held MIDI key's muted pressure",
                  ak_up < pk_ref * .0001, ak_up, pk_ref);

    t = reset(V_POLY);
    t->p[P_AMODE] = AM_UP;
    t->p[P_AHOLD] = 1;
    t->p[P_AOCT] = 1;
    t->p[P_APROB] = t->p[P_AGATE] = 127;
    message(0x90, 60, 127);
    message(0xA0, 60, 0);
    message(0x80, 60, 0);
    arp_silence(t);
    t->arp_idx = 0xFFFFFFFFu;
    arp_fire(t, FS / 4);
    double ahold_muted = level(t);
    bad += verify("ARP HOLD retains released muted MIDI key", ahold_muted < pk_ref * .0001, ahold_muted, pk_ref);
    message(0xB0, 121, 0);
    arp_silence(t);
    t->arp_idx = 0xFFFFFFFFu;
    arp_fire(t, FS / 4);
    double ahold_cc121 = level(t);
    bad += verify("CC121 restores ARP HOLD released MIDI key", ahold_cc121 > pk_ref * .25, ahold_cc121, pk_ref);
    t->p[P_AHOLD] = 0;
    events_block(CTL);

#ifdef HARM_BAR
    t = reset(V_POLY);
    t->p[P_LATCH] = LT_NEW;
    message(0x90, 60, 127);
    double lat_ref = level(t);
    message(0xA0, 60, 0);
    message(0x80, 60, 0);
    double lat_muted = level(t);
    bad += verify("LATCH retains released muted MIDI key", lat_muted < lat_ref * .0001, lat_muted, lat_ref);
    message(0xB0, 121, 0);
    double lat_cc121 = level(t);
    bad += verify("CC121 restores latched released MIDI key",
                  lat_cc121 > lat_ref * .5 && lat_cc121 < lat_ref * 2, lat_cc121, lat_ref);
    latch_free(t);
    message(0x90, 60, 127);
    message(0x80, 60, 0);
    bad += verify("latch_free detaches released owner", expr_depth[0][60] == 0, expr_depth[0][60], 0);
#endif
#if FELUCCA_PAIR_MORPH
    t = reset(V_LEGATO);
    trk[1].p[P_VOICE] = V_LEGATO;
    if (pair_enable(0, 1)) {
        message(0xE0, 0, 127);
        bad += verify("PAIR MORPH bends both engines",
                      midi_bend_target[0] != 0 && midi_bend_target[0] == midi_bend_target[1],
                      midi_bend_target[1], midi_bend_target[0]);
        message(0xE0, 0, 64);
        message(0x90, 60, 127);
        double pa_ref = level(&trk[0]);
        double pb_ref = level(&trk[1]);
        message(0xA0, 60, 0);
        message(0x90, 66, 127);
        double pa_hi = level(&trk[0]);
        double pb_hi = level(&trk[1]);
        bad += verify("PAIR MORPH LEGATO new key plays at unity on both engines",
                      pa_hi > pa_ref * .25 && pb_hi > pb_ref * .25, pb_hi, pb_ref);
        message(0x80, 66, 0);
        double pa_fb = level(&trk[0]);
        double pb_fb = level(&trk[1]);
        bad += verify("PAIR MORPH LEGATO fallback restores muted key on both engines",
                      pa_fb < pa_ref * .0001 && pb_fb < pb_ref * .0001, pb_fb, pb_ref);
        message(0xA0, 60, 127);
        double pa_on = level(&trk[0]);
        double pb_on = level(&trk[1]);
        bad += verify("PAIR MORPH LEGATO fallback responds on both engines",
                      pa_on > pa_ref * .5 && pb_on > pb_ref * .5, pb_on, pb_ref);
        pair_disable();
    }
#endif

#ifdef HARM_BAR
    /* Bug fix 2 (Caravel): CHRD BUS chord retriggered by Harmony keeps the MIDI key's pressure depth. */
    t = reset(V_POLY);
    t->p[P_ROOT] = 0;
    t->p[P_SCALE] = 1;
    t->p[P_QUANT] = QN_OFF;
    t->p[P_CHRD] = CH_BUS;
    t->p[P_VOIC] = VC_CLOSE;
    harmony_on = harmony_follow[0] = 1;
    link_retrig[0] = 1;
    for (uint32_t i = 0; i < 4u; i++) { hroot_set(i, i & 1u ? 5u : 0u); hdur_set(i, 0); }
    seq_start();
    events_block(CTL);
    message(0x90, 60, 127);
    double bus_chord = level(t);
    message(0xA0, 60, 0);
    double bus_muted = level(t);
    bad += verify("CHRD BUS chord mutes from MIDI key", bus_muted < bus_chord * .0001, bus_muted, bus_chord);
    harmony_phase = 0;
    perf_bars_q16 = ((harmony_base_bar + harmony_bar + 1u) << 16) - 1u;
    perf_bars_rem = 0;
    events_block(CTL);
    double bus_remapped = level(t);
    bad += verify("Harmony CHRD BUS retrigger retains MIDI key pressure",
                  harmony_bar == 1u && bus_remapped < bus_chord * .0001, bus_remapped, bus_chord);
    message(0xA0, 60, 127);
    double bus_restored = level(t);
    bad += verify("remapped CHRD BUS chord responds to original MIDI key",
                  bus_restored > bus_chord * .5 && bus_restored < bus_chord * 2, bus_restored, bus_chord);
    seq_stop();
    harmony_on = harmony_follow[0] = link_retrig[0] = 0;
#endif
    return bad ? 1 : 0;
}
