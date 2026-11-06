#!/usr/bin/env python3
"""Derive a publishable declared-vs-live x402 route table from a saved route-recon run.

WHAT IT IS FOR. An x402 client that wants to know what a route costs before paying reads
`/.well-known/x402` -- and there is no single field to read. A manifest-only census can say which
price SURFACE a host publishes on; it cannot say whether the published number is the number the
route charges. This tool turns a saved route-layer run (manifest terms paired with the live 402
challenge, per route) into a public per-route table plus the aggregate counts, so every figure in
the accompanying report can be recomputed by a stranger from the published rows.

THE COMPARISON THIS TOOL REFUSES TO MAKE, which is the reason it is not a plain `json.dump` of
its input. A live 402 may offer several `accepts[]` entries. The run's per-route record keeps the
live challenge's networks, amounts and assets as sorted DISTINCT SETS plus one SCALAR reading, and
that scalar triple (network, payee, amount) belongs to ONE entry. If the manifest declared a
different entry, then comparing the declared amount against the scalar live amount compares two
different offers. Two classes of false finding come out of ignoring that:

  1. A multi-network host whose live challenge quotes more than one price. The declared entry's
     live price was never observed, so the row supports neither an agreement nor a divergence.
     Those rows are AMOUNT_INDETERMINATE and are counted in NEITHER aggregate.
  2. CAIP-2 against a bare chain name -- `solana:<genesis>` versus `solana` -- which is the same
     chain written two ways, not a divergence.

Correspondence is therefore tested as `live_network == declared_network` (the full string, not the
family), and the amount question is asked AFTER the pairing question, never before.

WHAT IT CANNOT DO, stated because it bounds every verdict. Pairwise `accepts[]` matching is
IMPOSSIBLE from a capture that stored distinct sets rather than per-entry tuples: membership of a
declared term in a live set proves the host still OFFERS that term, NOT that it offers it at that
amount to that payee. No amount of re-analysis fixes that -- it needs a re-probe that keeps
per-entry tuples.

ADDRESSES MAY BE CASE-FOLDED by the upstream capture. Folding is lossless for EVM addresses
(EIP-55 checksum is only casing) and LOSSY for base58 Solana addresses, which are case-sensitive,
so a folded base58 payee is reported with `payto_comparable: false` rather than asserted equal.

    python3 x402_route_table.py --run <route_recon_run.json> --out <table.json>
    python3 x402_route_table.py --reanalyse <table.json>   # recompute aggregates, no network
    python3 x402_route_table.py --selftest                 # no network, no input file

This tool NEVER opens a socket: it imports no network module and only reads the files named on
its command line. `--reanalyse` recomputes every aggregate from the published rows and exits
non-zero if they disagree with the block stored in the file, so a reader never has to trust the
stated numbers.

MIT licensed. Produced by Quietforge Studio, an AI-run studio; this file was written by an
autonomous agent.
"""
import argparse, collections, json, sys

# The classes v1 assigned to a route it never probed. Kept verbatim as the REASON so no row
# loses why it is indeterminate.
UNPROBED = ("NOT_PROBED_CAP", "URL_REFUSED", "GATE_REFUSED", "TEMPLATED", "NO_CHALLENGE")


def net_family(n):
    """CAIP-2 `solana:<genesis>` and a bare `solana` name the same chain family.

    Returns None for a missing network so a comparison against it can be WITHHELD rather than
    resolved either way (a None must never compare unequal into an accusation).
    """
    if not isinstance(n, str) or not n.strip():
        return None
    return n.strip().lower().split(":", 1)[0]


def is_evm_addr(a):
    """EVM addresses are case-insensitive; base58 Solana addresses are not. Only the former may
    be compared after case folding."""
    if not isinstance(a, str):
        return False
    a = a.strip()
    return len(a) == 42 and a.lower().startswith("0x")


def term_present(declared, live_set):
    """Is a DECLARED term present in the live challenge's own set? Three states.

    True/False only when BOTH operands were observed; **None when either was not**. A missing
    declared term, or a live set we never read, must not publish `false` -- "the host does not
    offer the declared network" is a negative FACT about a named host, and deriving it from an
    unread field is the accusation `net_family`'s docstring forbids ("a None must never compare
    unequal into an accusation").

    This replaces `d_net in l_nets` / `bool(d_asset) and d_asset in l_assets`. Note that the
    `bool(...) and ...` form was NOT a guard: it also yields `False` for an unobserved term, which
    is the wrong value rather than a withheld one. Only a third state fixes it.
    """
    if declared in (None, "") or not live_set:
        return None
    return declared in live_set


def multi_entry(rt):
    """Does the live challenge carry MORE THAN ONE `accepts[]` entry?

    `live_accepts_n` is only trusted when it is an actual integer, because a STRING count would
    otherwise raise on comparison rather than answer. An unobserved count returns False, which
    keeps every single-entry and never-probed row on exactly the path it took before this
    predicate existed.

    NO BOOL SPECIAL-CASE, and an earlier version of this docstring claimed otherwise. It said a
    bool "would satisfy `n > 1` as 1" -- false: `True == 1`, so `True > 1` is already False and
    a bool is rejected by the comparison itself. `bin/mutate_check.py` deleted the clause and the
    suite stayed 93/93 green, which is what proved the justification wrong rather than merely
    unproven. The shift-598 `is_unpriced` guard DID need the bool clause, and the difference is
    the threshold, not the type: there the test was `v > 0`, and `True > 0` is True.
    """
    n = rt.get("live_accepts_n")
    if not isinstance(n, int):
        return False
    return n > 1


def declared_entry_identified(rt):
    """Does the DECLARED network pick out exactly ONE entry of the live challenge?

    This is the predicate the module docstring's "WHAT IT CANNOT DO" paragraph describes, stated
    as code. The run stores `live_networks` / `live_amounts` / `live_assets` as sorted DISTINCT
    SETS, not as per-entry arrays -- saylorinnovations.com serves `live_accepts_n` 6 behind 4
    distinct networks and 2 distinct amounts. So neither a `.count()` over that list nor
    `declared_network in live_networks` can say which entry the scalar `live_payto` /
    `live_amount` was read from: membership is true for every entry of a multi-network host.

    The dedupe is LOSSLESS with respect to entry identity in exactly one case: when
    `len(set(networks)) == live_accepts_n`, every entry carries its own network, so naming the
    network names the entry. That is why 47620.xyz (3 entries / 3 distinct networks) may be
    compared field-for-field while saylorinnovations.com (6 entries / 4 networks) may not.

    Returns True / False / None, and None is a THIRD STATE, not a soft False:
      True  -- one entry, or a lossless network set that contains the declared network
      False -- the set is lossy, or the declared network is absent from it
      None  -- the network set was not observed at all, so the question has no surface
    Every call site must refuse on anything that is not True.
    """
    if not multi_entry(rt):
        return True                      # the scalar IS the whole challenge
    nets = rt.get("live_networks") or []
    if not nets:
        return None                      # no surface: count says multi, set was not retained
    if len(set(nets)) != rt.get("live_accepts_n"):
        return False                     # dedupe lost the per-entry mapping
    d_net = rt.get("declared_network")
    if not d_net:
        return None
    return d_net in nets


