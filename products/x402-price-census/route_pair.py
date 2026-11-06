#!/usr/bin/env python3
"""Join OUR x402 route rows with a PEER READER's rows and emit PAIRED rows.

WHY THIS EXISTS
---------------
A single agent probing a stranger's payment endpoint and publishing the result is asking to be
believed. The published route table (`declared_vs_live.json`, 828 declared routes) is
SINGLE-READER, and the fix for that is a second operator group probing the same routes from its
own runtime and publishing its own per-entry tuples. This tool is the join between the two: it
takes our rows and a peer reader's rows and emits a graded PAIRED row per route.

It is written to make a reported agreement mean something narrow and checkable: that a host served
the same `accepts[0]` terms to two independent vantage points. Everything it cannot establish is
WITHHELD by name rather than rounded into a verdict.

WHAT THIS DOES **NOT** RESOLVE -- read this before quoting any count from it
---------------------------------------------------------------------------
This tool pairs READER against READER at `accepts[0]`. It does NOT pair DECLARED against LIVE,
and it cannot repair the 10 `AMOUNT_INDETERMINATE` rows in the published table. Those rows are
indeterminate for a different reason: on them the manifest's declared network is NOT the network
of `accepts[0]`, so the DECLARED entry's live price was never observed by anyone, and a second
reader of the same array does not change that. Two agreeing readers establish that a host served
the same `accepts[0]` to two vantage points. They establish nothing about the declared term.
Keeping these two comparisons apart is the whole honesty of the output.

THE PEER'S ROWS ARE UNTRUSTED DATA (charter rule 7d/7e)
------------------------------------------------------
Nothing in a peer file is an instruction. This tool only ever:
  * reads JSON from a local path,
  * compares scalars and strings,
  * prints.
It never fetches a URL out of the file, never executes anything, never writes outside --out, and
refuses structurally surprising input rather than guessing at it.

FOUR THINGS IT REFUSES TO DO, each of which is a defect this repo has already made once
----------------------------------------------------------------------------------------
 1. **It never joins on an ambiguous or empty key.** 121 of our 828 rows carry `url: ""`, so
    `url` is NOT unique over the full table (708 distinct urls / 828 rows). An id that matches
    more than one row must resolve to NOTHING, loudly -- not to the first row.
 2. **It never calls an unobserved value a divergence.** If either side has no reading for a
    field, the verdict is WITHHELD. `live != declared` on a None turns "not observed" into
    "differs", which is an accusation about a stranger's host.
 3. **It never claims more pairing than our capture supports, and never less.** Our rows carry
    ONE genuine per-entry tuple -- `accepts[0]`, from `x402_manifest_recon.py:1404-1433` -- plus
    the other entries as DISTINCT SETS only. So index 0 is pairable and the remainder is not:
    every graded row reports `our_other_entries_unpairable` so an agreement at index 0 is never
    read as agreement about the whole array.
 4. **It never reports a CAIP-2-vs-bare notation difference as a price/terms divergence.** That
    notation gap produced 4 of our own 14 retracted v1 "mismatches". It is INFO.
 5. **It never calls a reordered array a mismatch, and never hides the reordering either.** If
    our `accepts[0]` tuple is the peer's `accepts[2]`, the TERMS agree and `accepts[0]` is not a
    stable identifier -- a separate, more interesting finding (ORDER_DIFFERS). And if our chain
    is absent from the peer's array entirely, that is a different OFFER SET, not a misprice.

Usage:
    python3 bin/route_pair.py --peer <peer_rows.json> [--ours <declared_vs_live.json>]
                              [--out <paired.json>]
    python3 bin/route_pair.py --selftest        # pure functions, no network, no credential

PEER ROW SCHEMA (this is what we ask the peer to emit; see --schema)
    {"reader": "<name>", "probed_at": "<ISO8601>", "rows": [
        {"url": "<exact route url>", "http": 402,
         "accepts": [{"amount": "<atomic string>", "asset": "<addr>",
                      "network": "<base | eip155:8453 | ...>", "payTo": "<addr>"}, ...]}]}
    `accepts` MUST be per-entry tuples in the order the host served them. A row with no 402 may
    carry "accepts": [] and its own `http`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eip55  # sibling: pure-python keccak256 + EIP-55 (no eth deps in this env)

HERE = os.path.dirname(os.path.abspath(__file__))
# Two layouts resolve a bare `--peer` run with no `--ours`, and the SIBLING one is tried first
# because it is the layout a stranger has. In a published checkout this file sits next to
# declared_vs_live.json; in the studio's own tree it sits in bin/ and the table is under
# products/. A path that only resolves in our tree would make the published tool unrunnable
# without an argument the README does not mention, so both are candidates and the chosen one is
# PRINTED by main() -- a default input must say which file it read.
_OURS_CANDIDATES = (
    os.path.join(HERE, "declared_vs_live.json"),                       # published: side by side
    os.path.join(os.path.dirname(HERE), "products", "x402-price-census",
                 "declared_vs_live.json"),                             # this repo's own layout
)


def default_ours(candidates=_OURS_CANDIDATES):
    """The first candidate that EXISTS, else the last one so the error names a real path.

    Never invents a path and never silently reads a different file than the one it reports:
    main() prints whatever this returns."""
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[-1]

# Networks that are the SAME chain under two notations. Both directions, canonical = CAIP-2.
#
# ⚠ THE FIRST VERSION OF THIS TABLE WAS DECORATIVE (critic, shift 593). It folded `base`,
# `base-sepolia` and `ethereum` -- and all three occur **0 times** in declared_vs_live.json
# (measured: `grep -c '"base"'` → 0). Meanwhile the ONE notation pair that actually occurs in our
# data was absent: `solana` (4 rows, mpp.hyreagent.fun) against
# `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` (47620.xyz, and 471 declared rows). Our own census
# already has a verdict for that pair -- AMOUNT_OK_NETWORK_NOTATION, 4 rows -- so the gap was not
# hypothetical: a peer who canonicalised one way while the host served the other would have been
# reported as OFFER_SET_DIFFERS, a false finding about a named third party.
#
# The eip155 entries are kept because a peer may canonicalise a bare name we never saw; the
# solana pair is the one our own bytes require. `base` = 8453 and `base-sepolia` = 84532 per
# CAIP-2; Solana mainnet's CAIP-2 reference is its genesis-hash prefix.
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
NETWORK_ALIASES = {
    "base": "eip155:8453",
    "eip155:8453": "eip155:8453",
    "base-sepolia": "eip155:84532",
    "base-sepolia-testnet": "eip155:84532",
    "eip155:84532": "eip155:84532",
    "ethereum": "eip155:1",
    "eip155:1": "eip155:1",
    "solana": SOLANA_MAINNET,
    "solana-mainnet": SOLANA_MAINNET,
    "mainnet-beta": SOLANA_MAINNET,
    SOLANA_MAINNET.lower(): SOLANA_MAINNET,
}


# ------------------------------------------------------------------ pure: normalisation
def norm_network(value):
    """Return (canonical, notation). notation is 'caip2' | 'bare' | 'unknown'.

    An unrecognised value is returned LOWERCASED with notation 'unknown' and is never silently
    mapped onto a known chain -- two unknowns that differ are still a real difference, and an
    unknown vs a known one is not comparable."""
    if value is None:
        return None, "unknown"
    if not isinstance(value, str):
        return None, "unknown"
    v = value.strip().lower()
    if not v:
        return None, "unknown"
    canon = NETWORK_ALIASES.get(v)
    if canon is None:
        return v, "unknown"
    return canon, ("caip2" if ":" in v else "bare")


def norm_addr(value):
    """Return (normalised, comparable). EVM 0x addresses are case-insensitive (EIP-55 checksum is
    only casing) so they fold safely. Anything else -- notably base58 Solana -- is CASE-SENSITIVE
    and is returned unfolded; comparing it after a fold would be lossy, which is a limit our own
    census carries."""
    if not isinstance(value, str) or not value.strip():
        return None, False
    v = value.strip()
    if v.lower().startswith("0x") and len(v) >= 4:
        return v.lower(), True
    return v, True


CASE_SENSITIVE_FOLD_NOTE = (
    "our census case-folds this notation, so case-only inequality is not assertable")


def addr_compare(ours_v, peer_v):
    """Compare a DECLARED address out of OUR census against a PEER's byte-exact reading.

    Returns one of "AGREE", "DIFFER", "FOLD_UNCOMPARABLE", "UNOBSERVED".

    WHY "FOLD_UNCOMPARABLE" EXISTS -- shift 594, measured on Penny's real 43 rows
    ------------------------------------------------------------------------------
    `declared_vs_live.json` limit 3 says it in its own words: "Addresses here are case-folded.
    That is lossless for EVM ... and LOSSY for base58 Solana addresses, which are case-sensitive".
    The first run of this join ignored that and compared a known case-folded declared value
    byte-exact against the peer's unfolded one. MEASURED on the real file: **46 `DIFFERS` claims
    across 23 of the 43 rows, and ZERO of them was a real difference** -- every single pair was
    case-fold-equal, e.g. declared `epjfwdd5...dt1v` vs the peer's
    `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` -- the same base58 string in two renderings.
    (Which token that address is is not a claim this tool makes or can check; what matters here
    is only that the two strings are fold-equal.) Publishing those 46 DIFFERS would have been 46
    false accusations against four named hosts, where the reporting standard is aggregate counts
    rather than named accusations.

    THE PREDICATE, and why DIFFER is still assertable in one branch
    --------------------------------------------------------------
    A case fold can only ever make two strings LOOK equal; it can never make two strings that
    differ under folding look different. So:
      byte-equal                        -> AGREE                (safe)
      fold-equal but not byte-equal     -> FOLD_UNCOMPARABLE    (our side lost the case)
      fold-different                    -> DIFFER               (still sound: no fold explains it)
    0x values are folded on both sides first (EIP-55 checksum is only casing), so they can never
    reach the FOLD_UNCOMPARABLE branch -- a 0x pair is either AGREE or DIFFER.
    """
    # An ELIDED value is not an observation. CRITIC, SHIFT 594: `norm_addr` folds any
    # `0x`-prefixed string of 4+ chars, so a peer who abbreviates a payee as `0x6a66...43fc`
    # would be fold-DIFFERENT from our full value and get CONVICTED. Penny's file happens to
    # carry 0 elided asset/payTo values (measured), so no live claim depended on this -- but the
    # next peer file is not ours to control, and a false accusation is the one output class this
    # whole tool exists to avoid.
    for _v in (ours_v, peer_v):
        if isinstance(_v, str) and ("..." in _v or "\u2026" in _v):
            return "UNOBSERVED"
    o_norm, o_cmp = norm_addr(ours_v)
    p_norm, p_cmp = norm_addr(peer_v)
    if not o_cmp or not p_cmp or o_norm is None or p_norm is None:
        return "UNOBSERVED"
    if o_norm == p_norm:
        return "AGREE"
    if o_norm.lower() == p_norm.lower():
        return "FOLD_UNCOMPARABLE"
    return "DIFFER"


def addr_checksum_note(ours_v, peer_v):
    """EIP-55 case as a REPORTED NOTE, never a verdict -- the shift-595/596 finding.

    `norm_addr` folds any 0x value before comparison, so a mis-cased EVM address lands in
    `addr_compare`'s AGREE branch. That is CORRECT about identity -- for a 20-byte EVM address the
    40 hex digits ARE the address, so two fold-equal values are the same address with certainty --
    and WRONG about the CLAIM: the case pattern is a separate, self-verifying assertion the server
    made, and folding first discards it. The failure mode was therefore not a false conviction but
    a **false CLEAR**: agreement reported, finding thrown away. The branch is REACHABLE and not
    hypothetical: one live manifest in our own corpus served an EVM asset value whose mixed-case
    rendering does not re-derive under EIP-55. The host is deliberately NOT named here -- a
    rendering that does not re-derive is a minor, reproducible property of one public document,
    not a finding worth attaching a company's name to in a tool's docstring, and this repo names
    a third party only as the subject of a published, reproducible row.

    NEVER returns a difference and NEVER feeds `rank`: identity is settled by `addr_compare`, and
    widening a comparison is how a real defect ships (shift 594). It reports only
    CHECKSUM_INVALID, and only on a side that actually asserted a checksum -- a mixed-case value.
    An ALL_LOWER value asserts nothing, and on OUR declared column it is our own fold, so our side
    is structurally unable to produce a finding here; in practice the note is always about the
    peer's byte-exact read.

    SCOPE OF A HIT: the rendered case does not re-derive. NOT that the address is wrong (the same
    20 bytes), and NOT that a payment would fail -- untested, and no validating client is installed
    in this environment. Returns None when there is nothing to report.
    """
    out = []
    for who, v in (("declared", ours_v), ("peer-read", peer_v)):
        if not isinstance(v, str):
            continue
        v = v.strip()
        # An elided value is not an observation (same guard as addr_compare). MEASURED, shift 596:
        # this guard is NOT load-bearing today -- removing it leaves the selftest 161/0 green,
        # because `classify` already returns NOT_EVM for base58 and for an elided `0x8335...2913`,
        # and only INVALID is reported. It is kept as defence in depth against a future loosening
        # of is_evm/classify, and the legs below pin that mechanism so the controls are not passing
        # for a reason nobody wrote down.
        if "..." in v or "\u2026" in v or not eip55.is_evm(v):
            continue
        if eip55.classify(v) == eip55.INVALID:
            out.append("%s %s does not re-render under EIP-55 (canonical %s)"
                       % (who, v, eip55.to_checksum(v)))
    return "; ".join(out) if out else None


def amounts_of(accepts):
    """Per-entry amount strings, in served order. None entries are dropped and counted by the
    caller via len() against the raw list -- this returns only what was actually observed."""
    out = []
    for e in accepts or []:
        if not isinstance(e, dict):
            continue
        a = e.get("amount")
        if a is None:
            a = e.get("maxAmountRequired")
        if a is None:
            continue
        out.append(str(a).strip())
    return out


# ------------------------------------------------------------------ pure: indexing
def index_ours(routes):
    """url -> row, refusing empty and ambiguous keys.

    Returns (index, dropped_empty, ambiguous_urls). An ambiguous url is present in NEITHER the
    index nor usable: it resolves to nothing. Both counts are reported so an absence is never
    read as 'the peer sent a url we do not have'."""
    seen, amb = {}, set()
    dropped_empty = 0
    for r in routes or []:
        if not isinstance(r, dict):
            continue
        u = r.get("url")
        if not isinstance(u, str) or not u.strip():
            dropped_empty += 1
            continue
        u = u.strip()
        if u in seen:
            amb.add(u)
            continue
        seen[u] = r
    for u in amb:
        seen.pop(u, None)
    return seen, dropped_empty, sorted(amb)


# ------------------------------------------------------------------ pure: the join verdict
# WHAT OUR SIDE ACTUALLY HOLDS, and it is better than `limits` suggests (measured shift 593):
# `bin/x402_manifest_recon.py:1404` sets `live = _first_accept(parsed)` and lines 1430-1433 fill
# `live_amount` / `live_asset` / `live_network` / `live_payto` from THAT ONE OBJECT. So every row
# carries one genuine PER-ENTRY tuple -- **accepts[0]** -- and the source says so in its own
# comment: "`verdict` still reconciles accepts[0] only". The SET fields (`live_amounts`,
# `live_networks`, `live_assets`) are the lossy part: they prove which terms were present
# SOMEWHERE in the array without saying which entry carried which.
#
# That changes what two readers can establish. A single reader cannot tell whether `accepts[0]`
# is a STABLE identifier for a route, because it has only one observation of the order. Two
# readers can: if our accepts[0] tuple appears in the peer's array at a different index, then
# accepts[0] is not stable -- and OUR OWN published verdict reconciles accepts[0] only, as does
# every x402 client that reads the first entry. That is the finding this join exists to reach,
# so ORDER_DIFFERS is a first-class verdict and is NEVER folded into a divergence.

VERDICTS = (
    "INDEX0_AGREE",                # same (chain, amount) at index 0, same notation
    "INDEX0_AGREE_NOTATION_ONLY",  # same chain and amount at index 0, CAIP-2 vs bare -- INFO
    "ORDER_DIFFERS",               # our accepts[0] tuple is in the peer's array at index > 0
    "TERMS_DIVERGENT",             # our chain IS in the peer's array, at a different amount
    "OFFER_SET_DIFFERS",           # our accepts[0] chain is absent from the peer's array
    "NETWORK_NOTATION_UNCOMPARABLE",  # a network name this table does not recognise -> no claim
    "WITHHELD_NO_OBSERVATION",     # one or both sides did not observe -> no claim at all
)

# A row's DECLARED terms can be resolved by the peer even when OUR capture cannot resolve them.
# This is a SEPARATE comparison from the reader-vs-reader one above and is reported separately.
DECLARED_VERDICTS = (
    "DECLARED_RESOLVED_BY_PEER",   # the peer priced the declared (network, amount) per-entry
    "DECLARED_DIVERGENT_PER_PEER", # the peer read the declared network at a DIFFERENT amount
    "DECLARED_NOT_IN_PEER_ARRAY",  # the declared network is absent from the peer's array
    "DECLARED_UNRESOLVED",         # not enough observed on either side to say anything
)


def peer_entries(peer):
    """[(canonical_network, notation, amount, raw_entry)] in SERVED ORDER from a peer row.

    THE SINGLE extraction predicate. `declared_vs_peer` originally kept its own copy of this
    loop, so a leg proving `peer_tuples` drops a half-observed entry proved nothing about the
    declared path (mutation run, shift 593). A duplicated predicate is two predicates, and the
    second one is the one that rots -- so there is exactly one, and both callers use it.

    Entries missing either half are dropped: a tuple with an unobserved member cannot be
    compared, and substituting a default would invent an observation."""
    out = []
    for e in (peer.get("accepts") or []):
        if not isinstance(e, dict):
            continue
        amt = e.get("amount")
        if amt is None:
            amt = e.get("maxAmountRequired")
        canon, nota = norm_network(e.get("network"))
        if amt is None or canon is None:
            continue
        out.append((canon, nota, str(amt).strip(), e))
    return out


def peer_tuples(peer):
    """[(canonical_network, notation, amount)] -- `peer_entries` without the raw entry."""
    return [(c, n, a) for c, n, a, _e in peer_entries(peer)]


def pair_row(our, peer):
    """Grade ONE paired row on the per-entry tuple both sides actually hold.

    Returns (verdict, reason, detail). Every verdict is in VERDICTS. No verdict other than
    WITHHELD_NO_OBSERVATION is ever returned when either side's operand was not observed."""
    our_http, peer_http = our.get("live_http"), peer.get("http")
    if our_http is None or peer_http is None:
        return ("WITHHELD_NO_OBSERVATION",
                "no live reading on %s side -- not observed is not a difference"
                % ("our" if our_http is None else "the peer's"), {})

    our_amt = our.get("live_amount")
    our_canon, our_nota = norm_network(our.get("live_network"))
    ptups = peer_tuples(peer)

    # Neither reader saw a priced entry. Only a manifest that DECLARES free turns that into an
    # agreement; otherwise it is two absences, which agree about nothing.
    if (our_amt in (None, "")) and not ptups:
        if our.get("declared_free") and peer_http != 402:
            return ("INDEX0_AGREE",
                    "both readers saw no payment challenge on a route the manifest declares free",
                    {"our_http": our_http, "peer_http": peer_http})
        return ("WITHHELD_NO_OBSERVATION",
                "neither reader captured a comparable priced entry", {})
    if our_amt in (None, "") or our_canon is None:
        return ("WITHHELD_NO_OBSERVATION",
                "our accepts[0] tuple is incomplete (amount=%r network=%r) -- withheld rather "
                "than compared" % (our_amt, our.get("live_network")), {})
    if not ptups:
        return ("WITHHELD_NO_OBSERVATION",
                "the peer captured no entry carrying BOTH a network and an amount", {})

    our_amt = str(our_amt).strip()
    # How much of OUR row is structurally unpairable: we hold accepts[0] per-entry, and the rest
    # only as sets. This is reported on every row so an agreement at index 0 is never read as
    # agreement about the whole array.
    our_set = sorted({str(x) for x in (our.get("live_amounts") or []) if str(x)})
    # ⚠ This counts ENTRIES, from `live_accepts_n`, NOT distinct amounts. The first version used
    # len(distinct amounts) - 1 and reported "1 unpairable" on a row whose `live_accepts_n` was 6
    # (critic, shift 593): five entries we cannot pair, described as one.
    n_entries = our.get("live_accepts_n")
    detail = {"our_index0": {"network": our_canon, "notation": our_nota, "amount": our_amt},
              "peer_tuples": [{"network": c, "notation": n, "amount": a} for c, n, a in ptups],
              "our_other_entries_unpairable": (max(0, n_entries - 1)
                                               if isinstance(n_entries, int) else None),
              "our_live_accepts_n": n_entries,
              "our_amount_set": our_set}

    p0_canon, p0_nota, p0_amt = ptups[0]
    if (p0_canon, p0_amt) == (our_canon, our_amt):
        if our_nota != "unknown" and p0_nota != "unknown" and our_nota != p0_nota:
            return ("INDEX0_AGREE_NOTATION_ONLY",
                    "accepts[0] agrees: chain %s, amount %s. Written %s by us and %s by the peer "
                    "-- INFO, not a divergence" % (our_canon, our_amt, our_nota, p0_nota), detail)
        return ("INDEX0_AGREE",
                "accepts[0] agrees: chain %s, amount %s" % (our_canon, our_amt), detail)

    # Our tuple elsewhere in the peer's array -> the ORDER moved, the terms did not.
    for i, (c, _n, a) in enumerate(ptups):
        if (c, a) == (our_canon, our_amt):
            return ("ORDER_DIFFERS",
                    "our accepts[0] tuple (chain %s, amount %s) is the peer's accepts[%d] -- the "
                    "terms agree but accepts[0] is NOT a stable identifier for this route, and "
                    "our own verdict reconciles accepts[0] only"
                    % (our_canon, our_amt, i), detail)

    # Same chain present, different amount -> a real divergence about price.
    same_chain = sorted({a for c, _n, a in ptups if c == our_canon})
    if same_chain:
        return ("TERMS_DIVERGENT",
                "chain %s: we read amount %s, the peer read %s"
                % (our_canon, our_amt, same_chain), detail)

    # An ACCUSATION REQUIRES BOTH NETWORK NAMES TO BE RECOGNISED. If either side's name is not in
    # NETWORK_ALIASES we do not know whether two spellings are the same chain, so OFFER_SET_DIFFERS
    # would be a false finding about a named host -- exactly the defect the first version of this
    # tool shipped with (critic, shift 593). Withhold instead and say which name was unknown.
    unknown = sorted({n for n in [our.get("live_network")] if our_nota == "unknown"}
                     | {e.get("network") for e in (peer.get("accepts") or [])
                        if isinstance(e, dict) and norm_network(e.get("network"))[1] == "unknown"
                        and e.get("network")})
    if unknown:
        return ("NETWORK_NOTATION_UNCOMPARABLE",
                "network name(s) %s are not in this tool's alias table, so two spellings cannot "
                "be shown to be the same chain or different ones -- no claim made about the host"
                % unknown, detail)

    # Our chain absent from the peer's array, both names recognised. That is a different OFFER
    # SET, not a misprice: it is what a different vantage point or a redeploy between the two
    # windows looks like, and calling it a mismatch would be an accusation the data does not
    # support.
    return ("OFFER_SET_DIFFERS",
            "our accepts[0] chain %s does not appear in the peer's array at all (peer chains: "
            "%s) -- a different offer set, NOT a misprice"
            % (our_canon, sorted({c for c, _n, _a in ptups})), detail)


