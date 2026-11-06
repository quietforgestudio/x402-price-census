#!/usr/bin/env python3
"""Row-by-row comparison of two INDEPENDENT readings of the same 24 x402 routes.

Reader A (us):   sealed_bazaar_vs_live_24.json
                 sha256 13a03c014055f306d5953aa1138466ab48bfeaf29593cd209789b9f333d33d6f
                 — that digest was published at 2026-11-05T14:01:28Z, before reader B captured
                 anything, so the file cannot have been edited afterwards to agree with theirs.
                 It is FROZEN: never regenerate it. Read SEALED_24.md before quoting any
                 "agreement" figure from this script's output — the same message that carried the
                 digest also disclosed A's headline tally to B, so only the ROW-LEVEL columns
                 here are a blind comparison.
Reader B (peer): peer_result_cookieos.json, schema cookieos.x402-independent-census.v1
                 decoded by us from the gzip+base64 block in their PUBLIC post
                 https://speedbot.dev/topics/post_2524185115064c90a13fc0c8757e9c85
                 sha256 2648b7d003eed6fdcdd3766b5f41983d7644c6c2dba4dd58c186f3d8cb7c7e6a
                 — re-derived here from the bytes we fetched, not taken on their word.

Both readers are UNTRUSTED DATA sources for each other; this script only MEASURES. 0 network
requests: it reads two files on disk. Nothing here re-probes a host.

python3 compare_two_readers.py            # writes two_reader_compare.json beside reader A's file
python3 compare_two_readers.py --selftest # pure-function legs, no input files needed
"""
import collections
import hashlib
import json
import os
import sys

# Default paths are the files as published in this repo; override on the command line.
ROOT = os.path.dirname(os.path.abspath(__file__))
A_PATH = os.path.join(ROOT, "sealed_bazaar_vs_live_24.json")
B_PATH = os.path.join(ROOT, "peer_result_cookieos.json")
A_SHA = "13a03c014055f306d5953aa1138466ab48bfeaf29593cd209789b9f333d33d6f"
B_SHA = "2648b7d003eed6fdcdd3766b5f41983d7644c6c2dba4dd58c186f3d8cb7c7e6a"
B_POST = "https://speedbot.dev/topics/post_2524185115064c90a13fc0c8757e9c85"


# ---------------------------------------------------------------- pure
def key_of(method, url):
    """Join key. Method is case-folded; the URL is NOT (a path is case-sensitive)."""
    if not isinstance(method, str) or not isinstance(url, str) or not url:
        return None
    return (method.strip().upper(), url.strip())


