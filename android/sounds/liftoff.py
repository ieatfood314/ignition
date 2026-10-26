#!/usr/bin/env python3
"""Synthesizes the "rocket launch" liftoff sound: a blast, then the crackling roar of the engines, then a
beat of quiet so it repeats like an alarm. Plain stdlib, deterministic. Rebuild the bundled sound with:

    python3 android/sounds/liftoff.py /tmp/liftoff.wav
    ffmpeg -y -i /tmp/liftoff.wav -c:a libvorbis -q:a 4 android/app/src/main/res/raw/liftoff.ogg

Phone speakers barely go below 300 Hz, so most of the weight is in the low mids, not the sub-bass.
"""
import math
import random
import struct
import sys
import wave

RATE = 32000
LENGTH = 5.0  # seconds, including the gap before it loops
rand = random.Random(314)


def lowpass(xs, hz):
    a = 1 - math.exp(-2 * math.pi * hz / RATE)
    y, out = 0.0, []
    for x in xs:
        y += a * (x - y)
        out.append(y)
    return out


def highpass(xs, hz):
    return [x - l for x, l in zip(xs, lowpass(xs, hz))]


def unit(xs):
    """Scaled to an RMS of 1, so the mix levels below mean something."""
    rms = math.sqrt(sum(x * x for x in xs) / len(xs)) or 1
    return [x / rms for x in xs]


def env(t):
    """Blast: instant attack, fast decay. Roar: swells in under the blast, holds, fades by 4.2 s."""
    blast = math.exp(-t / 0.35) if t >= 0 else 0
    roar = min(1, t / 0.25) * (1 if t < 2.8 else max(0, 1 - (t - 2.8) / 1.4))
    return blast, roar


def main(path):
    n = int(RATE * LENGTH)
    noise = [rand.uniform(-1, 1) for _ in range(n)]
    # The blast: broadband noise, its brightness falling away as it decays, plus a dropping thump.
    bright = unit(lowpass(lowpass(noise, 3500), 3500))
    dark = unit(lowpass(lowpass(noise, 500), 500))
    # The roar: a rumble band (~150-900 Hz) that wobbles a little, like turbulent exhaust.
    rumble = unit(highpass(lowpass(lowpass(lowpass(noise, 900), 900), 900), 150))
    wobble = unit(lowpass([rand.uniform(-1, 1) for _ in range(n)], 4))
    # Crackle: sparse sharp pops on top of the roar, the signature of a big solid motor.
    pops = [0.0] * n
    for i in range(n):
        if rand.random() < 0.0016:
            amp = rand.uniform(0.3, 1.0)
            for k in range(min(160, n - i)):
                pops[i + k] += amp * math.exp(-k / 25) * rand.uniform(-1, 1)
    pops = unit(highpass(pops, 700))

    out = []
    phase = 0.0
    for i in range(n):
        t = i / RATE
        blast, roar = env(t)
        f = 40 + 90 * math.exp(-t / 0.15)  # the thump drops in pitch
        phase += 2 * math.pi * f / RATE
        thump = math.sin(phase) * math.exp(-t / 0.5)
        mix = blast * (0.9 * bright[i] * math.exp(-t / 0.12) + 0.9 * dark[i]) + 0.5 * thump
        mix += roar * (0.2 * rumble[i] * (1 + 0.35 * wobble[i]) + 0.12 * pops[i])
        out.append(math.tanh(1.5 * mix))  # saturate: loud, and as rough as the real thing

    peak = max(abs(x) for x in out) or 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(32000 * x / peak)) for x in out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "liftoff.wav")
