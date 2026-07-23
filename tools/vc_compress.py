#!/usr/bin/env python3
"""
vc_compress.py -- Visual Concepts 0E4837C3 compressor (exact inverse of vc_decomp).

Emits a token stream the game's own decompressor accepts:

  ctrl byte, bits consumed LSB-first
    bit 0 -> literal byte
    bit 1 -> big-endian u16 token: offset = tok & mask, length = (tok>>offbits)+3
  ctrl == 0 -> fast path, 8 raw literals follow

`offbits` comes from the block being replaced, so a rebuilt block keeps the same
window variant the game selected for that resource. Constraints that follow from
the format (and from the decoder's own guard):

  1 <= offset <= (1<<offbits)-1      and offset <= bytes emitted so far
  3 <= length <= ((1<<(16-offbits))-1)+3

Matching is greedy with a hash chain over 3-byte keys -- good enough to beat the
original in practice, which is what matters, because a replaced resource must
fit the slot it came from (see docs/04_MODDING_INTEL.md: packed resources cannot
grow).
"""
import struct

MAGIC = 0x0E4837C3
HDR = 0x14


def _best_matches(src, offbits, depth, start=0):
    """For every position, the longest match and its offset. (0,0) if none.

    Hot loop -- this dominates compression time, so it avoids per-position
    allocation: the 3-byte hash key is maintained as a rolling integer rather
    than by slicing out a `bytes` object, and each chain keeps only the newest
    `depth` positions. Because the token packs length into 16-offbits bits the
    longest possible match is short (18 bytes at offbits=12), so a candidate that
    reaches the cap ends the search immediately.
    """
    n = len(src)
    max_off = (1 << offbits) - 1
    max_len = ((1 << (16 - offbits)) - 1) + 3
    lens = bytearray(n)
    offs = [0] * n
    if n < 4:
        return lens, offs

    head = {}
    key = (src[0] << 8) | src[1]
    for i in range(n - 2):
        key = ((key << 8) | src[i + 2]) & 0xFFFFFF
        chain = head.get(key)
        if chain is not None:
            if i < start:                      # prefix: index only, do not score
                chain.append(i)
                if len(chain) > depth:
                    del chain[0]
                continue
            bl = 0
            bo = 0
            lim = max_len if n - i > max_len else n - i
            for cand in reversed(chain):
                off = i - cand
                if off > max_off:
                    break
                # first three bytes already match (same hash key, exact 24 bits)
                l = 3
                while l < lim and src[cand + l] == src[i + l]:
                    l += 1
                if l > bl:
                    bl = l
                    bo = off
                    if l >= lim:
                        break
            if bl >= 3:
                lens[i] = bl
                offs[i] = bo
            chain.append(i)
            if len(chain) > depth:
                del chain[0]
        else:
            head[key] = [i]
    return lens, offs


def reusable_prefix(block, upto):
    """How much of an existing block's token stream can be copied verbatim.

    Returns (token_bytes, output_len): the longest run of WHOLE 8-symbol control
    groups whose decoded output ends at or before `upto`. Safe because LZ decoder
    state is a pure function of the bytes decoded so far -- if the replacement
    keeps the first `output_len` bytes identical (it does; the edit starts later),
    then re-using those tokens reproduces exactly the same state.

    This is the difference between recompressing an 8 MB blob and recompressing
    only the part at or after the edit.
    """
    magic, unc, comp, _flags, ob = struct.unpack_from(">IIIII", block, 0)
    if magic != MAGIC:
        return b"", 0
    mask = (1 << ob) - 1
    i = HDR
    out = 0
    good_i = HDR
    good_out = 0
    end = min(comp, len(block))
    while i < end and out < upto:
        gi, gout = i, out
        ctrl = block[i]; i += 1
        if ctrl == 0:
            i += 8; out += 8
        else:
            for b in range(8):
                if (ctrl >> b) & 1:
                    if i + 2 > end:
                        return block[HDR:good_i], good_out
                    tok = (block[i] << 8) | block[i + 1]; i += 2
                    out += (tok >> ob) + 3
                else:
                    i += 1; out += 1
        if out <= upto:
            good_i, good_out = i, out
        else:
            return block[HDR:gi], gout
    return block[HDR:good_i], good_out