def amount_divergence_assertable(rt):
    """May a scalar declared-vs-live amount mismatch be published as DIVERGENT?

    A deduped set is lossy for the entry MAPPING but still exact for MEMBERSHIP, and those two
    facts license different claims. So a mismatch may be asserted when any one of these holds:
      * the challenge is single-entry (or its count was never observed) -- nothing to confuse;
      * the declared network identifies the entry, so the scalar is the declared offer;
      * the amount set WAS observed and the declared amount is absent from ALL of it -- then no
        entry of the challenge quotes the declared price, whichever entry the scalar came from.

    Otherwise the host may well offer the declared amount at an entry other than the one the
    scalar was read from, and a DIVERGENT verdict would be an accusation about a named host
    built on an entry we cannot identify. Returns (assertable: bool, reason: str).
    """
    if not multi_entry(rt):
        return True, "single-entry challenge: the scalar reading is the whole challenge"
    if declared_entry_identified(rt) is True:
        return True, "the declared network identifies exactly one entry of the challenge"
    amts = rt.get("live_amounts") or []
    if not amts:
        return False, "the challenge is multi-entry and its amount set was not observed"
    if str(rt.get("declared_amount")) not in [str(x) for x in amts]:
        return True, ("the declared amount appears in NO entry of the observed amount set (%s)"
                      % ", ".join(sorted(set(str(x) for x in amts))))
    return False, ("the challenge carries %s entries, the declared network does not identify "
                   "one of them, and the declared amount %s IS present in the challenge's own "
                   "amount set -- so the host offers the declared price at some entry and the "
                   "mismatch is an artifact of which entry the scalar was read from"
                   % (rt.get("live_accepts_n"), rt.get("declared_amount")))


def payto_claim(rt, d_payto, l_payto):
    """(payto_comparable, payto_equal) for one row -- the ONLY place either is decided.

    Both were previously computed inline at two separate call sites, which is how a gate added to
    one of them would have left the other asserting. Case folding is applied only to EVM
    addresses (lossless), never to base58. An equality claim additionally requires that the
    declared network identify the entry: the scalar `live_payto` of a lossy multi-entry challenge
    is some entry's payee, and asserting `payto_equal: false` about it would publish a mismatch
    against a named host on an entry we cannot identify.
    """
    if declared_entry_identified(rt) is not True:
        return False, None
    comparable = is_evm_addr(d_payto) and is_evm_addr(l_payto)
    if not comparable or not d_payto or not l_payto:
        return comparable, None
    return comparable, str(d_payto).lower() == str(l_payto).lower()


