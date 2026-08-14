"""Generate the original 60-second score and sound design for the OXPOS story."""

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "static" / "pos" / "media" / "oxpos-demo-bgm-v2.wav"
SAMPLE_RATE = 22_050
DURATION = 60


def frequency(midi_note: int) -> float:
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def pseudo_noise(index: int) -> float:
    value = math.sin(index * 12.9898 + 78.233) * 43_758.5453
    return (value - math.floor(value)) * 2.0 - 1.0


def pluck(note: int, note_time: float, decay: float = 5.5) -> float:
    if note_time < 0:
        return 0.0
    envelope = math.exp(-note_time * decay)
    tone = math.sin(2 * math.pi * frequency(note) * note_time)
    overtone = 0.34 * math.sin(2 * math.pi * frequency(note) * 2 * note_time)
    return (tone + overtone) * envelope


def tone_event(time: float, start: float, note: int, length: float = 0.5, level: float = 1.0) -> float:
    local = time - start
    if local < 0 or local >= length:
        return 0.0
    attack = min(1.0, local / 0.012)
    release = (1.0 - local / length) ** 2
    shimmer = math.sin(2 * math.pi * frequency(note) * local)
    shimmer += 0.26 * math.sin(2 * math.pi * frequency(note + 12) * local)
    return shimmer * attack * release * level


def sound_design(time: float, index: int) -> float:
    """Story beats: deadline, consequence, reveal, scan proof, alerts, and payoff."""
    value = 0.0

    # Cold-open impact and a short downward sting at the lost-sale reveal.
    cold_open = time - 0.06
    if 0 <= cold_open < 0.8:
        value += math.sin(2 * math.pi * (76 - cold_open * 38) * cold_open) * math.exp(-cold_open * 6) * 0.23
    for start, note in ((9.72, 64), (9.90, 60), (10.08, 57)):
        value += tone_event(time, start, note, 0.52, 0.10)

    # A rising breath pulls the picture into the OXPOS turnaround.
    riser = time - 19.45
    if 0 <= riser < 1.55:
        progress = riser / 1.55
        envelope = progress * progress * (1.0 - max(0.0, progress - 0.86) / 0.14)
        sweep = math.sin(2 * math.pi * (170 + 760 * progress * progress) * riser)
        value += (sweep * 0.06 + pseudo_noise(index * 5) * 0.035) * envelope
    for start, note in ((21.02, 72), (21.13, 76), (21.24, 79), (21.36, 84)):
        value += tone_event(time, start, note, 1.65, 0.18)

    # Product proof is reinforced with restrained UI sounds.
    for start in (33.35, 36.05, 38.65):
        value += tone_event(time, start, 88, 0.16, 0.085)
        value += tone_event(time, start + 0.07, 95, 0.18, 0.065)
    for start in (42.55, 45.35, 48.15):
        value += tone_event(time, start, 79, 0.62, 0.095)
        value += tone_event(time, start + 0.11, 86, 0.74, 0.075)

    # One final upward brand signature under the call to action.
    for start, note in ((53.65, 72), (53.82, 76), (54.00, 79), (54.18, 84)):
        value += tone_event(time, start, note, 1.8, 0.11)
    return value


def sample_at(index: int) -> float:
    time = index / SAMPLE_RATE
    fade_in = min(1.0, time / 0.18)
    fade_out = min(1.0, max(0.0, (DURATION - time) / 1.3))

    if time < 20:
        bpm = 92
        beat_position = time * bpm / 60
        beat_phase = beat_position % 1
        half_beat_position = beat_position * 2
        half_beat_phase = half_beat_position % 1
        bar = int(beat_position // 4)
        progression = [
            (45, 48, 52),  # A minor
            (41, 45, 48),  # F major
            (48, 52, 55),  # C major
            (43, 47, 50),  # G major
        ]
        chord = progression[bar % len(progression)]
        pad = sum(math.sin(2 * math.pi * frequency(note) * time) for note in chord) / 3
        low_pulse = math.sin(2 * math.pi * frequency(chord[0] - 12) * time) * math.exp(-beat_phase * 6.5)
        clock = math.sin(2 * math.pi * 1_100 * beat_phase * (60 / bpm)) * math.exp(-beat_phase * 42)
        motif_note = (chord[0] + 12, chord[2] + 12)[int(half_beat_position) % 2]
        motif = pluck(motif_note, half_beat_phase * (30 / bpm), 10.5)
        value = pad * 0.12 + low_pulse * 0.10 + clock * 0.038 + motif * 0.045
    else:
        local = time - 20
        bpm = 128
        beat_position = local * bpm / 60
        beat_phase = beat_position % 1
        half_beat_position = beat_position * 2
        half_beat_phase = half_beat_position % 1
        bar = int(beat_position // 4)
        progression = [
            (48, 52, 55),  # C major
            (43, 47, 50),  # G major
            (45, 48, 52),  # A minor
            (41, 45, 48),  # F major
        ]
        chord = progression[bar % len(progression)]
        pad = sum(math.sin(2 * math.pi * frequency(note) * local) for note in chord) / 3
        bass = math.sin(2 * math.pi * frequency(chord[0] - 12) * local) * math.exp(-beat_phase * 5.5)
        arpeggio = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 12]
        arp_note = arpeggio[int(half_beat_position) % len(arpeggio)]
        arp = pluck(arp_note, half_beat_phase * (30 / bpm), 8.5)
        kick = math.sin(2 * math.pi * (76 - 30 * beat_phase) * beat_phase * (60 / bpm)) * math.exp(-beat_phase * 17)
        beat_number = int(beat_position) % 4
        snare = pseudo_noise(index) * math.exp(-beat_phase * 25) if beat_number in (1, 3) else 0.0
        hi_hat = pseudo_noise(index * 3) * math.exp(-half_beat_phase * 52)
        value = pad * 0.105 + bass * 0.13 + arp * 0.12 + kick * 0.15 + snare * 0.038 + hi_hat * 0.022

    value += sound_design(time, index)
    return max(-0.92, min(0.92, value * fade_in * fade_out))


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    samples = array("h")
    for index in range(SAMPLE_RATE * DURATION):
        samples.append(int(sample_at(index) * 32_767))
    with wave.open(str(OUTPUT), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    return OUTPUT


if __name__ == "__main__":
    destination = build()
    print(f"Wrote {destination} ({destination.stat().st_size:,} bytes, {DURATION}s)")