def compress_optimal(src, offbits, flags=0, depth=192, reuse=None):
    """Least-cost parse.

    Each symbol costs one control bit plus its payload: a literal is 1 byte, a
    match is 2. So the cheapest encoding is a shortest-path over positions, and
    because the token packs the length into 16-offbits bits the longest match is
    short (18 bytes at offbits=12), which makes the DP genuinely cheap.

    Greedy parsing came out ~1.8% BIGGER than the original cooker, which is fatal
    when a replaced resource has to fit its original slot; this closes that gap.
    """
    n = len(src)
    if n == 0:
        return struct.pack(">IIIII", MAGIC, 0, HDR, flags, offbits)
    pre_tokens, pre_out = (reuse if reuse else (b"", 0))
    lens, offs = _best_matches(src, offbits, depth, start=pre_out)
    INF = float("inf")
    cost = [INF] * (n + 1)
    pick = [0] * (n + 1)          # 1 = literal, else the match length used
    cost[n] = 0
    for i in range(n - 1, pre_out - 1, -1):
        best = cost[i + 1] + 9    # literal: 1 ctrl bit + 8 payload bits
        bl = 1
        m = lens[i]
        if m >= 3:
            for L in range(3, m + 1):
                c = cost[i + L] + 17      # match: 1 ctrl bit + 16 payload bits
                if c < best:
                    best, bl = c, L
        cost[i] = best
        pick[i] = bl

    out = bytearray()
    ctrl_pos = None
    ctrl = 0
    nbits = 0
    pend = bytearray()

    def flush():
        nonlocal ctrl, nbits, ctrl_pos, pend
        if nbits == 0:
            return
        out[ctrl_pos] = ctrl
        out.extend(pend)
        pend = bytearray()
        ctrl = 0
        nbits = 0
        ctrl_pos = None

    def emit(is_match, payload):
        nonlocal ctrl, nbits, ctrl_pos
        if nbits == 0:
            ctrl_pos = len(out)
            out.append(0)
        if is_match:
            ctrl |= (1 << nbits)
        pend.extend(payload)
        nbits += 1
        if nbits == 8:
            flush()

    i = pre_out
    while i < n:
        L = pick[i]
        if L >= 3:
            emit(True, struct.pack(">H", ((L - 3) << offbits) | offs[i]))
            i += L
        else:
            emit(False, src[i:i + 1])
            i += 1
    flush()
    body = pre_tokens + bytes(out)
    comp = HDR + len(body)
    return struct.pack(">IIIII", MAGIC, n, comp, flags, offbits) + body


def compress_block(src, offbits, flags=0, level=4):
    """Compress `src` into a full 0E4837C3 block (header + tokens)."""
    max_off = (1 << offbits) - 1
    max_len = ((1 << (16 - offbits)) - 1) + 3
    n = len(src)

    head = {}          # 3-byte key -> positions, oldest first (scan reversed)
    out = bytearray()
    ctrl_pos = None
    ctrl = 0
    nbits = 0
    pend = bytearray()

    def flush():
        nonlocal ctrl, nbits, ctrl_pos, pend
        if nbits == 0:
            return
        out[ctrl_pos] = ctrl
        out.extend(pend)
        pend = bytearray()
        ctrl = 0
        nbits = 0
        ctrl_pos = None

    def emit(is_match, payload):
        nonlocal ctrl, nbits, ctrl_pos
        if nbits == 0:
            ctrl_pos = len(out)
            out.append(0)          # placeholder; ctrl==0 means "8 literals"
        if is_match:
            ctrl |= (1 << nbits)
        pend.extend(payload)
        nbits += 1
        if nbits == 8:
            flush()

    depth = max(8, level * 24)
    mv = memoryview(src)

    def find(at):
        """Best (length, offset) at `at`, or (0, 0)."""
        if at + 3 > n:
            return 0, 0
        chain = head.get(bytes(mv[at:at + 3]))
        if not chain:
            return 0, 0
        bl = 0
        bo = 0
        lim = min(max_len, n - at)
        for cand in reversed(chain[-depth:]):          # newest candidates first
            off = at - cand
            if off > max_off:
                break                                  # older ones are further still
            l = 0
            while l < lim and src[cand + l] == src[at + l]:
                l += 1
            if l > bl:
                bl, bo = l, off
                if l >= lim:
                    break
        return (bl, bo) if bl >= 3 else (0, 0)

    def index(a, b):
        for k in range(a, min(b, n - 2)):
            head.setdefault(bytes(mv[k:k + 3]), []).append(k)

    i = 0
    while i < n:
        best_len, best_off = find(i)
        if best_len >= 3 and best_len < max_len:
            # Lazy matching: if starting one byte later yields a strictly longer
            # match, emitting a literal here is cheaper overall. This is what
            # closes the gap to the original cooker's output.
            index(i, i + 1)
            nxt_len, _nxt_off = find(i + 1)
            if nxt_len > best_len:
                emit(False, src[i:i + 1])
                i += 1
                continue
        if best_len >= 3:
            tok = ((best_len - 3) << offbits) | best_off
            emit(True, struct.pack(">H", tok))
            index(i, i + best_len)
            i += best_len
        else:
            emit(False, src[i:i + 1])
            index(i, i + 1)
            i += 1
    flush()

    comp = HDR + len(out)
    return struct.pack(">IIIII", MAGIC, n, comp, flags, offbits) + bytes(out)


