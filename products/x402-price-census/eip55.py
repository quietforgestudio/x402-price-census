#!/usr/bin/env python3
"""EIP-55 checksum as a THIRD VERDICT about an EVM address, not a case nuisance.

WHY THIS EXISTS (shift 595, 2026-11-04)
---------------------------------------
Shift 594 fixed a real defect by WITHHOLDING: a case-folded column cannot be an operand in an
inequality, so `addr_compare()` returns FOLD_UNCOMPARABLE when two values are fold-equal but not
byte-equal. That is correct for base58 (Solana), where case carries address identity and a fold is
information destroyed forever.

It is WRONG, as tolerance, for `0x` EVM addresses, and the argument is NOT ours: an independent
peer reader made it to us first, and it is paraphrased here. A header rendering the EIP-55 checksum
beside a body rendering plain lowercase is the SAME forty hex digits, so it is provably one
address; fold both, re-render the checksum from the folded form and compare it to the unfolded
original, and a match is a proof rather than a tolerance. Folding first turns a self-verifying
value into one that asserts nothing.

The reader is not named and nothing of his is quoted. An earlier version of this paragraph gave
the reason as "the exchange was private". That was WRONG and it is corrected here: the exchange
was in a venue work room whose own `visibility` field reads `public`, measured 2026-11-05. The
real reason is CONSENT, not privacy -- a public room is not its author's permission to put his
handle into an MIT-licensed file and a permanent git history, which is a different and
irreversible act. We asked him directly on 2026-11-05 and he has not answered; until he does, he
stays unnamed, and silence means unnamed rather than a default to yes. Any error in this
restatement is ours, and the credit is his to claim if he wants it.

That is right, and it is stronger than what shift 594 shipped. For a 20-byte EVM address the 40 hex
digits ARE the address; case is a CHECKSUM over them (EIP-55: uppercase digit i iff nibble i of
keccak256(lowercase_hex_without_0x) >= 8). So two fold-equal EVM strings are the same address with
certainty -- no withholding needed -- AND the case pattern is a separate, independently checkable
claim the server made. Folding first does not lose identity; it loses the CLAIM.

So there are four states an EVM occurrence can be in, and only one of them is a finding:
    CHECKSUM_VALID    mixed case, re-render matches -> the server emitted a correct EIP-55 address
    ALL_LOWER         no checksum asserted (or our own capture folded it -- indistinguishable)
    ALL_UPPER         no checksum asserted
    CHECKSUM_INVALID  mixed case, re-render does NOT match -> the server emitted a BROKEN checksum
                      on a real address. A different finding about a host than "different address".
                      SCOPE: this says the rendered case does not re-derive, nothing more. It does
                      NOT say the address is wrong (the 20 bytes may be correct), does not say a
                      payment would fail (untested), and is not a claim about any client. By
                      construction a client that re-renders the checksum must reject it, but NO
                      such library is installed here (eth_utils/web3/eth_hash all ABSENT, measured)
                      so we have run none of them. Say "does not re-render", never "would fail".

THE INSTRUMENT TRAP, and it is the reason this file carries its own Keccak
-------------------------------------------------------------------------
EIP-55 needs **Keccak-256**, the ORIGINAL Keccak padding (domain byte 0x01). Python's stdlib
`hashlib.sha3_256` is **NIST SHA3-256**, the same permutation with padding 0x06. They differ on
every input. There is no keccak in this environment (no pysha3, no eth_hash, no pycryptodome --
measured shift 595), so using `sha3_256` is the available-and-wrong choice. It is not a
hypothetical one either: the substitution makes a correctly-rendered address classify INVALID, so
it indicts every host it reads, at scale, silently. The figures that first demonstrated that to
us came from someone else's run and are not republished here; the mechanism is checkable from the
reference vectors below without them.

Therefore: the permutation is implemented here, and `--selftest` puts KNOWN-GOOD references through
the instrument BEFORE any corpus can be judged -- the two standard keccak256 vectors, all four
EIP-55 reference addresses from the EIP itself, a widely-deployed Base token address (asserted
only as CHECKSUM_VALID: this file never claims to know WHICH contract an address is), and a leg
asserting the NIST/Keccak values DIFFER (so a future edit that quietly swaps in
`hashlib.sha3_256` goes red instead of silently indicting every host we read).

Read-only, no network, no credential.

    python3 bin/eip55.py --selftest
    python3 bin/eip55.py <address> [...]      # classify one or more
"""
import sys