def declared_vs_peer(our, peer):
    """The comparison OUR CAPTURE CANNOT MAKE AND THE PEER'S CAN. Returns (verdict, reason).

    WHY THIS EXISTS (critic, shift 593). The 10 `AMOUNT_INDETERMINATE` rows are not unresolvable
    -- the first version of this tool, and the message that nearly went with it, said they were.
    Their own bytes say otherwise: every one of them carries
    `declared_terms_present {"amount": true, "asset": true, "network": true}` and
    `live_accepts_n: 6`. All three declared terms ARE present somewhere in the live array; what
    our capture lost is only WHICH ENTRY carried them, because we stored sets.

    A peer sending PER-ENTRY tuples supplies exactly the missing mapping. So these rows become
    two-reader rows rather than staying indeterminate, and telling the peer not to bother would
    have thrown away the single most useful thing she can send.

    This grades DECLARED against the PEER's entries. It is NOT the reader-vs-reader verdict and
    is never mixed into those counts."""
    d_amt, d_net = our.get("declared_amount"), our.get("declared_network")
    d_asset, d_payto = our.get("declared_asset"), our.get("declared_payto")
    if d_amt in (None, "") or not d_net:
        return ("DECLARED_UNRESOLVED", "the manifest declares no comparable (network, amount)")
    d_canon, _d_nota = norm_network(d_net)
    if d_canon is None:
        return ("DECLARED_UNRESOLVED", "declared network %r is not a readable name" % d_net)
    # ONE derivation, used by every branch below. An earlier version called `peer_tuples` here
    # for the emptiness gate and `peer_entries` again further down -- two derivations of the same
    # list, so a mutation to the extraction was masked by the gate (measured, shift 593).
    entries = [(c, a, e) for c, _n, a, e in peer_entries(peer)]
    if not entries:
        return ("DECLARED_UNRESOLVED",
                "the peer captured no entry carrying both a network and an amount")
    if norm_network(d_net)[1] == "unknown":
        return ("DECLARED_UNRESOLVED",
                "declared network %r is not in this tool's alias table, so it cannot be matched "
                "against the peer's spelling without guessing" % d_net)

    d_amt = str(d_amt).strip()
    # ⚠ PAIR BEFORE YOU COMPARE. Everything below is bound to a SINGLE peer ENTRY, never
    # assembled across entries. The first version of this function collected the peer's
    # asset/payTo values from EVERY entry on the declared chain and then asked for membership,
    # so a peer serving (solana, 10000, assetZ, paytoZ) and (solana, 55555, assetA, paytoA)
    # against a manifest declaring (solana, 10000, assetA, paytoA) was graded
    # "priced at 10000, matching; asset agrees; payTo agrees" -- an agreement FABRICATED from two
    # different offers. Measured on exactly that fixture, shift 593. Shift 591 shipped the same
    # family of defect one level below this one.
    same_chain = [t for t in entries if t[0] == d_canon]
    if not same_chain:
        return ("DECLARED_NOT_IN_PEER_ARRAY",
                "the declared chain %s does not appear in the peer's array (peer chains: %s) -- "
                "an absence from ONE reading, not a finding about the host"
                % (d_canon, sorted({c for c, _a, _e in entries})))
    amts = sorted({a for _c, a, _e in same_chain})
    priced = [t for t in same_chain if t[1] == d_amt]
    if not priced:
        return ("DECLARED_DIVERGENT_PER_PEER",
                "the manifest declares %s on %s and the peer read %s on that chain"
                % (d_amt, d_canon, amts))

    # One or more entries match chain AND amount. asset/payTo are compared WITHIN such an entry.
    # EVM addresses are case-folded, base58 is not (`norm_addr`).
    best, best_rank, best_notes = None, None, None
    for _c, _a, e in priced:
        notes, rank = [], 0
        for label, ours_v, key in (("asset", d_asset, "asset"), ("payTo", d_payto, "payTo")):
            if not ours_v:
                continue
            o_norm, _o_cmp = norm_addr(ours_v)
            pv, _p_cmp = norm_addr(e.get(key))
            cmpv = addr_compare(ours_v, e.get(key))
            if cmpv == "UNOBSERVED":
                notes.append("%s not compared (one side unobserved)" % label)
                rank += 1
            elif cmpv == "AGREE":
                notes.append("%s agrees" % label)
            elif cmpv == "FOLD_UNCOMPARABLE":
                notes.append("%s not compared (%s; declared %s, this entry %s, equal under "
                             "case-folding)" % (label, CASE_SENSITIVE_FOLD_NOTE, o_norm, pv))
                rank += 1
            else:
                notes.append("%s DIFFERS (declared %s, this entry %s)" % (label, o_norm, pv))
                rank += 2
        if best_rank is None or rank < best_rank:
            best, best_rank, best_notes = e, rank, notes
    extra = list(best_notes or [])
    # EIP-55 case, reported for the entry actually REPORTED and never ranked (addr_checksum_note).
    # Placed after selection on purpose: a checksum note must not influence which entry is chosen.
    for _lbl, _ours, _key in (("asset", d_asset, "asset"), ("payTo", d_payto, "payTo")):
        _n = addr_checksum_note(_ours, (best or {}).get(_key))
        if _n:
            extra.append("%s checksum NOTE: %s" % (_lbl, _n))
    if len(priced) > 1:
        extra.append("%d peer entries carry that chain and amount; the one reported is the "
                     "closest match on asset/payTo" % len(priced))
    return ("DECLARED_RESOLVED_BY_PEER",
            "the peer priced the declared chain %s at %s in ONE entry, matching the manifest; %s"
            % (d_canon, d_amt, "; ".join(extra) if extra else "no asset/payTo declared"))