def compress_block_stored(src, offbits, flags=0):
    """All-literal fallback: no matching, but always valid. ~12.5% larger than
    the input (one control byte per 8 literals), so only useful as a safety net."""
    out = bytearray()
    for i in range(0, len(src), 8):
        chunk = src[i:i + 8]
        if len(chunk) == 8:
            out.append(0)                       # fast path: 8 raw literals
            out.extend(chunk)
        else:
            out.append(0)                       # bits are 0 -> literals
            out.extend(chunk)
    comp = HDR + len(out)
    return struct.pack(">IIIII", MAGIC, len(src), comp, flags, offbits) + bytes(out)


def verify(block, expect):
    """Round-trip a produced block through the real decoder. Returns True/False."""
    import vc_decomp
    try:
        got, unc, _flags, _ob = vc_decomp.decompress_at(block, 0)
    except Exception:
        return False
    return got == expect and unc == len(expect)


def compress_verified(src, offbits, flags=0, reuse=None):
    """Compress and prove it decodes back to `src`, else fall back to literals.
    Raises if even the fallback fails (that would mean the format is wrong).

    `reuse` is an optional (token_bytes, output_len) from reusable_prefix(): the
    head of an existing block that decodes to the same leading bytes. It is a
    pure speed optimisation -- the round-trip check below still has to pass."""
    attempts = []
    if reuse and reuse[1]:
        attempts.append(lambda s_, o_, f_: compress_optimal(s_, o_, f_, reuse=reuse))
    attempts += [compress_optimal, compress_block]
    for attempt in attempts:
        try:
            blk = attempt(src, offbits, flags)
        except Exception:
            continue
        if verify(blk, src):
            return blk
    blk = compress_block_stored(src, offbits, flags)
    if verify(blk, src):
        return blk
    raise ValueError("compressor produced a block that does not round-trip")


if __name__ == "__main__":
    import os, sys, random
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from nhl2k_arc import Archive
    import vc_extract, vc_decomp

    arc = Archive(sys.argv[1])
    idxs = [int(x) for x in sys.argv[2:]] or [0, 6, 17, 203]
    for idx in idxs:
        raw = arc.read_file(arc.files[idx])
        blocks = vc_extract.find_blocks(raw)
        if not blocks:
            print("#%d: no blocks" % idx)
            continue
        p, unc, comp, offbits = blocks[0]
        dec, _u, flags, _ob = vc_decomp.decompress_at(raw, p)
        blk = compress_verified(dec, offbits, flags)
        ok = verify(blk, dec)
        print("#%-5d offbits=%-2d orig=%-9d ours=%-9d (%+.1f%%)  roundtrip=%s"
              % (idx, offbits, comp, len(blk),
                 100.0 * (len(blk) - comp) / comp, "OK" if ok else "FAIL"))