def classify(rt):
    """One route's verdict, plus the facts the verdict rests on. Pure -- no I/O, so every branch
    below is reachable offline from a fixture.

    THE ORDER MATTERS AND IT IS THE WHOLE CORRECTION OF THIS FILE'S FIRST DRAFT. That draft
    compared the two AMOUNTS first and only then asked whether the other terms corresponded --
    which privileges `amount` for no reason. On a multi-entry challenge the scalar `live_amount`
    belongs to the SAME entry as the scalar `live_payto` and `live_network`; if those belong to a
    different entry than the manifest declared, so does the amount, and an "amounts agree" on that
    row is exactly the non-corresponding comparison this tool exists to refuse. So the FIRST
    question is whether the amount can be paired at all.

    Correspondence is tested as `live_network == declared_network` (the full string, not the
    family). The first draft tested only `declared_network in live_networks`, which is true for
    every entry of a multi-network host -- so it fired on 9 `47620.xyz` rows whose scalar network,
    payee, asset AND amount all match the declared entry field-for-field, and published
    "the scalar live payTo/network belong to a different entry" about a named host nine times
    when that row's own bytes say otherwise.
    """
    v1 = rt.get("verdict")
    d_amt, l_amt = rt.get("declared_amount"), rt.get("live_amount")
    d_net, l_net = rt.get("declared_network"), rt.get("live_network")
    d_payto, l_payto = rt.get("declared_payto"), rt.get("live_payto")
    d_asset = rt.get("declared_asset")
    l_nets = rt.get("live_networks") or []
    l_amts = rt.get("live_amounts") or []
    l_assets = rt.get("live_assets") or []

    # Both payTo keys are initialised EXPLICITLY so every return of this function carries them.
    # A withheld row previously returned no `payto_equal` key at all, which forces a consumer
    # into `.get("payto_equal")` -- and a defaulted read cannot be told apart from an observed
    # value. An explicit None says "withheld" in the row itself.
    out = {"verdict": None, "reason": "", "payto_comparable": None, "payto_equal": None}

    # A route that was never probed has nothing to reconcile. Keep v1's reason, and carry the HTTP
    # status when there is one: "NO_CHALLENGE" alone reads like a broken paywall, when on 8 of
    # the 11 such rows the route simply 404s.
    if v1 in UNPROBED:
        out["verdict"] = "INDETERMINATE"
        http = rt.get("live_http")
        out["reason"] = ("not compared: %s" % v1 if http is None
                         else "not compared: %s, route answered HTTP %s" % (v1, http))
        return out
    if v1 == "FREE_AS_DECLARED":
        out["verdict"] = "FREE_AS_DECLARED"
        out["reason"] = "manifest declares free and the route answered without a challenge"
        return out

    # Both amounts must be OBSERVED before any comparison is made.
    if d_amt in (None, "") or l_amt in (None, ""):
        out["verdict"] = "INDETERMINATE"
        out["reason"] = "declared or live amount not observed"
        return out

    multi = len(set(l_nets)) > 1
    corresponds = bool(d_net) and bool(l_net) and d_net == l_net
    # None, NOT 1, when the set was never read. Collapsing an unobserved set to "one distinct
    # amount" let STEP 1 publish DIVERGENT against "the challenge's only amount" while naming an
    # amount set we had not observed -- the same unobserved-operand accusation the new predicate
    # refuses one branch below.
    distinct_amounts = len(set(str(x) for x in l_amts)) if l_amts else None

    # ---- STEP 1: is the amount pairable at all? ------------------------------------------
    if multi and not corresponds:
        out["payto_comparable"] = False
        out["declared_terms_present"] = {
            "network": term_present(d_net, l_nets),
            "amount": term_present(str(d_amt), [str(x) for x in l_amts]),
            "asset": term_present(d_asset, l_assets),
        }
        if distinct_amounts is None:
            out["verdict"] = "AMOUNT_INDETERMINATE"
            out["reason"] = (
                "the scalar live tuple is network %r while the manifest declared %r, and the "
                "challenge's own amount set was NOT observed, so the declared entry's live price "
                "is not available and no divergence can be attributed to the host"
                % (l_net, d_net))
            return out
        if distinct_amounts > 1:
            # The scalar tuple is a DIFFERENT offer and the challenge quotes several prices, so
            # the declared entry's live price was never observed. This row cannot support an
            # agreement OR a divergence, and it must not be counted in either.
            out["verdict"] = "AMOUNT_INDETERMINATE"
            out["reason"] = (
                "the scalar live tuple is network %r while the manifest declared %r, and the "
                "challenge quotes %d distinct amounts (%s), so the declared entry's live price "
                "was not observed; set membership shows the host still OFFERS the declared term "
                "but not at what price to whom"
                % (l_net, d_net, distinct_amounts, ", ".join(sorted(set(str(x) for x in l_amts)))))
            return out
        # Only ONE distinct amount exists in the whole challenge, so the amount is unambiguous
        # even though the payee/network scalars belong to another entry.
        if str(d_amt) != str(l_amt):
            out["verdict"] = "DIVERGENT"
            out["reason"] = "declared amount %s != the challenge's only amount %s" % (d_amt, l_amt)
            return out
        out["verdict"] = "AMOUNT_OK_TERMS_INDETERMINATE"
        out["reason"] = (
            "amount agrees and is unambiguous (the challenge quotes exactly one amount), but the "
            "live challenge carries %d accepts entries across %d networks and the scalar "
            "payTo/network belong to network %r rather than the declared %r, so non-amount terms "
            "are not comparable" % (rt.get("live_accepts_n") or 0, len(set(l_nets)), l_net, d_net))
        return out

    # ---- STEP 2: the scalar tuple IS the declared entry (or there is only one entry) -------
    # STEP 1 only fires on `multi and not corresponds`, so a challenge whose entries share the
    # declared network -- or whose network set is DEDUPED LOSSILY -- arrives here with its entry
    # still unidentified. Convicting on the scalar there is the false finding the module
    # docstring forbids, so the conviction is gated on a named predicate rather than on having
    # fallen through STEP 1.
    if str(d_amt) != str(l_amt):
        assertable, why = amount_divergence_assertable(rt)
        if not assertable:
            out["verdict"] = "AMOUNT_INDETERMINATE"
            out["reason"] = ("declared amount %s != the scalar live amount %s, but that "
                             "difference cannot be attributed to the host: %s"
                             % (d_amt, l_amt, why))
            out["payto_comparable"] = False
            out["declared_terms_present"] = {
                "network": term_present(d_net, l_nets),
                "amount": term_present(str(d_amt), [str(x) for x in l_amts]),
                "asset": term_present(d_asset, l_assets),
            }
            return out
        out["verdict"] = "DIVERGENT"
        out["reason"] = "declared amount %s != live amount %s (%s)" % (d_amt, l_amt, why)
        return out

    df, lf = net_family(d_net), net_family(l_net)
    if df is None or lf is None:
        # A network we never observed must not be reported as agreeing. The first draft fell
        # through to OK here and would have printed "no other term disagrees" about an
        # unobserved field.
        out["verdict"] = "AMOUNT_OK_TERMS_UNOBSERVED"
        out["reason"] = ("amount agrees; declared network %r / live network %r -- at least one "
                         "was not observed, so no other term is compared" % (d_net, l_net))
        out["payto_comparable"] = False
        return out

    if df != lf:
        out["verdict"] = "AMOUNT_OK_NETWORK_DIFFERS"
        out["reason"] = "amount agrees; declared chain family %r, live %r" % (df, lf)
        out["payto_comparable"] = False
        return out

    if d_net != l_net:
        out["verdict"] = "AMOUNT_OK_NETWORK_NOTATION"
        out["reason"] = ("same chain family %r written two ways: declared %r, live %r"
                         % (df, d_net, l_net))
        out["payto_comparable"], out["payto_equal"] = payto_claim(rt, d_payto, l_payto)
        return out

    out["verdict"] = "OK"
    out["payto_comparable"], out["payto_equal"] = payto_claim(rt, d_payto, l_payto)
    # "no other term disagrees" is a claim about the payee too, so it may only be made when the
    # payee was actually COMPARED. On a withheld row the honest sentence names what was not read;
    # the old wording asserted agreement on a field the same row declines to compare -- the
    # flag/value contradiction this pass exists to remove, in prose instead of in a field.
    out["reason"] = ("declared and live amount agree and no other term disagrees"
                     if out["payto_comparable"] else
                     "declared and live amount and network agree; the payee is NOT compared on "
                     "this row (see payto_comparable) so no claim is made about it")
    return out


def build(run):
    """Flatten a route-recon run into publishable per-route rows. Third-party hosts are named --
    they are the public subjects of the census and were each read with polite unauthenticated
    requests to locations the x402 spec defines. No request body, header or response body is
    carried: the `reproduce` line lets a reader get the bytes themselves."""
    rows = []
    for rec in run["records"]:
        for rt in rec.get("routes") or []:
            c = classify(rt)
            rows.append({
                "host": rec["host"],
                "origin": rec["origin"],
                "manifest_class": rec["klass"],
                "manifest_http": rec.get("manifest_http"),
                "url": rt.get("url"),
                "probe_method": rt.get("probe_method"),
                "declared_convention": rt.get("declared_convention"),
                "declared_amount": rt.get("declared_amount"),
                "declared_asset": rt.get("declared_asset"),
                "declared_network": rt.get("declared_network"),
                "declared_payto": rt.get("declared_payto"),
                "declared_free": rt.get("declared_free"),
                "live_http": rt.get("live_http"),
                "live_amount": rt.get("live_amount"),
                "live_asset": rt.get("live_asset"),
                "live_network": rt.get("live_network"),
                "live_payto": rt.get("live_payto"),
                "live_accepts_n": rt.get("live_accepts_n"),
                "live_networks": rt.get("live_networks"),
                "live_amounts": rt.get("live_amounts"),
                "live_assets": rt.get("live_assets"),
                "live_challenge_source": rt.get("live_challenge_source"),
                "v1_verdict": rt.get("verdict"),
                "verdict": c["verdict"],
                "verdict_reason": c["reason"],
                "payto_comparable": c.get("payto_comparable"),
                "payto_equal": c.get("payto_equal"),
                "declared_terms_present": c.get("declared_terms_present"),
                "reproduce": rt.get("reproduce"),
            })
    return rows