# ------------------------------------------------------------------ pure: the whole join
def join(our_routes, peer_doc):
    """Return a result dict. Pure: no I/O."""
    idx, dropped_empty, ambiguous = index_ours(our_routes)
    peer_rows = (peer_doc or {}).get("rows")
    if not isinstance(peer_rows, list):
        return {"error": "peer document has no `rows` list -- refusing to guess at its shape"}

    paired, peer_only, bad = [], [], []
    peer_seen = set()
    for pr in peer_rows:
        if not isinstance(pr, dict):
            bad.append("non-object row")
            continue
        u = pr.get("url")
        if not isinstance(u, str) or not u.strip():
            bad.append("row with no url")
            continue
        u = u.strip()
        if u in peer_seen:
            bad.append("duplicate peer url: %s" % u)
            continue
        peer_seen.add(u)
        if u in ambiguous:
            bad.append("url is AMBIGUOUS in our table (>1 row) -- resolves to nothing: %s" % u)
            continue
        our = idx.get(u)
        if our is None:
            peer_only.append(u)
            continue
        v, why, detail = pair_row(our, pr)
        dv, dwhy = declared_vs_peer(our, pr)
        paired.append({"url": u, "host": our.get("host"), "verdict": v, "reason": why,
                       "declared_verdict": dv, "declared_reason": dwhy,
                       "our_verdict": our.get("verdict"), "detail": detail})

    ours_probed = [r for r in our_routes if isinstance(r, dict) and r.get("live_http") is not None]
    ours_only = sorted({r["url"] for r in ours_probed
                        if isinstance(r.get("url"), str) and r["url"].strip()
                        and r["url"].strip() not in peer_seen})
    counts, dcounts = {}, {}
    for p in paired:
        counts[p["verdict"]] = counts.get(p["verdict"], 0) + 1
        dcounts[p["declared_verdict"]] = dcounts.get(p["declared_verdict"], 0) + 1
    for v in DECLARED_VERDICTS:
        dcounts.setdefault(v, 0)
    # Every emittable verdict is named even at zero rows: a Counter cannot carry a zero, and a
    # missing key reads as "never happened" rather than "happened zero times".
    for v in VERDICTS:
        counts.setdefault(v, 0)
    return {
        "reader": (peer_doc or {}).get("reader"),
        "peer_probed_at": (peer_doc or {}).get("probed_at"),
        "paired": paired,
        "verdict_counts": counts,
        "declared_verdict_counts": dcounts,
        "rows_our_capture_could_not_resolve_that_the_peer_did": sorted(
            p["url"] for p in paired
            if p["our_verdict"] == "AMOUNT_INDETERMINATE"
            and p["declared_verdict"] in ("DECLARED_RESOLVED_BY_PEER",
                                          "DECLARED_DIVERGENT_PER_PEER")),
        "peer_only_urls": sorted(peer_only),
        "our_probed_urls_the_peer_did_not_read": ours_only,
        "our_index": {"joinable_urls": len(idx), "rows_with_empty_url_dropped": dropped_empty,
                      "ambiguous_urls_resolved_to_nothing": ambiguous},
        "malformed_peer_rows": bad,
        "two_reader_rows": sum(1 for p in paired
                               if p["verdict"] != "WITHHELD_NO_OBSERVATION"),
    }