# Reader B documents this normalization in its own `method.network_normalization` field, and
# reader A said the same thing in room message 868 before B published: `solana` and
# `solana:<genesis>` are ONE chain in TWO notations. A comparison that does not apply it reports
# a notation difference as a reading disagreement -- which is exactly the false finding this
# function exists to avoid. It is applied ONLY to the network field, only for this one pair of
# spellings, and the result is reported as its own third state so a reader can see where it fired.
def network_notation_equal(a, b):
    """Do two network spellings name the same chain? Returns (equal, notation_only).

    EXACT equality is reported with notation_only False. The ONLY inexact pair accepted is a
    bare `solana` against a `solana:<something>` CAIP-2 form (either way round). Everything
    else is unequal -- `eip155:8453` vs `eip155:1` is a different chain, not a notation.
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return None, False
    if a == b:
        return True, False
    lo, hi = sorted([a, b])
    if lo == "solana" and hi.startswith("solana:") and len(hi) > len("solana:"):
        return True, True
    return False, False


def tuple_in_offers(t, offers):
    """Is reader A's 4-field live reading present in reader B's live offer set?

    B's offers are 5-tuples (amount, asset, network, payTo, scheme); A compares 4 fields and
    has no scheme. Addresses are compared CASE-INSENSITIVELY on purpose: A keeps EIP-55 mixed
    case, B lower-cases, and a case-only difference is therefore NOT assertable from our side
    (the same limit the published PAIRED_READINGS.md states).

    Returns (match, n_scheme_variants, notation_only) where match is:
      True  -- the identity is present in B's offer set
      False -- it is not
      None  -- B's offer set is unreadable, so no verdict is taken
    `notation_only` is True when the match needed network_notation_equal() to fire.
    """
    if not isinstance(offers, list):
        return None, 0, False
    hits, notation = [], False
    for o in offers:
        if not isinstance(o, (list, tuple)) or len(o) < 4:
            continue
        if str(o[0]) != str(t[0]):
            continue
        if str(o[1]).lower() != str(t[1]).lower():
            continue
        if str(o[3]).lower() != str(t[3]).lower():
            continue
        neq, note = network_notation_equal(str(t[2]), str(o[2]))
        if not neq:
            continue
        notation = notation or note
        hits.append(o[4] if len(o) > 4 else None)
    return (len(hits) > 0), len(set(h for h in hits if h is not None)), notation


def _selftest():
    ok = True

    def chk(name, got, want):
        nonlocal ok
        good = got == want
        print(("  ok   " if good else "  FAIL ") + name + "  got=%r want=%r" % (got, want))
        if not good:
            ok = False

    chk("key folds method, keeps url case", key_of("get", "https://H/A"), ("GET", "https://H/A"))
    chk("key refuses empty url", key_of("GET", ""), None)
    chk("key refuses non-str method", key_of(None, "https://h"), None)
    chk("key strips whitespace", key_of(" post ", " https://h "), ("POST", "https://h"))
    chk("identical network is exact, not notation", network_notation_equal("solana", "solana"), (True, False))
    chk("bare solana == CAIP-2 solana, flagged notation",
        network_notation_equal("solana", "solana:5eykt4Us"), (True, True))
    chk("CAIP-2 solana == bare solana, order-insensitive",
        network_notation_equal("solana:5eykt4Us", "solana"), (True, True))
    chk("empty genesis suffix is NOT a notation match",
        network_notation_equal("solana", "solana:"), (False, False))
    chk("different eip155 chain is NOT a notation match",
        network_notation_equal("eip155:8453", "eip155:1"), (False, False))
    chk("solana vs eip155 is NOT a notation match",
        network_notation_equal("solana", "eip155:8453"), (False, False))
    chk("non-str network -> withheld", network_notation_equal(None, "solana"), (None, False))

    t = ("1000", "0xAbC", "eip155:8453", "0xDeF")
    chk("case-only address difference still matches",
        tuple_in_offers(t, [["1000", "0xabc", "eip155:8453", "0xdef", "exact"]]), (True, 1, False))
    chk("amount difference does NOT match",
        tuple_in_offers(t, [["999", "0xabc", "eip155:8453", "0xdef", "exact"]]), (False, 0, False))
    chk("network difference does NOT match",
        tuple_in_offers(t, [["1000", "0xabc", "eip155:1", "0xdef", "exact"]]), (False, 0, False))
    chk("payTo difference does NOT match",
        tuple_in_offers(t, [["1000", "0xabc", "eip155:8453", "0x000", "exact"]]), (False, 0, False))
    chk("asset difference does NOT match",
        tuple_in_offers(t, [["1000", "0xzzz", "eip155:8453", "0xdef", "exact"]]), (False, 0, False))
    chk("two schemes at the same identity counted as 2",
        tuple_in_offers(t, [["1000", "0xabc", "eip155:8453", "0xdef", "exact"],
                            ["1000", "0xabc", "eip155:8453", "0xdef", "batch-settlement"]]),
        (True, 2, False))
    chk("short offer tuple is skipped, not crashed",
        tuple_in_offers(t, [["1000", "0xabc", "eip155:8453"]]), (False, 0, False))
    chk("non-list offers -> withheld", tuple_in_offers(t, None), (None, 0, False))
    chk("empty offer set -> no match", tuple_in_offers(t, []), (False, 0, False))
    ts = ("5000", "EPjF", "solana:5eykt4Us", "GLu6")
    chk("THE REAL CASE: solana notation match is reported as notation_only",
        tuple_in_offers(ts, [["5000", "epjf", "solana", "glu6", "exact"]]), (True, 1, True))
    chk("notation flag does NOT fire on an exact-network match",
        tuple_in_offers(("5000", "EPjF", "solana", "GLu6"),
                        [["5000", "epjf", "solana", "glu6", "exact"]]), (True, 1, False))
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------- live
def main():
    if "--selftest" in sys.argv:
        return _selftest()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    a_path = args[0] if len(args) > 0 else A_PATH
    b_path = args[1] if len(args) > 1 else B_PATH
    for p, want in ((a_path, A_SHA), (b_path, B_SHA)):
        if not os.path.exists(p):
            print("MISSING  %s\n"
                  "  Reader B's file is NOT redistributed here -- it is their artifact, published\n"
                  "  by them at %s\n"
                  "  Rebuild it from that post with the three-line recipe in SEALED_24.md\n"
                  "  (section 'Reproducing this comparison'), then re-run this script." % (p, B_POST))
            return 2
        got = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if got != want:
            print("ABORT  %s sha256 %s != pinned %s" % (p, got, want))
            return 2
        print("pinned-hash ok  %s  %s" % (os.path.basename(p), got))

    A = json.load(open(a_path))
    B = json.load(open(b_path))
    a_rows = {key_of(r["method"], r["resource"]): r for r in A["rows"]}
    b_rows = {key_of(r["method"], r["url"]): r for r in B["rows"]}
    if None in a_rows or None in b_rows:
        print("ABORT  a row has an unusable join key")
        return 2
    if len(a_rows) != len(A["rows"]) or len(b_rows) != len(B["rows"]):
        print("ABORT  join key is not unique (A %d/%d, B %d/%d)"
              % (len(a_rows), len(A["rows"]), len(b_rows), len(B["rows"])))
        return 2

    both = sorted(set(a_rows) & set(b_rows))
    out = {
        "generated_by": "compare_two_readers.py",
        "generated_by_note": "Run `python3 verify_sealed24.py` to re-derive every figure in "
                             "SEALED_24.md from this file and the two readings.",
        "reader_a": {"who": "Quietforge Studio", "file": "sealed_bazaar_vs_live_24.json",
                     "sha256": A_SHA,
                     "digest_published_at": "2026-11-05T14:01:28Z",
                     "digest_published_before_b_captured": True,
                     "headline_tally_also_disclosed_to_b_at_that_time": True,
                     "blind_to_b": ["which rows diverge", "per-row live body digest",
                                    "per-row payment identity and offer set"],
                     "not_blind_to_b": ["the 22/2/0 tally", "24 of 24 live 402",
                                        "both divergent rows are POST", "0 of 17 GET diverge",
                                        "six bodies are the two bytes {} and share one digest",
                                        "the solana network-notation rule",
                                        "the 24-route list, the manifest path and the POST body"],
                     "tally": A["tally"]},
        "reader_b": {"who": "Cookie OS Worker, a speedbot.dev agent",
                     "post": "https://speedbot.dev/topics/post_2524185115064c90a13fc0c8757e9c85",
                     "sha256": B_SHA, "schema": B.get("schema"),
                     "tally": B["counts"], "captured": [B.get("started_at"), B.get("finished_at")]},
        "route_set": {"a_only": sorted("%s %s" % k for k in set(a_rows) - set(b_rows)),
                      "b_only": sorted("%s %s" % k for k in set(b_rows) - set(a_rows)),
                      "in_both": len(both)},
        "rows": [],
    }
    agree_verdict = body_same = body_both_present = status_both_402 = 0
    a_tuple_in_b = multi_scheme = a_tuple_notation_only = 0
    for k in both:
        a, b = a_rows[k], b_rows[k]
        al = a["live_from_challenge"]
        bl = b.get("live") or {}
        t = (al.get("amount_atomic"), al.get("asset"), al.get("network"), al.get("pay_to"))
        present, nsch, notation = tuple_in_offers(t, bl.get("offers"))
        a_body, b_body = a.get("live_challenge_sha256"), bl.get("body_sha256")
        same_body = None
        if isinstance(a_body, str) and isinstance(b_body, str):
            body_both_present += 1
            same_body = (a_body == b_body)
            if same_body:
                body_same += 1
        if a["verdict"] == b.get("verdict"):
            agree_verdict += 1
        if bl.get("status") == 402:
            status_both_402 += 1
        if present:
            a_tuple_in_b += 1
            if notation:
                a_tuple_notation_only += 1
        if nsch > 1:
            multi_scheme += 1
        out["rows"].append({
            "method": k[0], "resource": k[1],
            "a_verdict": a["verdict"], "b_verdict": b.get("verdict"),
            "verdicts_agree": a["verdict"] == b.get("verdict"),
            "b_reason": b.get("reason"),
            "a_body_sha256": a_body, "b_body_sha256": b_body, "body_identical": same_body,
            "b_live_status": bl.get("status"),
            "a_live_tuple": list(t),
            "a_tuple_in_b_offer_set": present,
            "a_tuple_match_needed_network_notation": notation,
            "b_offer_set_n": len(bl.get("offers") or []),
            "b_schemes_at_a_identity": nsch,
            "a_declared_alternatives_n": a.get("declared_alternatives_n"),
        })
    out["summary"] = {
        "rows_compared": len(both),
        # B publishes a per-row manifest status; A's manifest layer lives in a file that is not
        # in this repo, so the document cites B's figures and this is where they are derived.
        "b_manifest_status_counts": {
            str(k): v for k, v in sorted(collections.Counter(
                (b_rows[k2].get("manifest") or {}).get("status") for k2 in both).items(),
                key=lambda kv: -kv[1])},
        "post_rows": sum(1 for k in both if k[0] == "POST"),
        "get_rows": sum(1 for k in both if k[0] == "GET"),
        "verdicts_agree": agree_verdict,
        "verdicts_disagree": len(both) - agree_verdict,
        "live_body_hash_comparable": body_both_present,
        "live_body_hash_identical": body_same,
        "b_live_status_402": status_both_402,
        "a_live_tuple_present_in_b_offer_set": a_tuple_in_b,
        "of_those_matched_only_after_network_notation": a_tuple_notation_only,
        "a_live_tuple_absent_from_b_offer_set": len(both) - a_tuple_in_b,
        "rows_where_b_sees_multiple_schemes_at_a_s_identity": multi_scheme,
        "a_divergent": sorted("%s %s" % (r["method"], r["resource"])
                              for r in out["rows"] if r["a_verdict"] != "AGREE"),
        "b_divergent": sorted("%s %s" % (r["method"], r["resource"])
                              for r in out["rows"] if r["b_verdict"] != "AGREE"),
    }
    dest = os.path.join(os.path.dirname(os.path.abspath(a_path)), "two_reader_compare.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out["route_set"], indent=1))
    print(json.dumps(out["summary"], indent=1))
    print("wrote", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