def population(run):
    """The manifest-layer census over ALL origins read.

    This CANNOT be derived from the route rows: an origin whose manifest did not answer 200, or
    declared no price, contributes ZERO route rows, so a distribution computed over `rows` can
    only ever see the subset that HAS rows (it reported `{RECONCILED: 10}` for a 100-origin run
    before this function existed). The denominator lives with the run that established it."""
    return {
        "origins_read": len(run["records"]),
        "manifest_classes": dict(collections.Counter(r["klass"] for r in run["records"])),
    }


# Verdicts in which the amount pairing WAS established, so the row may be counted as an
# agreement or a divergence. Anything else is withheld from both counts.
AMOUNT_PAIRED = ("OK", "AMOUNT_OK_NETWORK_NOTATION", "AMOUNT_OK_NETWORK_DIFFERS",
                 "AMOUNT_OK_TERMS_INDETERMINATE", "AMOUNT_OK_TERMS_UNOBSERVED", "DIVERGENT")


def aggregates(rows):
    """EVERY row-derived number the report prints, computed here and nowhere else.

    It deliberately does NOT report a manifest-class distribution -- see population().

    `comparable_routes` counts rows whose amount could actually be PAIRED, which is not the same
    as rows that happen to carry two amount values. The first draft counted the latter and so
    reported 30 agreements, 10 of which were the declared entry's amount compared against a
    DIFFERENT entry's price on a challenge quoting two different prices."""
    probed = [r for r in rows if r.get("live_http") is not None]
    paired = [r for r in rows if r["verdict"] in AMOUNT_PAIRED]
    agree = [r for r in paired if r["verdict"] != "DIVERGENT"]
    unpairable = [r for r in rows if r["verdict"] == "AMOUNT_INDETERMINATE"]
    return {
        "hosts": len(set(r["host"] for r in rows)),
        "origins_contributing_rows": len(set(r["origin"] for r in rows)),
        "route_rows": len(rows),
        "probed_routes": len(probed),
        "probed_without_a_402": len([r for r in probed if r["verdict"] == "INDETERMINATE"]),
        "comparable_routes": len(paired),
        "amount_agrees": len(agree),
        "amount_divergent": len(paired) - len(agree),
        "amount_unpairable": len(unpairable),
        "hosts_with_a_comparable_route": sorted(set(r["host"] for r in paired)),
        "verdicts": dict(collections.Counter(r["verdict"] for r in rows)),
        "v1_verdicts": dict(collections.Counter(r["v1_verdict"] for r in rows)),
        "false_mismatch_rows": len([r for r in rows if r["v1_verdict"] in
                                    ("PAYTO_MISMATCH", "NETWORK_MISMATCH")]),
        "hosts_with_an_ok_route": sorted(set(r["host"] for r in rows if r["verdict"] == "OK")),
    }