SCHEMA_TEXT = """PEER ROW SCHEMA -- emit exactly this and the join is mechanical:
{"reader": "<your name>", "probed_at": "<ISO8601 UTC>", "rows": [
  {"url": "<the EXACT route url, byte-for-byte as it appears in declared_vs_live.json>",
   "http": 402,
   "accepts": [{"amount": "<atomic string, e.g. \\"10000\\">", "asset": "<token address>",
                "network": "<base | eip155:8453 | ...>", "payTo": "<address>"}]}]}
RULES THAT MAKE A ROW PAIRABLE:
 * `accepts` MUST be PER-ENTRY TUPLES in the SERVED ORDER. Order is load-bearing, not cosmetic:
   our published verdict reconciles `accepts[0]` only, so whether your `accepts[0]` is the same
   entry as ours is itself a finding, and it is one neither of us can reach alone.
 * Do NOT collapse the array into distinct sets of amounts/networks/assets. That is the defect
   that leaves the non-first entries of our own rows permanently unpairable.
 * `url` is the join key. A url we hold on more than one row resolves to NOTHING and is reported.
 * A route that answered without a 402 is still a useful row: send it with its real `http` and
   "accepts": [].
 * Never send a value you did not observe. Omit the field instead; the join WITHHOLDS rather
   than calling an absence a difference."""