# ---------------------------------------------------------------- Keccac-f[1600]
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
# _R[x][y] -- rho offsets in the (column, row) convention used by state[x + 5*y]
_R = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_M = (1 << 64) - 1


def _rol(v, n):
    n &= 63
    return ((v << n) | (v >> (64 - n))) & _M


def _keccak_f(st):
    for rnd in range(24):
        c = [st[x] ^ st[x + 5] ^ st[x + 10] ^ st[x + 15] ^ st[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(st[x + 5 * y], _R[x][y])
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        st[0] ^= _RC[rnd]
    return st


def keccak256(data):
    """Original Keccak-256 (pad domain 0x01). NOT hashlib.sha3_256 (NIST, domain 0x06)."""
    if isinstance(data, str):
        data = data.encode()
    rate = 136                                   # 1600/8 - 2*256/8
    pad = bytearray(data)
    pad.append(0x01)
    while len(pad) % rate != 0:
        pad.append(0x00)
    pad[-1] |= 0x80
    st = [0] * 25
    for off in range(0, len(pad), rate):
        blk = pad[off:off + rate]
        for i in range(rate // 8):
            st[i] ^= int.from_bytes(blk[i * 8:i * 8 + 8], "little")
        _keccak_f(st)
    out = b"".join(st[i].to_bytes(8, "little") for i in range(4))
    return out[:32]


# ---------------------------------------------------------------- EIP-55
VALID = "CHECKSUM_VALID"
INVALID = "CHECKSUM_INVALID"
ALL_LOWER = "ALL_LOWER"
ALL_UPPER = "ALL_UPPER"
NOT_EVM = "NOT_EVM"


def is_evm(s):
    """A 0x-prefixed 40-hex-digit string. Deliberately strict: no elisions, no 0x-prefixed
    anything-else. `norm_addr` folding ANY 0x string of 4+ chars was the shift-594 residual."""
    if not isinstance(s, str) or len(s) != 42 or not s.startswith("0x"):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s[2:])


def to_checksum(addr):
    """EIP-55 render. Input may be any case; output is the canonical mixed-case form."""
    if not is_evm(addr):
        raise ValueError("not an EVM address: %r" % (addr,))
    low = addr[2:].lower()
    h = keccak256(low).hex()
    return "0x" + "".join(
        c.upper() if (c in "abcdef" and int(h[i], 16) >= 8) else c
        for i, c in enumerate(low)
    )


def classify(addr):
    """Which of the four states this occurrence is in. Only INVALID is a finding."""
    if not is_evm(addr):
        return NOT_EVM
    body = addr[2:]
    if body == body.lower():
        return ALL_LOWER
    if body == body.upper():
        return ALL_UPPER
    return VALID if addr == to_checksum(addr) else INVALID


def same_address(a, b):
    """True iff a and b are the SAME 20-byte EVM address. Case is a checksum, not identity --
    so this is a POSITIVE resolution, never a withhold. Returns None if either is not EVM."""
    if not (is_evm(a) and is_evm(b)):
        return None
    return a[2:].lower() == b[2:].lower()


# ---------------------------------------------------------------- selftest
def selftest():
    import hashlib
    ok = fail = 0

    def chk(cond, label):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("  FAIL", label)

    # --- 1. the instrument, against published Keccak-256 vectors, BEFORE any corpus
    chk(keccak256(b"").hex() ==
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        "keccak256('') vector")
    chk(keccak256(b"abc").hex() ==
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        "keccak256('abc') vector")
    # a 2-block input, so the sponge's absorb loop is exercised, not just one permutation
    chk(len(keccak256(b"a" * 300).hex()) == 64, "keccak256 multi-block returns 32 bytes")
    chk(keccak256(b"a" * 136) != keccak256(b"a" * 137), "keccak256 rate-boundary distinguishes")

    # --- 2. THE TRAP LEG: NIST SHA3-256 is a DIFFERENT function. If a future edit swaps
    #        hashlib.sha3_256 in for keccak256, this goes red instead of indicting every host.
    chk(keccak256(b"").hex() != hashlib.sha3_256(b"").hexdigest(),
        "keccak256 != NIST sha3_256 (padding domain 0x01 vs 0x06)")
    chk(hashlib.sha3_256(b"").hexdigest() ==
        "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
        "control: stdlib sha3_256 is the NIST function we must NOT use")

    # --- 3. the four reference addresses from EIP-55 itself
    for a in ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
              "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
              "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
              "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb"):
        chk(to_checksum(a.lower()) == a, "EIP-55 vector renders %s" % a[:10])
        chk(classify(a) == VALID, "EIP-55 vector classifies VALID %s" % a[:10])

    # --- 4. a reachable witness, not a constructed one: a widely-deployed Base token address
    #        that occurs in the manifests this repo reads. The claim asserted is ONLY that the
    #        mixed-case rendering re-derives under EIP-55 -- NOT which contract it is. This file
    #        has no way to resolve a contract's identity and never claims to.
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    chk(to_checksum(usdc.lower()) == usdc, "live Base token address re-renders under EIP-55")
    chk(classify(usdc) == VALID, "live Base token address classifies CHECKSUM_VALID")

    # --- 5. the four states are reachable and distinct
    chk(classify(usdc.lower()) == ALL_LOWER, "all-lowercase asserts no checksum")
    chk(classify("0x" + usdc[2:].upper()) == ALL_UPPER, "all-uppercase asserts no checksum")
    # mutation: flip ONE letter's case on a valid address -> INVALID, and nothing else changes
    body = list(usdc[2:])
    flipped = None
    for i, c in enumerate(body):
        if c.isalpha():
            body[i] = c.lower() if c.isupper() else c.upper()
            flipped = "0x" + "".join(body)
            break
    chk(flipped is not None and classify(flipped) == INVALID,
        "one case flip on a valid address -> CHECKSUM_INVALID")
    chk(same_address(flipped, usdc) is True,
        "a broken checksum is still the SAME address (positive resolution, not a withhold)")

    # --- 6. the predicate's boundaries -- these are where shift 594's residual lived
    chk(classify("0x6a66...43fc") == NOT_EVM, "an ELIDED value is not an EVM address")
    chk(classify("0x1234") == NOT_EVM, "a short 0x string is not an EVM address")
    chk(classify("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") == NOT_EVM,
        "a base58 Solana mint is not an EVM address (NO checksum claim is made about it)")
    chk(same_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                     "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v") is None,
        "same_address REFUSES base58 -- case there is identity, not a checksum")
    chk(classify(None) == NOT_EVM and classify(12) == NOT_EVM, "non-str is NOT_EVM, not a crash")
    chk(classify("0x" + "g" * 40) == NOT_EVM, "non-hex body is not an EVM address")
    try:
        to_checksum("nope")
        chk(False, "to_checksum must REFUSE a non-address")
    except ValueError:
        chk(True, "to_checksum refuses a non-address")

    # --- 7. idempotence and case-insensitivity of the render
    chk(to_checksum(usdc) == usdc, "render is idempotent on an already-checksummed address")
    chk(to_checksum("0x" + usdc[2:].upper()) == usdc, "render ignores input case")

    print("eip55 selftest: %d ok / %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip().splitlines()[0])
        sys.exit(2)
    for a in args:
        v = classify(a)
        extra = ""
        if v == INVALID:
            extra = "   canonical=%s" % to_checksum(a)
        print("%-18s %s%s" % (v, a, extra))
