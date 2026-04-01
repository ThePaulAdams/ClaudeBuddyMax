"""
Pure Python implementation of Zig std.hash.Wyhash (as used by Bun.hash).
Ported from: https://github.com/ziglang/zig/blob/0.13.0/lib/std/hash/wyhash.zig
"""

MASK64 = (1 << 64) - 1

# Public algorithm constants from the Zig stdlib Wyhash spec (not secrets)
WY_PRIMES = [
    0xa0761d6478bd642f,
    0xe7037ed1a0b428db,
    0x8ebc6af09c88c6e3,
    0x589965cc75374cc3,
]


def _read(data, offset, nbytes):
    """Read nbytes as little-endian u64."""
    return int.from_bytes(data[offset:offset + nbytes], 'little')


def _mum(a, b):
    """128-bit multiply, return (lo, hi) after truncation."""
    x = a * b
    lo = x & MASK64
    hi = (x >> 64) & MASK64
    return lo, hi


def _mix(a, b):
    """mum then XOR."""
    a, b = _mum(a, b)
    return (a ^ b) & MASK64


def wyhash(key, seed=0):
    """Compute Zig Wyhash. Matches Bun.hash(key) when seed=0."""
    if isinstance(key, str):
        key = key.encode('utf-8')

    data = key
    length = len(data)

    # Init state
    s0 = (seed ^ _mix((seed ^ WY_PRIMES[0]) & MASK64, WY_PRIMES[1])) & MASK64
    s1 = s0
    s2 = s0

    a = 0
    b = 0

    if length <= 16:
        # smallKey
        if length >= 4:
            end = length - 4
            quarter = (length >> 3) << 2
            a = ((_read(data, 0, 4) << 32) | _read(data, quarter, 4)) & MASK64
            b = ((_read(data, end, 4) << 32) | _read(data, end - quarter, 4)) & MASK64
        elif length > 0:
            a = (data[0] << 16) | (data[length >> 1] << 8) | data[length - 1]
            b = 0
        else:
            a = 0
            b = 0
    else:
        # Process 48-byte rounds
        i = 0
        if length >= 48:
            while i + 48 < length:
                # round: 3 pairs of 8-byte reads
                ra0 = _read(data, i, 8)
                rb0 = _read(data, i + 8, 8)
                s0 = _mix((ra0 ^ WY_PRIMES[1]) & MASK64, (rb0 ^ s0) & MASK64)

                ra1 = _read(data, i + 16, 8)
                rb1 = _read(data, i + 24, 8)
                s1 = _mix((ra1 ^ WY_PRIMES[2]) & MASK64, (rb1 ^ s1) & MASK64)

                ra2 = _read(data, i + 32, 8)
                rb2 = _read(data, i + 40, 8)
                s2 = _mix((ra2 ^ WY_PRIMES[3]) & MASK64, (rb2 ^ s2) & MASK64)

                i += 48

            # final0
            s0 = (s0 ^ s1 ^ s2) & MASK64

        # final1: process remaining 16-byte chunks
        remaining = data[i:]
        j = 0
        while j + 16 < len(remaining):
            s0 = _mix((_read(remaining, j, 8) ^ WY_PRIMES[1]) & MASK64, (_read(remaining, j + 8, 8) ^ s0) & MASK64)
            j += 16

        # Read last 16 bytes of full input
        a = _read(data, length - 16, 8)
        b = _read(data, length - 8, 8)

    # final2
    a = (a ^ WY_PRIMES[1]) & MASK64
    b = (b ^ s0) & MASK64
    a, b = _mum(a, b)
    return _mix((a ^ WY_PRIMES[0] ^ length) & MASK64, (b ^ WY_PRIMES[1]) & MASK64)


def bun_hash(s):
    """Match Bun.hash(s) truncated to u32 (as used by Claude Code companion)."""
    return wyhash(s) & 0xFFFFFFFF