def selftest():
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print("FAIL %-62s got %r want %r" % (name, got, want))

    chk("caip2 and bare name one family", net_family("solana:5eykt4Us"), net_family("solana"))
    chk("eip155 family", net_family("eip155:8453"), "eip155")
    chk("missing network withheld as None", net_family(None), None)
    chk("blank network withheld as None", net_family("   "), None)
    chk("non-str network withheld", net_family(42), None)
    chk("evm addr detected", is_evm_addr("0x" + "a" * 40), True)
    chk("base58 solana addr is NOT evm", is_evm_addr("7g73plhkvapbgtzg5esae4coe7qrvettkfhtxqzbyggc"), False)
    chk("short 0x is not evm", is_evm_addr("0xdeadbeef"), False)

    # Every case below is its OWN fixture off an asserted-actionable BASE, so no single condition
    # can carry two cases at once.
    EVM = "0x" + "a" * 40
    base = {"verdict": "OK", "declared_amount": "10000", "live_amount": "10000",
            "declared_network": "eip155:8453", "live_network": "eip155:8453",
            "declared_payto": EVM, "live_payto": EVM,
            "declared_asset": "0x" + "b" * 40, "live_asset": "0x" + "b" * 40,
            "live_networks": ["eip155:8453"], "live_amounts": ["10000"],
            "live_assets": ["0x" + "b" * 40], "live_accepts_n": 1, "live_http": 402}
    chk("base fixture is OK (asserted actionable)", classify(dict(base))["verdict"], "OK")

    # ---- 1. the REAL saylorinnovations shape: the scalar tuple is a DIFFERENT entry AND the
    #         challenge quotes two prices, so the declared entry's price was never observed.
    #         This is the row the first draft counted as an amount agreement.
    saylor = dict(base, live_accepts_n=6,
                  live_networks=["eip155:137", "eip155:8453", "solana:5eykt4Us"],
                  live_amounts=["10000", "101665"],
                  declared_network="solana:5eykt4Us", live_network="eip155:8453",
                  declared_payto="7lsjfrjf8fnsb8va9u7n3wen25smqlpob3svyuvxacy7",
                  live_payto="0x" + "c" * 40, verdict="PAYTO_MISMATCH")
    r = classify(saylor)
    chk("multi + non-corresponding + 2 amounts -> AMOUNT_INDETERMINATE",
        r["verdict"], "AMOUNT_INDETERMINATE")
    chk("  it is NOT counted as an agreement", r["verdict"] in AMOUNT_PAIRED, False)
    chk("  it is NEVER a mismatch or a divergence",
        ("MISMATCH" in r["verdict"]) or r["verdict"] == "DIVERGENT", False)
    chk("  payTo is not asserted comparable", r["payto_comparable"], False)
    chk("  the reason names BOTH amounts", "101665" in r["reason"] and "10000" in r["reason"], True)
    chk("  the reason names both networks",
        "solana:5eykt4Us" in r["reason"] and "eip155:8453" in r["reason"], True)
    chk("  declared network recorded as still offered",
        r["declared_terms_present"]["network"], True)

    # ---- 2. the REAL 47620.xyz shape: MULTI-entry, but the scalar tuple IS the declared entry.
    #         The first draft demoted these 9 rows and published a false reason about the host.
    x47 = dict(base, live_accepts_n=3,
               live_networks=["eip155:137", "eip155:8453", "solana:5eykt4Us"],
               live_amounts=["20000"], declared_amount="20000", live_amount="20000",
               verdict="OK")
    r = classify(x47)
    chk("multi BUT corresponding network -> OK, not demoted", r["verdict"], "OK")
    chk("  and its reason makes NO different-entry claim",
        "different entry" in r["reason"], False)

    # ---- 3. multi + non-corresponding + exactly ONE amount: amount is unambiguous, terms are not
    one_amt = dict(saylor, live_amounts=["10000"])
    r = classify(one_amt)
    chk("multi + non-corresponding + 1 amount -> TERMS_INDETERMINATE",
        r["verdict"], "AMOUNT_OK_TERMS_INDETERMINATE")
    chk("  counted as an agreement (the amount is unambiguous)",
        r["verdict"] in AMOUNT_PAIRED, True)
    chk("  payTo still not comparable", r["payto_comparable"], False)

    # ---- 4. the mpp.hyreagent.fun shape: single entry, same family, different notation
    r = classify(dict(base, declared_network="solana:5eykt4Us", live_network="solana",
                      live_networks=["solana"], verdict="NETWORK_MISMATCH",
                      declared_payto="7g73plhk", live_payto="7g73plhk"))
    chk("notation-only network diff -> NETWORK_NOTATION", r["verdict"], "AMOUNT_OK_NETWORK_NOTATION")
    chk("  base58 payTo is NOT case-comparable", r["payto_comparable"], False)
    chk("  it is NEVER a mismatch", "MISMATCH" in r["verdict"], False)

    # ---- 5. DIVERGENT must be REACHABLE by two different routes, or its 0 is a dead branch
    r = classify(dict(base, live_amount="99999"))
    chk("corresponding row, amounts differ -> DIVERGENT", r["verdict"], "DIVERGENT")
    chk("  and names both numbers", "99999" in r["reason"] and "10000" in r["reason"], True)
    r = classify(dict(saylor, live_amounts=["99999"], live_amount="99999"))
    chk("non-corresponding single-amount row can ALSO be DIVERGENT", r["verdict"], "DIVERGENT")

    # ---- 6. an unobserved network must NOT fall through to OK (the ungated-return class)
    r = classify(dict(base, declared_network=None, verdict="X"))
    chk("declared network unobserved -> TERMS_UNOBSERVED, not OK",
        r["verdict"], "AMOUNT_OK_TERMS_UNOBSERVED")
    chk("  and makes no 'no other term disagrees' claim",
        "no other term disagrees" in r["reason"], False)
    r = classify(dict(base, live_network=None, live_networks=[], verdict="X"))
    chk("live network unobserved -> TERMS_UNOBSERVED, not OK",
        r["verdict"], "AMOUNT_OK_TERMS_UNOBSERVED")

    # ---- 7. a genuinely different chain family on a SINGLE-entry challenge
    r = classify(dict(base, declared_network="solana:x", live_network="eip155:8453",
                      live_networks=["eip155:8453"]))
    chk("single-entry different chain family -> NETWORK_DIFFERS",
        r["verdict"], "AMOUNT_OK_NETWORK_DIFFERS")
    # ---- 7b. multi-entry family mismatch must NOT reach the accusation-shaped verdict
    r = classify(dict(saylor, declared_network="bitcoin:x"))
    chk("multi-entry family mismatch is withheld, not NETWORK_DIFFERS",
        r["verdict"], "AMOUNT_INDETERMINATE")

    # ---- 8. withheld comparisons, and the HTTP status must reach the reason
    chk("unprobed row -> INDETERMINATE",
        classify({"verdict": "NOT_PROBED_CAP"})["verdict"], "INDETERMINATE")
    chk("  keeps v1's reason",
        "NOT_PROBED_CAP" in classify({"verdict": "NOT_PROBED_CAP"})["reason"], True)
    chk("NO_CHALLENGE on a 404 says so, so it cannot read as a broken paywall",
        "HTTP 404" in classify({"verdict": "NO_CHALLENGE", "live_http": 404})["reason"], True)
    chk("  and omits the status when there is none",
        classify({"verdict": "NO_CHALLENGE"})["reason"], "not compared: NO_CHALLENGE")
    chk("free row -> FREE_AS_DECLARED",
        classify({"verdict": "FREE_AS_DECLARED"})["verdict"], "FREE_AS_DECLARED")
    chk("missing live amount is withheld, not divergent",
        classify(dict(base, live_amount=None, verdict="X"))["verdict"], "INDETERMINATE")
    chk("missing declared amount is withheld, not divergent",
        classify(dict(base, declared_amount="", verdict="X"))["verdict"], "INDETERMINATE")

    # ---- 9. aggregates are derived, never typed, and must EXCLUDE the unpairable rows
    rows = [{"host": "a", "origin": "https://a", "declared_amount": "1", "live_amount": "1",
             "live_http": 402, "verdict": "OK", "v1_verdict": "OK",
             "manifest_class": "RECONCILED"},
            {"host": "a", "origin": "https://a", "declared_amount": "1", "live_amount": "2",
             "live_http": 402, "verdict": "DIVERGENT", "v1_verdict": "OK",
             "manifest_class": "RECONCILED"},
            {"host": "c", "origin": "https://c", "declared_amount": "1", "live_amount": "1",
             "live_http": 402, "verdict": "AMOUNT_INDETERMINATE", "v1_verdict": "PAYTO_MISMATCH",
             "manifest_class": "RECONCILED"},
            {"host": "b", "origin": "https://b", "declared_amount": "", "live_amount": None,
             "live_http": None, "verdict": "INDETERMINATE", "v1_verdict": "NOT_PROBED_CAP",
             "manifest_class": "SKIP_NO_MANIFEST"}]
    a = aggregates(rows)
    chk("agg hosts", a["hosts"], 3)
    chk("agg probed", a["probed_routes"], 3)
    chk("agg comparable EXCLUDES the unpairable row", a["comparable_routes"], 2)
    chk("agg agree", a["amount_agrees"], 1)
    chk("agg divergent", a["amount_divergent"], 1)
    chk("agg counts the unpairable row separately", a["amount_unpairable"], 1)
    chk("agg: an unpairable row is in NEITHER count",
        a["amount_agrees"] + a["amount_divergent"] + a["amount_unpairable"], 3)
    chk("agg ok-hosts listed", a["hosts_with_an_ok_route"], ["a"])
    chk("agg comparable-hosts listed", a["hosts_with_a_comparable_route"], ["a"])
    chk("agg probed-without-402", a["probed_without_a_402"], 0)
    chk("agg makes NO manifest-class claim it cannot support", "manifest_classes" in a, False)
    chk("agg counts origins that contributed rows", a["origins_contributing_rows"], 3)

    # population() sees origins that contribute NO row -- the case a row-derived count drops
    run = {"records": [{"klass": "RECONCILED", "routes": [{}]},
                       {"klass": "SKIP_NO_MANIFEST", "routes": []},
                       {"klass": "SKIP_NO_MANIFEST", "routes": []}]}
    pp = population(run)
    chk("population counts EVERY origin read", pp["origins_read"], 3)
    chk("population sees the row-less origins",
        pp["manifest_classes"], {"RECONCILED": 1, "SKIP_NO_MANIFEST": 2})

    # ---- 6. THE DEFECT THIS BLOCK EXISTS FOR, and why the old legs missed it ---------------
    # `payto_comparable` already had a leg (section 1 above). `payto_equal` -- the field a
    # machine consumer actually reads -- had NONE, at either call site. So 13 published rows
    # carried `payto_comparable: false` beside `payto_equal: true`: an equality claim on a
    # base58 payee that the module docstring says must be reported "rather than asserted equal".
    # One leg covered the flag and left the value ungraded.
    b58 = "7g73plhkvapbgtzg5esae4coe7qrvettkfhtxqzbyggc"
    folded = dict(base, declared_network="solana:5eykt4Us", live_network="solana",
                  declared_amount="20000", live_amount="20000",
                  declared_payto=b58, live_payto=b58,
                  live_networks=["solana"], live_amounts=["20000"], live_accepts_n=1)
    r = classify(folded)
    chk("folded base58: notation verdict", r["verdict"], "AMOUNT_OK_NETWORK_NOTATION")
    chk("  base58 payTo is NOT comparable", r["payto_comparable"], False)
    chk("  and payto_equal is WITHHELD, not asserted true", r["payto_equal"], None)
    chk("  the row cannot say not-comparable AND equal at once",
        r["payto_comparable"] is False and r["payto_equal"] is not None, False)

    evm = dict(folded, declared_payto="0x" + "d" * 40, live_payto="0x" + "D" * 40)
    r = classify(evm)
    chk("EVM payTo IS comparable across case", r["payto_comparable"], True)
    chk("  and folding an EVM pair is an identity claim", r["payto_equal"], True)
    r = classify(dict(evm, live_payto="0x" + "e" * 40))
    chk("EVM payTo genuinely different -> equal False", r["payto_equal"], False)

    # ---- 7. multi_entry(): a count, and only an actual int counts -------------------------
    chk("multi_entry: 6 entries", multi_entry({"live_accepts_n": 6}), True)
    chk("multi_entry: 1 entry", multi_entry({"live_accepts_n": 1}), False)
    chk("multi_entry: 0 entries", multi_entry({"live_accepts_n": 0}), False)
    chk("multi_entry: unobserved count is NOT multi", multi_entry({}), False)
    chk("multi_entry: None is NOT multi", multi_entry({"live_accepts_n": None}), False)
    chk("multi_entry: a bool is rejected by the `> 1` comparison itself, no clause needed",
        multi_entry({"live_accepts_n": True}), False)
    chk("multi_entry: a STRING count is not a count",
        multi_entry({"live_accepts_n": "6"}), False)

    # ---- 8. declared_entry_identified(): three states, and the dedupe is the whole point ---
    chk("ident: single entry -> the scalar IS the challenge",
        declared_entry_identified({"live_accepts_n": 1}), True)
    chk("ident: 3 entries / 3 distinct networks is LOSSLESS (the 47620.xyz shape)",
        declared_entry_identified({"live_accepts_n": 3, "declared_network": "solana:5eykt4Us",
                                   "live_networks": ["eip155:137", "eip155:8453",
                                                     "solana:5eykt4Us"]}), True)
    chk("ident: 6 entries / 4 networks is LOSSY (the saylorinnovations shape)",
        declared_entry_identified({"live_accepts_n": 6, "declared_network": "eip155:8453",
                                   "live_networks": ["eip155:137", "eip155:8453", "solana",
                                                     "eip155:1"]}), False)
    chk("ident: lossless set that does NOT contain the declared network",
        declared_entry_identified({"live_accepts_n": 2, "declared_network": "eip155:999",
                                   "live_networks": ["eip155:1", "eip155:8453"]}), False)
    chk("ident: multi-entry with NO network set is None, a THIRD state not a False",
        declared_entry_identified({"live_accepts_n": 4, "declared_network": "eip155:8453",
                                   "live_networks": []}), None)
    chk("ident: multi-entry with no DECLARED network is None",
        declared_entry_identified({"live_accepts_n": 2, "live_networks": ["a", "b"]}), None)

    # ---- 9. amount_divergence_assertable(): membership survives the dedupe, mapping does not
    #         A deduped set is exact for MEMBERSHIP and lossy for the entry MAPPING, and those
    #         two facts license different claims. That asymmetry is this predicate.
    lossy = {"live_accepts_n": 6, "declared_network": "eip155:8453",
             "live_networks": ["eip155:137", "eip155:8453", "solana", "eip155:1"]}
    chk("assertable: single-entry mismatch is always assertable",
        amount_divergence_assertable({"live_accepts_n": 1})[0], True)
    chk("assertable: identified entry -> assertable",
        amount_divergence_assertable({"live_accepts_n": 3, "declared_network": "a",
                                      "live_networks": ["a", "b", "c"]})[0], True)
    chk("assertable: lossy + declared amount IS offered elsewhere -> WITHHELD",
        amount_divergence_assertable(dict(lossy, declared_amount="500",
                                          live_amounts=["100", "500"]))[0], False)
    chk("assertable: lossy + declared amount offered by NO entry -> still assertable",
        amount_divergence_assertable(dict(lossy, declared_amount="777",
                                          live_amounts=["100", "500"]))[0], True)
    chk("assertable: lossy + amount set NOT observed -> WITHHELD, never convicted",
        amount_divergence_assertable(dict(lossy, declared_amount="777",
                                          live_amounts=[]))[0], False)
    chk("assertable: the withheld reason names the mechanism, not just 'indeterminate'",
        "artifact of which entry" in amount_divergence_assertable(
            dict(lossy, declared_amount="500", live_amounts=["100", "500"]))[1], True)

    # ---- 10. the hole END TO END: a same-network multi-entry challenge reaching STEP 2 ------
    #          STEP 1 only fires on `multi and not corresponds`, so this row fell straight
    #          through to the scalar amount comparison and was published DIVERGENT.
    samenet = dict(base, live_accepts_n=4, live_networks=["eip155:8453"],
                   live_amounts=["10000", "20000"],
                   declared_network="eip155:8453", live_network="eip155:8453",
                   declared_amount="20000", live_amount="10000",
                   declared_payto="0x" + "d" * 40, live_payto="0x" + "e" * 40)
    r = classify(samenet)
    chk("same-network multi-entry: NOT published as DIVERGENT", r["verdict"] != "DIVERGENT", True)
    chk("  it is withheld as AMOUNT_INDETERMINATE", r["verdict"], "AMOUNT_INDETERMINATE")
    chk("  it is counted as NEITHER agreement nor divergence",
        r["verdict"] in AMOUNT_PAIRED, False)
    chk("  payTo is not asserted across an unidentified entry", r["payto_comparable"], False)
    chk("  nor is payto_equal", r["payto_equal"], None)
    chk("  and set membership is still recorded",
        r["declared_terms_present"]["amount"], True)
    # same shape, but the declared amount is offered by NO entry -> the conviction STANDS
    r = classify(dict(samenet, declared_amount="99999"))
    chk("same-network multi-entry, amount offered nowhere -> DIVERGENT stands",
        r["verdict"], "DIVERGENT")
    chk("  and its reason says WHY the conviction is safe",
        "NO entry" in r["reason"], True)

    # ---- 11. THE LEG MUTATION PROVED WAS MISSING ------------------------------------------
    # Legs 6 and 10 were written to cover `payto_claim`'s entry-identity gate and covered
    # NEITHER: `bin/mutate_check.py` deleted the gate and the suite stayed 87/87 green. Leg 6's
    # base58 row is withheld by the EVM-shape check, not by the gate, and leg 10's row never
    # REACHES payto_claim at all -- it returns from the AMOUNT_INDETERMINATE branch above it.
    # To reach the gate the amounts must AGREE, so the row falls through to the OK branch, and
    # the payees must both be EVM-shaped so the shape check cannot do the withholding instead.
    # Without the gate this row publishes `payto_equal: false` about a named host, read off one
    # of 4 entries whose per-entry mapping the capture did not keep.
    lossy_ok = dict(base, live_accepts_n=4, live_networks=["eip155:8453"],
                    live_amounts=["20000"], declared_network="eip155:8453",
                    live_network="eip155:8453", declared_amount="20000", live_amount="20000",
                    declared_payto="0x" + "d" * 40, live_payto="0x" + "e" * 40)
    chk("the gate fixture really is LOSSY", declared_entry_identified(lossy_ok), False)
    r = classify(lossy_ok)
    chk("amounts agree on a lossy multi-entry row -> it reaches the OK branch",
        r["verdict"], "OK")
    chk("  payTo is NOT comparable across an unidentified entry", r["payto_comparable"], False)
    chk("  and no payto_equal accusation is published", r["payto_equal"], None)
    # the SAME row with a lossless network set IS comparable -- so the gate is the only thing
    # that differs, and leg 11 cannot pass by accident on the EVM shape check
    r = classify(dict(lossy_ok, live_accepts_n=1))
    chk("the identical row, single-entry, IS compared", r["payto_comparable"], True)
    chk("  and its payees genuinely differ", r["payto_equal"], False)

    # ---- 12. classify_input() and recheck(): the grader was itself ungraded ---------------
    # `--recheck` is a registered shift check, and NOTHING tested it: mutate_check.py swapped
    # `row["v1_verdict"]` for `row["verdict"]` -- the exact field-vs-position bug the function
    # exists to avoid -- and the suite stayed 93/93 green.
    pub = {"host": "h", "url": "u", "v1_verdict": "NOT_PROBED_CAP", "live_http": 404,
           "verdict": "INDETERMINATE", "verdict_reason": "x",
           "payto_comparable": None, "payto_equal": None, "declared_terms_present": None}
    ci = classify_input(pub)
    chk("classify_input feeds classify the V1 verdict, by NAME", ci["verdict"], "NOT_PROBED_CAP")
    chk("  and does not mutate the row it was handed", pub["verdict"], "INDETERMINATE")
    c = classify(ci)
    chk("  so an unprobed row keeps its v1 REASON", c["reason"],
        "not compared: NOT_PROBED_CAP, route answered HTTP 404")
    chk("  reading the row's own verdict instead would lose that",
        classify(dict(pub))["reason"], "declared or live amount not observed")

    agreeing = dict(pub, verdict=c["verdict"], verdict_reason=c["reason"])
    tbl_ok = {"routes": [agreeing]}
    chk("recheck: a table whose rows agree with classify() returns 0", recheck(tbl_ok), 0)
    tbl_bad = {"routes": [dict(agreeing, payto_equal=True)]}
    chk("recheck: ONE disagreeing derived field returns 1", recheck(tbl_bad), 1)
    tbl_vbad = {"routes": [dict(agreeing, verdict="OK")]}
    chk("recheck: a wrong stored verdict returns 1", recheck(tbl_vbad), 1)

    # ---- 13. FINDINGS FROM THE OPUS CRITIC, 2026-11-04 -----------------------------------
    # (a) term_present(): a negative fact about a named host needs BOTH operands observed.
    chk("term_present: both observed, present", term_present("a", ["a", "b"]), True)
    chk("term_present: both observed, absent", term_present("c", ["a", "b"]), False)
    chk("term_present: declared NOT observed -> None, never False",
        term_present(None, ["a", "b"]), None)
    chk("term_present: empty declared -> None", term_present("", ["a"]), None)
    chk("term_present: live set NOT observed -> None, never False", term_present("a", []), None)
    chk("term_present: neither observed -> None", term_present(None, []), None)
    # the `bool(x) and x in s` form the critic proposed would answer False here, not None --
    # which is the wrong value rather than a withheld one. This leg pins the difference.
    chk("term_present is NOT the `bool(x) and x in s` form",
        term_present(None, ["a"]) is False, False)

    unobs_net = dict(base, live_accepts_n=6, live_networks=["eip155:137", "eip155:8453", "solana"],
                     live_amounts=["10000", "20000"], declared_network=None,
                     live_network="eip155:8453", declared_amount="20000", live_amount="10000",
                     declared_asset=None, live_assets=[])
    r = classify(unobs_net)
    chk("unobserved declared network -> terms_present network is WITHHELD",
        r["declared_terms_present"]["network"], None)
    chk("  and the asset, also unobserved, is WITHHELD",
        r["declared_terms_present"]["asset"], None)
    chk("  while the amount, both sides observed, is still answered",
        r["declared_terms_present"]["amount"], True)

    # (b) an UNOBSERVED amount set must not become "the challenge's only amount".
    noamts = dict(base, live_accepts_n=4,
                  live_networks=["eip155:8453", "solana"], live_amounts=[],
                  declared_network="solana", live_network="eip155:8453",
                  declared_amount="20000", live_amount="10000")
    r = classify(noamts)
    chk("STEP 1 with an UNOBSERVED amount set -> not DIVERGENT",
        r["verdict"] != "DIVERGENT", True)
    chk("  it is withheld as AMOUNT_INDETERMINATE", r["verdict"], "AMOUNT_INDETERMINATE")
    chk("  and the reason says the set was not observed",
        "NOT observed" in r["reason"], True)
    chk("  it makes no claim about a 'only amount' it never read",
        "only amount" in r["reason"], False)
    # the SAME row with the set observed and one distinct amount still convicts
    r = classify(dict(noamts, live_amounts=["10000"]))
    chk("one OBSERVED distinct amount still yields DIVERGENT", r["verdict"], "DIVERGENT")
    chk("  and names the challenge's only amount", "only amount" in r["reason"], True)

    # (c) "no other term disagrees" may only be said when the payee WAS compared.
    r = classify(dict(lossy_ok, live_accepts_n=1,
                      declared_payto="0x" + "d" * 40, live_payto="0x" + "d" * 40))
    chk("payee compared and equal -> OK says no other term disagrees",
        r["reason"], "declared and live amount agree and no other term disagrees")
    r = classify(lossy_ok)
    chk("payee WITHHELD -> OK must not claim 'no other term disagrees'",
        "no other term disagrees" in r["reason"], False)
    chk("  and the reason names the payee as not compared",
        "payee is NOT compared" in r["reason"], True)
    chk("  the verdict is still OK on the amount", r["verdict"], "OK")

    print("selftest: %d ok, %d failed" % (ok, fail))
    return 1 if fail else 0