# ------------------------------------------------------------------ selftest
def selftest():
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print("FAIL %s:\n  got  %r\n  want %r" % (name, got, want))

    # ---- norm_network ----
    chk("base folds to caip2", norm_network("base"), ("eip155:8453", "bare"))
    chk("eip155:8453 is caip2", norm_network("eip155:8453"), ("eip155:8453", "caip2"))
    chk("case and space insensitive", norm_network("  BASE "), ("eip155:8453", "bare"))
    chk("sepolia folds", norm_network("base-sepolia"), ("eip155:84532", "bare"))
    chk("unknown is NOT mapped onto a known chain", norm_network("cosmoshub-4"),
        ("cosmoshub-4", "unknown"))
    chk("the solana notation pair IS folded -- it is the one that occurs in our data",
        norm_network("solana")[0], norm_network(SOLANA_MAINNET)[0])
    chk("solana bare is marked bare, CAIP-2 form is marked caip2",
        (norm_network("solana")[1], norm_network(SOLANA_MAINNET)[1]), ("bare", "caip2"))
    chk("None network", norm_network(None), (None, "unknown"))
    chk("empty network", norm_network("   "), (None, "unknown"))
    chk("non-string network", norm_network(8453), (None, "unknown"))
    chk("mainnet and testnet do NOT fold together",
        norm_network("base")[0] == norm_network("base-sepolia")[0], False)

    # ---- norm_addr ----
    chk("EVM folds case", norm_addr("0xAbCd"), ("0xabcd", True))
    chk("base58 is NOT folded", norm_addr("So11111111111111111111111111111111111111112"),
        ("So11111111111111111111111111111111111111112", True))
    chk("empty addr", norm_addr(""), (None, False))
    chk("None addr", norm_addr(None), (None, False))

    # ---- amounts_of: per-entry, order preserved ----
    chk("per-entry order preserved",
        amounts_of([{"amount": "10000"}, {"amount": "101665"}]), ["10000", "101665"])
    chk("v1 field name accepted",
        amounts_of([{"maxAmountRequired": "1000"}]), ["1000"])
    chk("entry with no amount is dropped, not zeroed",
        amounts_of([{"amount": "5"}, {"asset": "0x1"}]), ["5"])
    chk("non-dict entry dropped", amounts_of(["10000"]), [])
    chk("None accepts", amounts_of(None), [])

    # ---- index_ours: the ambiguity + empty-key refusals ----
    rows = [{"url": "https://a/1", "host": "a"}, {"url": "", "host": "a"},
            {"url": "   ", "host": "a"},
            {"url": "https://a/2", "host": "a", "tag": "first"},
            {"url": "https://a/2", "host": "a", "tag": "second"}]
    idx, empt, amb = index_ours(rows)
    chk("empty urls dropped and counted", empt, 2)
    chk("ambiguous url reported", amb, ["https://a/2"])
    chk("ambiguous url resolves to NOTHING (not the first row)", "https://a/2" in idx, False)
    chk("unique url indexed", idx["https://a/1"]["host"], "a")
    chk("index holds only the unambiguous key", sorted(idx), ["https://a/1"])
    chk("None routes", index_ours(None), ({}, 0, []))

    # ---- pair_row: the accepts[0] tuple model ----------------------------------
    OURS_OK = {"live_http": 402, "live_amount": "10000", "live_network": "eip155:8453",
               "live_amounts": ["10000"], "live_networks": ["eip155:8453"],
               "verdict": "OK", "host": "h"}
    PEER_OK = {"http": 402, "accepts": [{"amount": "10000", "network": "eip155:8453"}]}
    chk("index0 agree", pair_row(OURS_OK, PEER_OK)[0], "INDEX0_AGREE")
    chk("notation only is NOT a divergence",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "10000", "network": "base"}]})[0],
        "INDEX0_AGREE_NOTATION_ONLY")
    chk("notation-only reason says INFO",
        "INFO, not a divergence" in pair_row(
            OURS_OK, {"http": 402, "accepts": [{"amount": "10000", "network": "base"}]})[1], True)
    # ORDER: the finding only two readers can reach
    order = pair_row(OURS_OK, {"http": 402, "accepts": [
        {"amount": "99999", "network": "eip155:137"},
        {"amount": "10000", "network": "eip155:8453"}]})
    chk("our tuple at the peer's index 1 -> ORDER_DIFFERS", order[0], "ORDER_DIFFERS")
    chk("ORDER_DIFFERS names the index it was found at", "accepts[1]" in order[1], True)
    chk("ORDER_DIFFERS says the terms AGREE", "the terms agree" in order[1], True)
    chk("ORDER_DIFFERS is never reported as a divergence", "DIVERGENT" in order[0], False)
    chk("order difference survives a notation difference",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "1", "network": "eip155:137"},
                                                    {"amount": "10000", "network": "base"}]})[0],
        "ORDER_DIFFERS")
    # DIVERGENT: same chain, different amount
    div = pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "20000", "network": "base"}]})
    chk("same chain different amount -> TERMS_DIVERGENT", div[0], "TERMS_DIVERGENT")
    chk("TERMS_DIVERGENT quotes both amounts",
        "10000" in div[1] and "20000" in div[1], True)
    # OFFER_SET_DIFFERS: our chain absent -> never an accusation
    off = pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "10000",
                                                       "network": "ethereum"}]})
    chk("our chain absent from the peer array -> OFFER_SET_DIFFERS", off[0], "OFFER_SET_DIFFERS")
    chk("OFFER_SET_DIFFERS explicitly denies a misprice", "NOT a misprice" in off[1], True)
    chk("OFFER_SET_DIFFERS is not a divergence", "DIVERGENT" in off[0], False)
    # REFUSALS: an absence is never a difference
    chk("no reading our side -> WITHHELD",
        pair_row({"live_http": None, "live_amount": None}, PEER_OK)[0],
        "WITHHELD_NO_OBSERVATION")
    chk("no reading peer side -> WITHHELD",
        pair_row(OURS_OK, {"http": None, "accepts": []})[0], "WITHHELD_NO_OBSERVATION")
    # These reach the HTTP guard specifically: terms are present on both sides, so no later guard
    # can answer. Without them the http guard could be deleted and the suite stayed green
    # (measured, shift 593).
    chk("our http unobserved but terms present -> WITHHELD via the http guard",
        pair_row(dict(OURS_OK, live_http=None), PEER_OK)[0], "WITHHELD_NO_OBSERVATION")
    chk("peer http unobserved but accepts present -> WITHHELD via the http guard",
        pair_row(OURS_OK, {"accepts": [{"amount": "10000", "network": "eip155:8453"}]})[0],
        "WITHHELD_NO_OBSERVATION")
    chk("the http guard names which side was unobserved",
        "our side" in pair_row(dict(OURS_OK, live_http=None), PEER_OK)[1], True)
    chk("our amount unobserved -> WITHHELD not DIVERGENT",
        pair_row(dict(OURS_OK, live_amount=None), PEER_OK)[0], "WITHHELD_NO_OBSERVATION")
    chk("our network unobserved -> WITHHELD not DIVERGENT",
        pair_row(dict(OURS_OK, live_network=None), PEER_OK)[0], "WITHHELD_NO_OBSERVATION")
    chk("peer sent an amount with NO network -> not comparable -> WITHHELD",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "10000"}]})[0],
        "WITHHELD_NO_OBSERVATION")
    chk("peer sent a network with NO amount -> not comparable -> WITHHELD",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"network": "base"}]})[0],
        "WITHHELD_NO_OBSERVATION")
    chk("an unobserved operand NEVER yields a non-withheld verdict",
        {pair_row(o, p)[0] for o, p in
         [({"live_http": None}, PEER_OK), (OURS_OK, {"http": None}),
          (dict(OURS_OK, live_amount=None), PEER_OK),
          (dict(OURS_OK, live_network=None), PEER_OK),
          (OURS_OK, {"http": 402, "accepts": [{"amount": "1"}]})]},
        {"WITHHELD_NO_OBSERVATION"})
    # free route, both saw no challenge
    chk("declared-free + no challenge on both sides = agree",
        pair_row({"live_http": 200, "live_amount": None, "declared_free": True},
                 {"http": 200, "accepts": []})[0], "INDEX0_AGREE")
    chk("no challenge on both sides but NOT declared free -> WITHHELD",
        pair_row({"live_http": 200, "live_amount": None, "declared_free": False},
                 {"http": 200, "accepts": []})[0], "WITHHELD_NO_OBSERVATION")
    chk("declared free but the peer DID get a 402 -> not an agreement",
        pair_row({"live_http": 200, "live_amount": None, "declared_free": True},
                 {"http": 402, "accepts": []})[0], "WITHHELD_NO_OBSERVATION")
    # the set remainder is reported on every graded row, so index-0 agreement is never read as
    # agreement about the whole array
    # The remainder counts ENTRIES (live_accepts_n), not distinct amounts: a row with 6 entries
    # and 2 distinct amounts has FIVE unpairable entries, not one (critic, shift 593).
    det = pair_row(dict(OURS_OK, live_amounts=["10000", "5000"], live_accepts_n=6), PEER_OK)[2]
    chk("unpairable remainder counts ENTRIES not distinct amounts",
        det["our_other_entries_unpairable"], 5)
    chk("remainder carries live_accepts_n for audit", det["our_live_accepts_n"], 6)
    chk("remainder is 0 on a single-entry row",
        pair_row(dict(OURS_OK, live_accepts_n=1), PEER_OK)[2]["our_other_entries_unpairable"], 0)
    chk("remainder is WITHHELD (None), never 0, when live_accepts_n is absent",
        pair_row(OURS_OK, PEER_OK)[2]["our_other_entries_unpairable"], None)
    chk("detail records our index0 tuple",
        pair_row(OURS_OK, PEER_OK)[2]["our_index0"],
        {"network": "eip155:8453", "notation": "caip2", "amount": "10000"})
    chk("detail keeps the peer's tuples in served order",
        [t["amount"] for t in pair_row(OURS_OK, {"http": 402, "accepts": [
            {"amount": "7", "network": "base"},
            {"amount": "10000", "network": "base"}]})[2]["peer_tuples"]], ["7", "10000"])
    chk("every verdict pair_row can return is declared in VERDICTS",
        {pair_row(o, p)[0] for o, p in
         [(OURS_OK, PEER_OK),
          (OURS_OK, {"http": 402, "accepts": [{"amount": "10000", "network": "base"}]}),
          (OURS_OK, {"http": 402, "accepts": [{"amount": "1", "network": "eip155:137"},
                                              {"amount": "10000", "network": "base"}]}),
          (OURS_OK, {"http": 402, "accepts": [{"amount": "2", "network": "base"}]}),
          (OURS_OK, {"http": 402, "accepts": [{"amount": "2", "network": "eip155:137"}]}),
          ({"live_http": None}, PEER_OK)]} - set(VERDICTS), set())

    # ---- NETWORK_NOTATION_UNCOMPARABLE: an accusation needs both names recognised -----
    # This is the defect the first version shipped: `solana` vs the CAIP-2 solana reference were
    # two unrecognised strings, so a peer canonicalising either way produced OFFER_SET_DIFFERS --
    # a false finding about a named host. Both must be RECOGNISED before any claim is made.
    OURS_UNK = {"live_http": 402, "live_amount": "80000", "live_network": "cosmoshub-4",
                "live_accepts_n": 1}
    chk("our network unrecognised -> no claim about the host",
        pair_row(OURS_UNK, {"http": 402, "accepts": [{"amount": "80000",
                                                      "network": "base"}]})[0],
        "NETWORK_NOTATION_UNCOMPARABLE")
    chk("peer network unrecognised -> no claim about the host",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "10000",
                                                     "network": "cosmoshub-4"}]})[0],
        "NETWORK_NOTATION_UNCOMPARABLE")
    chk("the uncomparable verdict names the unknown spelling",
        "cosmoshub-4" in pair_row(OURS_OK, {"http": 402, "accepts": [
            {"amount": "10000", "network": "cosmoshub-4"}]})[1], True)
    chk("the uncomparable verdict makes NO claim about the host",
        "no claim made about the host" in pair_row(OURS_OK, {"http": 402, "accepts": [
            {"amount": "10000", "network": "cosmoshub-4"}]})[1], True)
    chk("an unrecognised spelling NEVER yields an accusing verdict",
        {pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": a, "network": "cosmoshub-4"}]})[0]
         for a in ("10000", "99999")},
        {"NETWORK_NOTATION_UNCOMPARABLE"})
    # ... but a RECOGNISED pair still gets a real verdict, so the refusal is not a blanket silence
    chk("two recognised chains still produce OFFER_SET_DIFFERS",
        pair_row(OURS_OK, {"http": 402, "accepts": [{"amount": "10000",
                                                     "network": "ethereum"}]})[0],
        "OFFER_SET_DIFFERS")
    chk("the solana pair now AGREES instead of being called an offer-set difference",
        pair_row({"live_http": 402, "live_amount": "5000", "live_network": "solana",
                  "live_accepts_n": 1},
                 {"http": 402, "accepts": [{"amount": "5000",
                                            "network": SOLANA_MAINNET}]})[0],
        "INDEX0_AGREE_NOTATION_ONLY")

    # ---- declared_vs_peer: the comparison the peer CAN make and our capture CANNOT -------
    # Shaped on a real AMOUNT_INDETERMINATE row: declared solana/10000, accepts[0] is eip155:8453,
    # 6 entries, both amounts present as a SET. declared_terms_present is all-True on all 10.
    TEN = {"live_http": 402, "live_amount": "10000", "live_network": "eip155:8453",
           "live_amounts": ["10000", "101665"], "live_accepts_n": 6,
           "declared_amount": "10000", "declared_network": SOLANA_MAINNET,
           "declared_asset": "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
           "declared_payto": "7lsjfrjf8fnsb8va9u7n3wen25smqlpob3svyuvxacy7",
           "verdict": "AMOUNT_INDETERMINATE"}
    peer_res = {"http": 402, "accepts": [
        {"amount": "101665", "network": "eip155:8453"},
        {"amount": "10000", "network": "solana",
         "asset": "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
         "payTo": "7lsjfrjf8fnsb8va9u7n3wen25smqlpob3svyuvxacy7"}]}
    chk("the peer's per-entry tuples RESOLVE a row our capture called indeterminate",
        declared_vs_peer(TEN, peer_res)[0], "DECLARED_RESOLVED_BY_PEER")
    chk("resolution reports the asset comparison",
        "asset agrees" in declared_vs_peer(TEN, peer_res)[1], True)
    chk("resolution reports the payTo comparison",
        "payTo agrees" in declared_vs_peer(TEN, peer_res)[1], True)
    chk("a differing payTo is reported as DIFFERS, not swallowed",
        "payTo DIFFERS" in declared_vs_peer(TEN, {"http": 402, "accepts": [
            {"amount": "10000", "network": "solana", "payTo": "7other"}]})[1], True)
    chk("an EVM payTo differing only in CASE is NOT a difference",
        "payTo agrees" in declared_vs_peer(
            dict(TEN, declared_payto="0xAbCdEf", declared_asset=""),
            {"http": 402, "accepts": [{"amount": "10000", "network": "solana",
                                       "payTo": "0xabcdef"}]})[1], True)
    # SHIFT 594 REPLACED A LEG HERE, AND THE OLD LEG WAS ASSERTING A FALSE FINDING.
    # It read: "a base58 payTo differing in case IS a difference (case-sensitive)" and required
    # `payTo DIFFERS` for declared "So1111" vs peer "so1111". That is true of two addresses read
    # byte-exact -- and OUR declared column is not byte-exact. `declared_vs_live.json` limit 3
    # says it case-folds addresses, and the census agrees: 471 of 471 base58 `declared_payto`
    # values and 471 of 471 `declared_asset` values are all-lowercase, so a mixed-case value like
    # "So1111" CANNOT occur on our side of this comparison. The leg tested an unreachable input
    # and licensed 46 false DIFFERS claims on the first real peer file. Both branches are kept:
    # fold-equal withholds, fold-different still convicts.
    chk("a base58 payTo differing ONLY in case is NOT assertable (our census folds case)",
        "payTo not compared" in declared_vs_peer(
            dict(TEN, declared_payto="so1111", declared_asset=""),
            {"http": 402, "accepts": [{"amount": "10000", "network": "solana",
                                       "payTo": "So1111"}]})[1], True)
    chk("a case-only base58 payTo is never reported as DIFFERS",
        "payTo DIFFERS" in declared_vs_peer(
            dict(TEN, declared_payto="so1111", declared_asset=""),
            {"http": 402, "accepts": [{"amount": "10000", "network": "solana",
                                       "payTo": "So1111"}]})[1], False)
    chk("the withheld note literally says the census case-folds (bytes, not the constant)",
        "case-folds" in CASE_SENSITIVE_FOLD_NOTE
        and "not assertable" in CASE_SENSITIVE_FOLD_NOTE, True)
    chk("the withheld note says WHY it was withheld",
        CASE_SENSITIVE_FOLD_NOTE in declared_vs_peer(
            dict(TEN, declared_payto="so1111", declared_asset=""),
            {"http": 402, "accepts": [{"amount": "10000", "network": "solana",
                                       "payTo": "So1111"}]})[1], True)
    # THE CONTROL that keeps the conviction branch alive: fold-different base58 still DIFFERS.
    chk("a base58 payTo that differs UNDER FOLDING is still a difference",
        "payTo DIFFERS" in declared_vs_peer(
            dict(TEN, declared_payto="so1111", declared_asset=""),
            {"http": 402, "accepts": [{"amount": "10000", "network": "solana",
                                       "payTo": "So2222"}]})[1], True)
    # ---- addr_compare, the one predicate both branches go through ----
    chk("addr_compare byte-equal base58 -> AGREE", addr_compare("so1111", "so1111"), "AGREE")
    chk("addr_compare fold-equal base58 -> FOLD_UNCOMPARABLE",
        addr_compare("so1111", "So1111"), "FOLD_UNCOMPARABLE")
    chk("addr_compare fold-different base58 -> DIFFER",
        addr_compare("so1111", "So2222"), "DIFFER")
    chk("addr_compare EVM case-only -> AGREE", addr_compare("0xAbCd", "0xabcd"), "AGREE")
    chk("addr_compare EVM genuinely different -> DIFFER",
        addr_compare("0xabcd", "0xabce"), "DIFFER")
    chk("addr_compare never returns FOLD_UNCOMPARABLE for a 0x pair",
        addr_compare("0xABCD", "0xabcd") == "FOLD_UNCOMPARABLE", False)
    chk("addr_compare missing peer side -> UNOBSERVED", addr_compare("so1111", None), "UNOBSERVED")
    chk("addr_compare missing our side -> UNOBSERVED", addr_compare("", "so1111"), "UNOBSERVED")
    chk("addr_compare blank-only peer side -> UNOBSERVED",
        addr_compare("so1111", "   "), "UNOBSERVED")
    chk("an ELIDED peer payee is UNOBSERVED, never a conviction",
        addr_compare("0x6a66aaaabbbbccccddddeeeeffff000011112222", "0x6a66...2222"),
        "UNOBSERVED")
    chk("an ELIDED value on OUR side is UNOBSERVED too",
        addr_compare("0x6a66...2222", "0x6a66aaaabbbbccccddddeeeeffff000011112222"),
        "UNOBSERVED")
    chk("a unicode-ellipsis elision is UNOBSERVED",
        addr_compare("so1111aaaa", "so11\u20261111"), "UNOBSERVED")
    chk("the elision guard does not swallow a real difference",
        addr_compare("so1111aaaa", "so2222bbbb"), "DIFFER")
    # the REAL pair that produced the 46 false claims, verbatim from Penny's file
    chk("the real USDC-mint pair that produced 46 false DIFFERS is FOLD_UNCOMPARABLE",
        addr_compare("epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
                     "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"), "FOLD_UNCOMPARABLE")

    # ---- addr_checksum_note: the FALSE CLEAR (shift 595/596) ----
    # A REACHABLE witness, not a constructed one: the mixed-case `asset` value as one live
    # public manifest in our corpus rendered it, beside our own case-folded declared column.
    # Identity AGREEs (the same 20 bytes) and the checksum claim is still reported separately.
    # The host is not named: see addr_checksum_note's docstring for why.
    _BCH   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"   # as served, measured 2026-11-04
    # The EIP-55 CANONICAL RENDERING of those same 20 bytes (bin/eip55.py: CHECKSUM_VALID).
    # "Canonical" here means the casing keccak decides, which is a pure function of the bytes.
    # It is NOT a claim about which contract this address is; this tool cannot resolve that.
    _CANON = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    _OURS  = _CANON.lower()                                  # our census folds the declared column
    chk("the mis-cased EVM pair still AGREEs -- the note is not a new DIFFER",
        addr_compare(_OURS, _BCH), "AGREE")
    _n = addr_checksum_note(_OURS, _BCH)
    chk("a mis-cased peer-read EVM value produces a checksum NOTE",
        _n is not None and "peer-read" in _n, True)
    chk("the NOTE names the canonical rendering so a reader can re-derive it",
        _n is not None and _CANON in _n, True)
    chk("the NOTE does not claim the address is wrong or that payment would fail",
        _n is not None and not any(w in _n.lower() for w in
                                   ("wrong address", "would fail", "wallet", "settle")), True)
    # CONTROLS THAT CAN FAIL -- each of these would go red if the note fired unconditionally.
    chk("a CORRECTLY cased peer value produces NO note", addr_checksum_note(_OURS, _CANON), None)
    chk("an ALL_LOWER peer value asserts no checksum -> NO note",
        addr_checksum_note(_OURS, _CANON.lower()), None)
    chk("our own folded declared side alone produces NO note (it is our fold, not a host claim)",
        addr_checksum_note(_OURS, None), None)
    chk("base58 never reaches the checksum note (case IS identity there)",
        addr_checksum_note("epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
                           "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"), None)
    chk("an ELIDED peer value produces NO note",
        addr_checksum_note(_OURS, "0x8335...2913"), None)
    chk("a non-string peer value produces NO note", addr_checksum_note(_OURS, 42), None)
    # WHY those two controls pass -- pin it, because the guard above is not what stops them
    # (removing the guard leaves this suite green; `classify` is the thing that refuses).
    chk("mechanism: classify() refuses base58 outright",
        eip55.classify("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"), "NOT_EVM")
    chk("mechanism: classify() refuses an elided 0x value",
        eip55.classify("0x8335...2913"), "NOT_EVM")
    chk("mechanism: ALL_UPPER asserts no checksum either -> NO note",
        addr_checksum_note(_OURS, _CANON.upper().replace("0X", "0x")), None)
    # a BROKEN value on OUR side is reported too, labelled as ours -- the function is symmetric
    # even though our folded column cannot currently produce one.
    _b = addr_checksum_note(_BCH, _CANON)
    chk("a broken value on OUR side is labelled declared, not peer-read",
        _b is not None and _b.startswith("declared") and "peer-read" not in _b, True)
    _both = addr_checksum_note(_BCH, _BCH)
    chk("both sides broken -> both reported", _both is not None
        and _both.count("does not re-render") == 2, True)
    # This fixture puts a DIFFERENT asset and payTo on a wrong-chain entry. Without it the chain
    # filter in the asset/payTo loop could be deleted and the suite stayed green, because the
    # earlier fixture's wrong-chain entry carried no asset/payTo at all (critic + mutation run,
    # shift 593).
    wrongchain = {"http": 402, "accepts": [
        {"amount": "10000", "network": "eip155:8453", "asset": "0xdeadbeef",
         "payTo": "0xcafebabe"},
        {"amount": "10000", "network": "solana",
         "asset": "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
         "payTo": "7lsjfrjf8fnsb8va9u7n3wen25smqlpob3svyuvxacy7"}]}
    chk("asset/payTo are compared ONLY against entries on the declared chain",
        declared_vs_peer(TEN, wrongchain)[1].count("agrees"), 2)
    chk("a wrong-chain asset does not create a false DIFFERS",
        "DIFFERS" in declared_vs_peer(TEN, wrongchain)[1], False)
    # The DETECTING case: the declared chain's entry carries NO asset/payTo and only a WRONG-chain
    # entry does. With the chain filter this is "not compared"; without it, the wrong chain's
    # values would be compared against our declared ones and produce a FALSE DIFFERS. The
    # previous fixture could not catch that, because the right value was present either way.
    only_wrong = {"http": 402, "accepts": [
        {"amount": "10000", "network": "eip155:8453", "asset": "0xdeadbeef",
         "payTo": "0xcafebabe"},
        {"amount": "10000", "network": "solana"}]}
    chk("asset on the WRONG chain only -> not compared, never a DIFFERS",
        "asset not compared" in declared_vs_peer(TEN, only_wrong)[1], True)
    chk("payTo on the WRONG chain only -> not compared, never a DIFFERS",
        "payTo not compared" in declared_vs_peer(TEN, only_wrong)[1], True)
    chk("no false DIFFERS from a wrong-chain-only value",
        "DIFFERS" in declared_vs_peer(TEN, only_wrong)[1], False)

    chk("an unobserved asset is 'not compared', never 'agrees'",
        "asset not compared" in declared_vs_peer(TEN, {"http": 402, "accepts": [
            {"amount": "10000", "network": "solana"}]})[1], True)
    chk("the peer reading the declared chain at another price is a DIVERGENCE",
        declared_vs_peer(TEN, {"http": 402, "accepts": [
            {"amount": "55555", "network": "solana"}]})[0], "DECLARED_DIVERGENT_PER_PEER")
    chk("declared chain absent from the peer array is an ABSENCE, not a finding",
        declared_vs_peer(TEN, {"http": 402, "accepts": [
            {"amount": "10000", "network": "base"}]})[0], "DECLARED_NOT_IN_PEER_ARRAY")
    chk("absence says so in words",
        "not a finding about the host" in declared_vs_peer(TEN, {"http": 402, "accepts": [
            {"amount": "10000", "network": "base"}]})[1], True)
    chk("no declared amount -> UNRESOLVED",
        declared_vs_peer(dict(TEN, declared_amount=""), peer_res)[0], "DECLARED_UNRESOLVED")
    chk("no declared network -> UNRESOLVED",
        declared_vs_peer(dict(TEN, declared_network=None), peer_res)[0], "DECLARED_UNRESOLVED")
    chk("peer sent nothing comparable -> UNRESOLVED",
        declared_vs_peer(TEN, {"http": 402, "accepts": []})[0], "DECLARED_UNRESOLVED")
    chk("an UNRECOGNISED declared network is UNRESOLVED, never matched by guessing",
        declared_vs_peer(dict(TEN, declared_network="cosmoshub-4"), peer_res)[0],
        "DECLARED_UNRESOLVED")
    # ---- PAIR BEFORE YOU COMPARE: no agreement assembled across two entries ----------
    # The defect this catches, measured on this exact fixture (shift 593): the peer serves TWO
    # entries on the declared chain -- one at the declared PRICE with the wrong asset/payTo, one
    # with the right asset/payTo at a different price. Collecting the peer's asset/payTo across
    # entries and then asking for membership graded this "priced at 10000, matching the manifest;
    # asset agrees; payTo agrees" -- an agreement that exists in NEITHER offer.
    CROSS = dict(TEN, declared_asset="assetaaa", declared_payto="paytoaaa")
    cross_peer = {"http": 402, "accepts": [
        {"amount": "10000", "network": "solana", "asset": "assetzzz", "payTo": "paytozzz"},
        {"amount": "55555", "network": "solana", "asset": "assetaaa", "payTo": "paytoaaa"}]}
    cv, cwhy = declared_vs_peer(CROSS, cross_peer)
    chk("cross-entry: the price match is still a resolution", cv, "DECLARED_RESOLVED_BY_PEER")
    chk("cross-entry: asset is NOT reported as agreeing", "asset agrees" in cwhy, False)
    chk("cross-entry: payTo is NOT reported as agreeing", "payTo agrees" in cwhy, False)
    chk("cross-entry: the entry that carries the declared price is the one graded",
        "asset DIFFERS" in cwhy and "payTo DIFFERS" in cwhy, True)
    chk("cross-entry: the reason says ONE entry", "in ONE entry" in cwhy, True)
    # and the clean case must still agree, so the fix is not a blanket refusal
    clean_peer = {"http": 402, "accepts": [
        {"amount": "55555", "network": "solana", "asset": "assetzzz", "payTo": "paytozzz"},
        {"amount": "10000", "network": "solana", "asset": "assetaaa", "payTo": "paytoaaa"}]}
    chk("same-entry match on all three fields still agrees",
        declared_vs_peer(CROSS, clean_peer)[0], "DECLARED_RESOLVED_BY_PEER")
    chk("same-entry match reports BOTH fields agreeing",
        declared_vs_peer(CROSS, clean_peer)[1].count("agrees"), 2)
    chk("a wrong-chain entry at the declared price does not resolve the declared chain",
        declared_vs_peer(CROSS, {"http": 402, "accepts": [
            {"amount": "10000", "network": "base", "asset": "assetaaa",
             "payTo": "paytoaaa"}]})[0], "DECLARED_NOT_IN_PEER_ARRAY")
    # two entries at the SAME chain and amount: disclose the ambiguity rather than pick silently
    dup_peer = {"http": 402, "accepts": [
        {"amount": "10000", "network": "solana", "asset": "assetzzz", "payTo": "paytozzz"},
        {"amount": "10000", "network": "solana", "asset": "assetaaa", "payTo": "paytoaaa"}]}
    chk("duplicate chain+amount entries: the ambiguity is disclosed",
        "2 peer entries carry that chain and amount" in declared_vs_peer(CROSS, dup_peer)[1], True)
    chk("duplicate chain+amount entries: the BEST match is the one reported",
        declared_vs_peer(CROSS, dup_peer)[1].count("agrees"), 2)

    chk("every verdict declared_vs_peer can return is in DECLARED_VERDICTS",
        {declared_vs_peer(o, pp)[0] for o, pp in
         [(TEN, peer_res), (TEN, {"http": 402, "accepts": [{"amount": "5", "network": "solana"}]}),
          (TEN, {"http": 402, "accepts": [{"amount": "1", "network": "base"}]}),
          (dict(TEN, declared_amount=""), peer_res)]} - set(DECLARED_VERDICTS), set())
    # The two comparisons are INDEPENDENT, and this fixture proves it in the strongest way: the
    # SAME row is a reader-vs-reader DIVERGENCE (our accepts[0] is eip155:8453/10000, the peer
    # read 101665 on that chain) and a DECLARED RESOLUTION (the peer priced the declared solana
    # term at the declared 10000). Reporting either verdict as the other would be a false claim.
    chk("the declared comparison is INDEPENDENT of the reader-vs-reader one",
        (pair_row(TEN, peer_res)[0], declared_vs_peer(TEN, peer_res)[0]),
        ("TERMS_DIVERGENT", "DECLARED_RESOLVED_BY_PEER"))

    # ---- peer_tuples ----
    chk("peer tuples normalise the chain and keep the notation",
        peer_tuples({"accepts": [{"amount": "1", "network": "base"}]}),
        [("eip155:8453", "bare", "1")])
    chk("v1 field name accepted",
        peer_tuples({"accepts": [{"maxAmountRequired": "1000", "network": "base"}]}),
        [("eip155:8453", "bare", "1000")])
    chk("half-observed entries dropped, not defaulted",
        peer_tuples({"accepts": [{"amount": "1"}, {"network": "base"},
                                 {"amount": "2", "network": "base"}]}),
        [("eip155:8453", "bare", "2")])
    chk("non-dict entry dropped", peer_tuples({"accepts": ["base"]}), [])
    chk("no accepts", peer_tuples({}), [])

    # ---- the shared extraction predicate governs BOTH paths ---------------------------
    # `declared_vs_peer` used to keep its own copy of this loop, so these legs passed while the
    # declared path silently accepted half-observed entries (mutation run, shift 593).
    HALF = {"http": 402, "accepts": [
        {"amount": "10000"},                               # no network
        {"network": "solana"},                             # no amount
        "not-a-dict",
        {"amount": "10000", "network": "solana", "asset": "assetaaa", "payTo": "paytoaaa"}]}
    chk("peer_entries keeps the raw entry alongside the tuple",
        [(c, a, isinstance(e, dict)) for c, _n, a, e in peer_entries(HALF)],
        [(SOLANA_MAINNET, "10000", True)])
    chk("peer_tuples is derived from peer_entries, not a second loop",
        peer_tuples(HALF), [(SOLANA_MAINNET, "bare", "10000")])
    chk("the DECLARED path drops the same half-observed entries",
        declared_vs_peer(dict(TEN, declared_asset="assetaaa", declared_payto="paytoaaa"),
                         HALF)[0], "DECLARED_RESOLVED_BY_PEER")
    chk("and it resolved from the ONE complete entry, not from the half-observed ones",
        declared_vs_peer(dict(TEN, declared_asset="assetaaa", declared_payto="paytoaaa"),
                         HALF)[1].count("agrees"), 2)
    chk("a peer row of ONLY half-observed entries resolves nothing",
        declared_vs_peer(TEN, {"http": 402, "accepts": [{"amount": "10000"},
                                                        {"network": "solana"}]})[0],
        "DECLARED_UNRESOLVED")
    chk("a non-dict entry cannot reach either path",
        (peer_tuples({"accepts": ["solana"]}),
         declared_vs_peer(TEN, {"http": 402, "accepts": ["solana"]})[0]),
        ([], "DECLARED_UNRESOLVED"))

    # ---- default_ours: the published layout resolves without --ours -------------------
    # A tool published BESIDE its data must run there. The first version resolved only this
    # repo's bin/ layout, so a stranger's checkout would have raised FileNotFoundError on a
    # path that does not exist in their tree -- a defect invisible from inside our own tree.
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        _sib = os.path.join(_td, "declared_vs_live.json")
        _repo = os.path.join(_td, "elsewhere.json")
        chk("neither candidate exists -> the LAST path is returned so the error names it",
            default_ours((_sib, _repo)), _repo)
        open(_repo, "w").write("{}")
        chk("only the repo-layout candidate exists -> it is chosen",
            default_ours((_sib, _repo)), _repo)
        open(_sib, "w").write("{}")
        chk("the SIBLING (published) layout wins when both exist",
            default_ours((_sib, _repo)), _sib)
        os.remove(_repo)
        chk("only the sibling exists -> it is chosen", default_ours((_sib, _repo)), _sib)
    chk("the real candidate list has the published sibling FIRST",
        os.path.basename(os.path.dirname(_OURS_CANDIDATES[0])), os.path.basename(HERE))
    chk("and exactly two candidates, both named declared_vs_live.json",
        (len(_OURS_CANDIDATES),
         {os.path.basename(p) for p in _OURS_CANDIDATES}),
        (2, {"declared_vs_live.json"}))

    # ---- join ----
    our = [{"url": "https://h/1", "host": "h", "live_http": 402, "live_amount": "10000",
            "live_network": "eip155:8453", "live_amounts": ["10000"], "verdict": "OK"},
           {"url": "https://h/2", "host": "h", "live_http": 402, "live_amount": "10000",
            "live_network": "eip155:8453", "live_amounts": ["10000", "101665"],
            "verdict": "AMOUNT_INDETERMINATE"},
           {"url": "", "host": "h", "live_http": None},
           {"url": "https://h/dup", "host": "h", "live_http": 402, "live_amount": "1",
            "live_network": "base"},
           {"url": "https://h/dup", "host": "h", "live_http": 402, "live_amount": "2",
            "live_network": "base"},
           {"url": "https://h/unread", "host": "h", "live_http": None},
           {"url": "https://h/weonly", "host": "h", "live_http": 402, "live_amount": "3",
            "live_network": "base", "verdict": "OK"},
           # paired but WITHHELD: both readers present, neither captured a comparable entry.
           # Its only job is to prove `two_reader_rows` excludes a withheld row.
           {"url": "https://h/withheld", "host": "h", "live_http": 402, "live_amount": None,
            "declared_free": False, "verdict": "INDETERMINATE"},
           # an AMOUNT_INDETERMINATE row the peer's per-entry tuples can resolve -- without it
           # the rescued-rows list could be deleted and the suite stayed green (shift 593)
           {"url": "https://h/rescued", "host": "h", "live_http": 402, "live_amount": "10000",
            "live_network": "eip155:8453", "live_amounts": ["10000", "101665"],
            "live_accepts_n": 6, "declared_amount": "10000",
            "declared_network": SOLANA_MAINNET, "verdict": "AMOUNT_INDETERMINATE"}]
    peer = {"reader": "p", "probed_at": "2026-11-04T00:00:00Z", "rows": [
        {"url": "https://h/1", "http": 402, "accepts": [{"amount": "10000", "network": "base"}]},
        {"url": "https://h/2", "http": 402, "accepts": [{"amount": "101665", "network": "base"},
                                                        {"amount": "10000", "network": "base"}]},
        {"url": "https://h/dup", "http": 402, "accepts": [{"amount": "1", "network": "base"}]},
        {"url": "https://h/never-seen", "http": 402, "accepts": [{"amount": "7",
                                                                  "network": "base"}]},
        {"url": "https://h/1", "http": 402, "accepts": [{"amount": "10000"}]},
        {"url": "https://h/withheld", "http": 402, "accepts": []},
        {"url": "https://h/rescued", "http": 402, "accepts": [
            {"amount": "101665", "network": "eip155:8453"},
            {"amount": "10000", "network": "solana"}]},
        "not-an-object"]}
    res = join(our, peer)
    chk("join: notation-only row graded", res["verdict_counts"]["INDEX0_AGREE_NOTATION_ONLY"], 1)
    chk("join: the reordered row is ORDER_DIFFERS", res["verdict_counts"]["ORDER_DIFFERS"], 1)
    # Exactly ONE divergence, and it is the /rescued row, where the peer genuinely read 101665 on
    # eip155:8453 against our 10000. No OTHER row may produce one.
    chk("join: exactly one divergence, and only on the row that really diverges",
        [p["url"] for p in res["paired"] if p["verdict"] == "TERMS_DIVERGENT"],
        ["https://h/rescued"])
    chk("join: no offer-set difference invented", res["verdict_counts"]["OFFER_SET_DIFFERS"], 0)
    chk("join: ambiguous url is NOT paired",
        [p["url"] for p in res["paired"]],
        ["https://h/1", "https://h/2", "https://h/withheld", "https://h/rescued"])
    chk("join: the withheld row IS paired (read by both) but withheld",
        res["verdict_counts"]["WITHHELD_NO_OBSERVATION"], 1)
    chk("join: ambiguous url reported as malformed-with-reason",
        any("AMBIGUOUS" in b and "https://h/dup" in b for b in res["malformed_peer_rows"]), True)
    chk("join: duplicate peer url reported",
        any("duplicate peer url" in b for b in res["malformed_peer_rows"]), True)
    chk("join: non-object peer row reported",
        any("non-object row" in b for b in res["malformed_peer_rows"]), True)
    chk("join: peer-only url listed", res["peer_only_urls"], ["https://h/never-seen"])
    # /dup and /unread must NOT appear here: the peer DID read /dup (it is ambiguous on our side,
    # a different complaint) and we never probed /unread. Only a url we probed and the peer did
    # not belongs in this list.
    chk("join: our probed urls the peer skipped are listed, so absence is not silent",
        res["our_probed_urls_the_peer_did_not_read"], ["https://h/weonly"])
    chk("join: empty-url rows counted, not silently dropped",
        res["our_index"]["rows_with_empty_url_dropped"], 1)
    chk("join: ambiguous urls named", res["our_index"]["ambiguous_urls_resolved_to_nothing"],
        ["https://h/dup"])
    # 4 paired rows, 1 of them WITHHELD -> 3 two-reader rows.
    chk("join: two-reader rows counts only non-withheld", res["two_reader_rows"], 3)
    chk("join: two-reader rows is strictly fewer than the paired rows here, so a withheld row "
        "cannot inflate it", res["two_reader_rows"] < len(res["paired"]), True)
    chk("join: every emittable verdict named even at zero",
        sorted(res["verdict_counts"]), sorted(VERDICTS))
    chk("join: every DECLARED verdict named even at zero",
        sorted(res["declared_verdict_counts"]), sorted(DECLARED_VERDICTS))
    chk("join: the row our capture called AMOUNT_INDETERMINATE is listed as peer-resolved",
        res["rows_our_capture_could_not_resolve_that_the_peer_did"], ["https://h/rescued"])
    chk("join: that rescue is counted under the DECLARED comparison",
        res["declared_verdict_counts"]["DECLARED_RESOLVED_BY_PEER"], 1)
    chk("join: and it is NOT added to the reader-vs-reader agreement counts",
        res["verdict_counts"]["INDEX0_AGREE"], 0)
    chk("join: refuses a document with no rows list",
        "error" in join(our, {"reader": "p"}), True)
    chk("join: refuses None document", "error" in join(our, None), True)
    chk("join: a rows value that is not a list is refused, not iterated",
        "error" in join(our, {"rows": "https://h/1"}), True)

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


