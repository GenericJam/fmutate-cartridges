/* SPDX-License-Identifier: GPL-3.0-only
 * Real Skiff/Caravel input queues, ARP and DSP through their host harness.
 * No audio device. Included by expression_verify.py's offline acceptance run. */
#define UI_TEST_NO_MAIN 1
#include "ui_test.c"

static void message(uint32_t st, uint32_t note, uint32_t value)
{
    midi_enqueue((st >> 4) | st << 8 | note << 16 | value << 24, 1);
    events_block(CTL);
}

static track_t *reset(uint32_t mode)
{
    ui_power_on();
    memset(midi_ch, 0, sizeof midi_ch);
    memset(midi_notes, 0, sizeof midi_notes);
    memset(midi_owners, 0, sizeof midi_owners);
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
    t->eng_req = 3; /* LOFI: let an octave note queue during the real engine fade. */
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
    return bad ? 1 : 0;
}