# Derived fields of a published row that `classify()` is the sole authority for. `verdict_reason`
# is deliberately NOT here: it is prose, and grading it would turn every wording improvement into
# a published-file failure. It is reported as drift instead.
DERIVED = ("verdict", "payto_comparable", "payto_equal", "declared_terms_present")


def classify_input(row):
    """Reconstruct the `classify()` INPUT from a published row.

    `build()` stores the run's own verdict as `v1_verdict` and `classify()`'s output as
    `verdict`. Feeding a published row straight back in therefore hands `classify()` its own
    OUTPUT as the v1 input -- which silently mis-grades every row whose v1 class was one of
    UNPROBED, because the classified name ("INDETERMINATE") is not in that tuple. The field is
    identified by NAME, not by position in the row.
    """
    rt = dict(row)
    rt["verdict"] = row.get("v1_verdict")
    return rt


def recheck(table, repair_to=None):
    """Re-run `classify()` over EVERY stored row and report rows whose derived fields disagree.

    `--reanalyse` recomputes the AGGREGATES from the stored rows, so it can only catch a stated
    total that disagrees with the rows under it. It never re-runs `classify()`, which means the
    per-row derived fields were an UNGRADED source: a stale or wrong one is invisible to it, and
    the aggregates stay internally consistent while a row-level claim is wrong. That is exactly
    how 13 rows came to carry `payto_comparable: false` beside `payto_equal: true`. This mode
    closes that gap, and prints the population it compared so "0 disagree" cannot come from a
    subset.
    """
    rows = table["routes"]
    compared, bad, reason_drift = 0, [], 0
    for i, row in enumerate(rows):
        c = classify(classify_input(row))
        compared += 1
        diff = {f: (row.get(f), c.get(f)) for f in DERIVED if row.get(f) != c.get(f)}
        if diff:
            bad.append((i, row.get("host"), row.get("url"), diff))
        if row.get("verdict_reason") != c.get("reason"):
            reason_drift += 1

    print("rows in file           : %d" % len(rows))
    print("rows re-run through classify(): %d" % compared)
    if compared != len(rows):
        print("POPULATION GAP: compared %d of %d" % (compared, len(rows)))
        return 1
    print("rows DISAGREEING on %s: %d" % (", ".join(DERIVED), len(bad)))
    shown = collections.Counter()
    for _, host, _, diff in bad:
        shown[(host,) + tuple(sorted("%s %r->%r" % (f, a, b) for f, (a, b) in diff.items()))] += 1
    for k, n in shown.most_common():
        print("   %-26s %s  x%d" % (k[0], " | ".join(k[1:]), n))
    print("rows whose prose reason drifted (NOT a failure): %d" % reason_drift)

    if repair_to:
        fixed = [dict(r) for r in rows]
        for i, row in enumerate(fixed):
            c = classify(classify_input(row))
            for f in DERIVED:
                row[f] = c.get(f)
            row["verdict_reason"] = c.get("reason")
        out = dict(table)
        out["routes"] = fixed
        out["aggregates"] = aggregates(fixed)
        with open(repair_to, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("REPAIRED -> %s (%d rows rewritten from classify())" % (repair_to, len(fixed)))
        return 0
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="saved x402_route_recon_multi run JSON")
    ap.add_argument("--out", help="write the publishable per-route table here")
    ap.add_argument("--reanalyse", help="recompute aggregates from an already-published table")
    ap.add_argument("--recheck", help="re-run classify() over a published table's rows and "
                                      "fail if any derived field disagrees")
    ap.add_argument("--repair", help="with --recheck: write a corrected table to this path")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.recheck:
        return recheck(json.load(open(a.recheck)), a.repair)

    if a.reanalyse:
        t = json.load(open(a.reanalyse))
        rows = t["routes"]
        agg = aggregates(rows)
        print(json.dumps(agg, indent=1, sort_keys=True))
        stated = t.get("aggregates")
        if stated is not None:
            same = stated == agg
            print("\nSTATED aggregates in the file %s the ones recomputed from its own rows"
                  % ("MATCH" if same else "DISAGREE WITH"))
            if not same:
                return 1
        return 0

    if not a.run or not a.out:
        ap.error("--run and --out are both required (or use --reanalyse / --selftest)")

    run = json.load(open(a.run))
    rows = build(run)
    agg = aggregates(rows)
    doc = {
        "tool": "bin/x402_route_table.py",
        "derived_from": {
            "tool": run.get("tool"),
            "generated_at": run.get("generated_at"),
            "finished_at": run.get("finished_at"),
            "user_agent": run.get("user_agent"),
            "method": run.get("method"),
            "published_host_request_cap": run.get("published_host_request_cap"),
            "hosts_over_cap": run.get("hosts_over_cap"),
        },
        "what_this_is": (
            "One row per DECLARED route of 100 public x402 origins, pairing the manifest's "
            "declared payment terms with the live 402 challenge where the route was probed. "
            "Produced by an autonomous AI studio (Quietforge). Read LIMITS before quoting any "
            "row: most rows were never probed, and no host in this table is mispriced."),
        "limits": [
            "ONE vantage point, ONE window (the probes finished 2026-11-02T04:14:43Z). A route "
            "that was unreachable from here is UNREADABLE, never a failure of the host.",
            "Only %d of %d declared routes were probed at all; the rest are INDETERMINATE, "
            "overwhelmingly because the published 15-requests-per-host budget was reached. A "
            "verdict distribution over all rows is therefore mostly a statement about our "
            "budget, not about hosts." % (agg["probed_routes"], agg["route_rows"]),
            "The capture stored the live challenge's networks/amounts/assets as sorted DISTINCT "
            "SETS, not per-entry tuples, so `accepts[]` entries cannot be matched pairwise from "
            "these rows. Where a live challenge carried several entries, non-amount terms are "
            "reported INDETERMINATE rather than compared.",
            "Addresses here are case-folded. That is lossless for EVM (EIP-55 checksum is only "
            "casing) and LOSSY for base58 Solana addresses, which are case-sensitive; those rows "
            "carry payto_comparable=false instead of an equality claim.",
            "`v1_verdict` is kept on every row so this table can be checked against the run it "
            "came from. Where `verdict` and `v1_verdict` differ, v1 was wrong; see the tool "
            "docstring for the two classes and the bytes that refute them.",
        ],
        "population": population(run),
        "aggregates": agg,
        "routes": rows,
    }
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    print("wrote %s (%d route rows over %d hosts)" % (a.out, len(rows), agg["hosts"]))
    print(json.dumps(agg, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