# ------------------------------------------------------------------ main
def main(argv):
    if "--schema" in argv:
        print(SCHEMA_TEXT)
        return 0
    if "--peer" not in argv:
        print(__doc__)
        return 2
    peer_path = argv[argv.index("--peer") + 1]
    ours_path = argv[argv.index("--ours") + 1] if "--ours" in argv else default_ours()
    with open(ours_path) as f:
        ours = json.load(f)
    with open(peer_path) as f:
        peer = json.load(f)
    res = join(ours.get("routes") or [], peer)
    if "error" in res:
        print("REFUSED: %s" % res["error"])
        return 1
    print("paired against %s" % ours_path)
    print("peer reader=%r probed_at=%r" % (res["reader"], res["peer_probed_at"]))
    print("our joinable urls: %d (%d rows had an empty url; %d url(s) ambiguous -> resolved to "
          "nothing)" % (res["our_index"]["joinable_urls"],
                        res["our_index"]["rows_with_empty_url_dropped"],
                        len(res["our_index"]["ambiguous_urls_resolved_to_nothing"])))
    for v in sorted(res["verdict_counts"]):
        print("  %-28s %d" % (v, res["verdict_counts"][v]))
    print("TWO-READER ROWS (non-withheld): %d" % res["two_reader_rows"])
    print("DECLARED-vs-PEER (a DIFFERENT comparison -- never add these to the counts above):")
    for v in sorted(res["declared_verdict_counts"]):
        print("  %-28s %d" % (v, res["declared_verdict_counts"][v]))
    rescued = res["rows_our_capture_could_not_resolve_that_the_peer_did"]
    print("  rows OUR capture called AMOUNT_INDETERMINATE that the peer's tuples RESOLVE: %d"
          % len(rescued))
    for u in rescued[:20]:
        print("    %s" % u)
    for p in res["paired"]:
        print("  %-24s %-28s %s" % (p["host"], p["verdict"], p["url"]))
        print("      %s" % p["reason"])
    if res["peer_only_urls"]:
        print("peer read %d url(s) we have no row for:" % len(res["peer_only_urls"]))
        for u in res["peer_only_urls"][:20]:
            print("    %s" % u)
    if res["our_probed_urls_the_peer_did_not_read"]:
        print("we probed %d url(s) the peer did not read (still SINGLE-READER):"
              % len(res["our_probed_urls_the_peer_did_not_read"]))
        for u in res["our_probed_urls_the_peer_did_not_read"][:20]:
            print("    %s" % u)
    if res["malformed_peer_rows"]:
        print("REFUSED %d peer row(s):" % len(res["malformed_peer_rows"]))
        for b in res["malformed_peer_rows"]:
            print("    %s" % b)
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
        with open(out, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True)
        print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main(sys.argv[1:]))
