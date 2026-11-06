#!/usr/bin/env python3
"""Re-derive every numeric and comparative claim in PAIRED_READINGS.md from
paired_readings.json, and fail loudly on any that does not hold.

Why this exists: PAIRED_READINGS.md is prose about a 43-row join of two readers' captures of the
same x402 routes, plus a third reader's capture of one route. Prose can drift from its rows. This
script builds each expected statement FROM THE ROWS and then asserts the document says it, rather
than mirroring a number typed in both places.

Python 3 standard library only. No network. Reads the two files beside it and prints.

    python3 verify_paired.py            # check the document against the rows
    python3 verify_paired.py --selftest # check this verifier's own logic, offline
"""
import collections
import hashlib
import subprocess
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROWS = HERE / "paired_readings.json"
DOC = HERE / "PAIRED_READINGS.md"

FAILED = []
PASSED = 0


def chk(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(label)


def says(doc, needle, label):
    """The document must contain `needle` literally, ignoring line wrapping.

    A rendered Markdown paragraph wraps wherever the author's editor wrapped it, so a
    line-oriented search can miss a phrase that IS present. Collapse all runs of whitespace on
    both sides before comparing. (This repo has published a vacuous negative control before by
    grepping a phrase that spanned a line break.)
    """
    flat = re.sub(r"\s+", " ", doc)
    chk(re.sub(r"\s+", " ", needle) in flat, label)


def derive(rows):
    """Everything the document is allowed to claim, computed from the rows alone."""
    d = {}
    join = rows["join"]
    P = join["rows"]
    d["paired_rows"] = len(P)
    d["verdicts"] = dict(collections.Counter(r["verdict"] for r in P))
    d["declared"] = dict(collections.Counter(r["declared_verdict"] for r in P))
    d["hosts"] = dict(collections.Counter(r["host"] for r in P))
    agree = [r for r in P if r["verdict"] == "INDEX0_AGREE"]
    d["agree"] = len(agree)
    free = [r for r in agree if "no payment challenge" in r["reason"]]
    d["both_free"] = len(free)
    d["priced"] = len(agree) - len(free)
    # Ordering is only testable where BOTH arrays hold 2+ entries.
    testable = 0
    for r in agree:
        if r in free:
            continue
        det = r.get("detail") or {}
        peer_n = len(det.get("peer_tuples") or [])
        our_n = det.get("our_live_accepts_n") or 0
        if peer_n > 1 and our_n > 1:
            testable += 1
    d["order_testable"] = testable
    d["order_single_entry"] = d["priced"] - testable
    # The prose says these are "single-entry on BOTH sides". Assert the predicate, not the count:
    # a 5-vs-1 row is also not ordering-testable but would make that sentence false.
    d["order_remainder_all_single_both_sides"] = all(
        len((r.get("detail") or {}).get("peer_tuples") or []) <= 1
        and ((r.get("detail") or {}).get("our_live_accepts_n") or 0) <= 1
        for r in agree
        if r not in free
        and not (len((r.get("detail") or {}).get("peer_tuples") or []) > 1
                 and ((r.get("detail") or {}).get("our_live_accepts_n") or 0) > 1))
    # Derived from the ROWS. The join's own `verdict_counts` summary is a second copy of the same
    # number typed into the same file; asserting the document against it would verify nothing.
    d["divergence_verdicts"] = {k: v for k, v in d["verdicts"].items()
                                if k not in ("INDEX0_AGREE", "WITHHELD_NO_OBSERVATION")}
    d["divergences_total"] = sum(d["divergence_verdicts"].values())
    d["summary_agrees_with_rows"] = all(
        join["verdict_counts"].get(k, 0) == v for k, v in d["verdicts"].items())
    d["resolved_by_peer"] = d["declared"].get("DECLARED_RESOLVED_BY_PEER", 0)
    d["our_unresolvable_that_peer_resolved"] = len(
        join["rows_our_capture_could_not_resolve_that_the_peer_did"])
    d["readers"] = len(rows["readers"])
    d["verified_digests"] = sum(1 for r in rows["readers"]
                                if str(r.get("artifact_sha256_status", "")).startswith("VERIFIED"))
    d["attributed_not_verified"] = sum(len(r.get("attributed_not_verified") or [])
                                       for r in rows["readers"])
    return d


def check_in_repo_digests(rows, doc):
    """Every digest the rows claim for a file IN THIS REPO must equal that file's actual bytes.

    A digest that quietly stops matching its own file is the defect this exists for: the route
    table was repaired after a reader had verified its digest, and the old value then described
    a file nobody could fetch any more.
    """
    claimed_in_repo = 0
    for r in rows["readers"]:
        name = r.get("artifact_in_repo")
        if not name:
            continue
        f = HERE / name
        chk(f.exists(), f"the in-repo artifact {name} exists beside this checker")
        if not f.exists():
            continue
        claimed_in_repo += 1
        raw = f.read_bytes()
        chk(hashlib.sha256(raw).hexdigest() == r["artifact_sha256"],
            f"the digest claimed for {name} equals the digest of the file in this repo")
        chk(len(raw) == r["artifact_bytes"],
            f"the byte count claimed for {name} equals the file's size")
        if r.get("artifact_sha256_at_peer_capture"):
            chk(r["artifact_sha256_at_peer_capture"] != r["artifact_sha256"],
                f"{name}'s superseded digest is recorded as a DIFFERENT value from the current "
                "one (if they were equal the supersession note would be false)")
            says(doc, r["artifact_sha256_at_peer_capture"][:8],
                 f"the document names the superseded digest of {name}")
    chk(claimed_in_repo >= 1,
        "at least one reader artifact is a file in this repo, so the legs above are not vacuous")


def check_third_reading(rows):
    """The one-route, three-operator-group block: agreements and the one field that does not."""
    t = rows["third_reading_one_route"]
    reads = t["readings"]
    by = {}
    for r in reads:
        by.setdefault(r["reader"], []).append(r)
    chk(len({r["reader"] for r in reads}) == 3,
        "the one-route block holds readings from exactly 3 distinct readers")

    CUT = "2026-11-03T23:00:00Z"   # Penny's capture is the earliest at or after this
    after = [r for r in reads if r["captured_at"] >= CUT]
    chk(len(after) == 3, "3 readings were taken at or after 2026-11-03T23:01Z")
    chk(all(r["accepts_n"] == 5 for r in after),
        "all 3 of those readings saw accepts length 5")
    chk(all(r["distinct_amounts"] == ["10000"] for r in after),
        "all 3 of those readings saw a single distinct amount, 10000")

    first = [r for r in reads if r["captured_at"] < CUT]
    chk(len(first) == 1 and first[0]["accepts_n"] == 6,
        "the earliest reading saw 6 entries")
    chk(first[0]["distinct_amounts"] == ["10000", "101665"],
        "the earliest reading saw two distinct amounts")

    rna = [r for r in reads if r["reader"] == "RNA"][0]
    ours = [r for r in reads if r.get("per_entry_tuples")][0]
    rna_t = rna["per_entry_tuples_as_published_by_rna"]
    our_t = ours["per_entry_tuples"]
    chk(len(rna_t) == len(our_t) == 5, "both tuple lists hold 5 entries")
    chk([e[0] for e in rna_t] == [e[0] for e in our_t],
        "network wire order is identical between RNA's tuples and ours")
    chk([e[2] for e in rna_t] == [e[2] for e in our_t],
        "every amount is identical between RNA's tuples and ours")
    chk([e[3] for e in rna_t] == [e[3] for e in our_t],
        "every payTo is identical between RNA's tuples and ours")
    tail_equal = [i for i in range(1, 5) if rna_t[i][1] == our_t[i][1]]
    chk(len(tail_equal) == 4,
        "entries 2-5 carry an identical asset on both sides (4 of 4)")
    chk(rna_t[0][1] != our_t[0][1],
        "entry 1 is the ONLY asset that differs -- a 0 here would make the finding vacuous")

    f = t["one_field_that_does_not_reconcile"]
    rv, ov = f["rna_published_value"], f["our_live_value"]
    chk(len(rv) - 2 == f["rna_published_value_hex_digits"] == 38,
        "RNA's value is 38 hex digits as stated")
    chk(len(ov) - 2 == f["our_live_value_hex_digits"] == 40,
        "our value is 40 hex digits as stated")
    OFF = f["deleted_at_offset"]
    chk(ov[OFF:OFF + 2] == f["deleted_chars"] and rv == ov[:OFF] + ov[OFF + 2:],
        f"deleting the two characters '{f['deleted_chars']}' at offset {OFF} of our value yields "
        "RNA's value exactly")
    others = [v for i, e in enumerate(rna_t) for j, v in ((1, e[1]), (3, e[3]))
              if str(v).startswith("0x") and not (i == 0 and j == 1)]
    # 5 values: the 0x asset and 0x payTo of entries 1-3, less entry 1's asset.
    # Entries 4-5 are Solana base58 on both fields and are not EVM.
    chk(len(others) >= 5 and all(len(a) - 2 == 40 for a in others),
        f"every OTHER EVM value in RNA's own reading (asset and payTo, {len(others)} of them) is "
        "40 hex digits")
    chk(len(rna_t[0][1]) - 2 == 38,
        "and the one excluded value -- entry 1's asset -- is the 38-digit one")
    chk(rna["raw_bytes_claimed"] == ours["raw_bytes"],
        "RNA's raw byte count equals ours")
    chk(rna["raw_body_sha256_claimed"] != ours["raw_body_sha256"],
        "RNA's raw-body digest differs from ours (the part we cannot settle)")
    return rna["raw_bytes_claimed"]


def main():
    rows = json.loads(ROWS.read_text())
    doc = DOC.read_text()
    d = derive(rows)

    # --- SHIFT 603: the published join tool's quoted leg totals must be the totals it
    # actually prints. A leg count typed into prose is a second copy of a number, and the copy
    # is the one that rots as legs are added. These RUN the tools and read their own output, so
    # the document cannot drift from them. A tool that is absent, or that exits non-zero, fails
    # here by name rather than being skipped.
    for _tool, _pat in (("route_pair.py", r"(\d+) passed, (\d+) failed"),
                        ("eip55.py", r"selftest: (\d+) ok / (\d+) failed")):
        _path = HERE / _tool
        if not _path.exists():
            chk(False, f"the report names {_tool}, so {_tool} must be published beside it")
            continue
        _r = subprocess.run([sys.executable, str(_path), "--selftest"],
                            capture_output=True, text=True, timeout=300)
        _m = re.search(_pat, _r.stdout)
        chk(_r.returncode == 0, f"{_tool} --selftest exits 0")
        chk(_m is not None, f"{_tool} --selftest prints a leg total this checker can read")
        if _m:
            chk(_m.group(2) == "0", f"{_tool} --selftest reports 0 failures")
            says(doc, f"{_m.group(1)} offline checks",
                 f"the document cites {_tool}'s OWN leg total ({_m.group(1)})")

    # --- the join's headline numbers, each BUILT from the rows ---
    says(doc, f"{d['paired_rows']} paired routes", "the paired-route count")
    says(doc, f"{d['agree']} are two-reader rows", "the two-reader count")
    says(doc, f"{d['priced']} priced `accepts[0]` agreements", "the priced-agreement count")
    says(doc, f"{d['both_free']} where both readers saw no challenge", "the both-free count")
    says(doc, f"{d['verdicts'].get('WITHHELD_NO_OBSERVATION', 0)} WITHHELD",
         "the withheld count")
    says(doc, f"{d['order_testable']} of the {d['priced']}", "the ordering-testable fraction")
    says(doc, f"{d['order_single_entry']} are single-entry on both sides",
         "the single-entry remainder")
    says(doc, f"{d['resolved_by_peer']} of {d['paired_rows']}",
         "the DECLARED_RESOLVED_BY_PEER fraction")
    says(doc, f"{d['our_unresolvable_that_peer_resolved']} rows our own capture could not resolve",
         "the resolved-indeterminate count")
    says(doc, f"{d['readers']} readers", "the reader count")
    says(doc, f"{d['verified_digests']} verified", "the verified-digest count")
    says(doc, f"{d['attributed_not_verified']} attributed-not-verified",
         "the attributed-not-verified count")

    # the zeros are the whole claim, so they are checked as zeros, by name
    chk(d["divergences_total"] == 0,
        "every divergence verdict really is absent from the rows (derived, not read off the "
        "typed summary)")
    chk(d["divergence_verdicts"] == {},
        "no row in the join carries any verdict other than INDEX0_AGREE or WITHHELD")
    chk(d["summary_agrees_with_rows"],
        "the join's typed verdict_counts summary agrees with the rows it summarises")
    chk(d["order_remainder_all_single_both_sides"],
        "every non-ordering-testable priced row really is single-entry on BOTH sides")
    for k in ("TERMS_DIVERGENT", "ORDER_DIFFERS", "OFFER_SET_DIFFERS",
              "NETWORK_NOTATION_UNCOMPARABLE"):
        chk(d["verdicts"].get(k, 0) == 0, f"{k} is absent from the rows (derived)")
        says(doc, k, f"the document names {k}")

    # host breakdown
    for host, n in sorted(d["hosts"].items()):
        says(doc, f"{host} {n}", f"the per-host row count for {host}")
    chk(sum(d["hosts"].values()) == d["paired_rows"],
        "the per-host counts sum to the paired-row count")

    # population bookkeeping: nothing silently dropped
    j = rows["join"]
    chk(j["peer_only_urls"] == [], "no URL the peer read was left unjoined")
    chk(j["our_probed_urls_the_peer_did_not_read"] == [],
        "no URL we probed was left unread by the peer")
    chk(j["malformed_peer_rows"] == [], "no peer row was malformed")
    says(doc, f"{j['our_index']['rows_with_empty_url_dropped']} of our rows carry an empty url",
         "the empty-url disclosure")
    chk(j["our_index"]["ambiguous_urls_resolved_to_nothing"] == [],
        "no ambiguous URL was resolved to a row")

    check_in_repo_digests(rows, doc)

    rna_bytes = check_third_reading(rows)
    says(doc, f"{rna_bytes:,} bytes", "RNA's byte count appears in the document")

    # every reader named in the rows must be credited in the document, and every
    # permission the rows record must appear in the document verbatim
    for r in rows["readers"]:
        says(doc, r["name"], f"the document credits {r['name']}")
        if r.get("permission_verbatim"):
            says(doc, r["permission_verbatim"],
                 f"the document quotes {r['name']}'s permission verbatim")
        # A sentence we publish only because its author permitted it, in its author's wording,
        # is the one text an editor must never "improve". Gate the bytes -- and drive the
        # requirement from the DOCUMENT, not from the row: an `if row.get(...)` guard deletes
        # its own leg when the field is removed, leaving the quote unguarded in public.
        quoted = re.search(r"^> (%s confirms [^\n]+)$" % re.escape(r["name"]), doc, re.M)
        if quoted or r.get("attribution_verbatim"):
            chk(bool(r.get("attribution_verbatim")),
                f"{r['name']}'s attribution sentence is recorded in the rows, not only in prose")
            if r.get("attribution_verbatim"):
                says(doc, r["attribution_verbatim"],
                     f"the document quotes {r['name']}'s attribution verbatim")
            chk(quoted is not None and quoted.group(1) == r.get("attribution_verbatim"),
                f"the document's blockquote IS {r['name']}'s gated sentence, whole and alone")
            # `bool(x)` passes on a placeholder like "PENDING"; require a real timestamp and a
            # real sentence of scope.
            chk(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                                  str(r.get("attribution_at", "")))),
                f"{r['name']}'s attribution_at is an ISO instant, not a placeholder")
            chk(isinstance(r.get("attribution_scope"), str) and len(r["attribution_scope"]) > 80,
                f"{r['name']}'s attribution_scope states who asked and what is not derived")
        if r.get("capture_scope_verbatim"):
            says(doc, r["capture_scope_verbatim"],
                 f"the document quotes {r['name']}'s scope note verbatim")
        says(doc, r["capture"], f"the document states {r['name']}'s capture instant")

    # Every limit in the rows must be a real sentence AND reachable from the document's own
    # Limits section. A bare `len(limits) >= 7` cannot go red while anyone is adding limits.
    chk(all(isinstance(x, str) and len(x) > 40 for x in rows["limits"]),
        "every stated limit is a sentence, not a stub")
    says(doc, "## Limits", "the document has a Limits section")
    lim = doc.split("## Limits", 1)[1] if "## Limits" in doc else ""
    chk(lim.count("\n1. ") == 1 and len(re.findall(r"^\d+\. ", lim, re.M)) >= len(rows["limits"]),
        f"the Limits section is numbered and lists at least the {len(rows['limits'])} stated limits")
    says(doc, "host is named as mispriced",
         "the document states in terms that no host is named as mispriced")

    # --- the accusation gate: a FIXED number of legs, one per (name, word) pair, so the leg
    # count cannot drift with the prose. Every occurrence of an accusing word must sit within
    # 400 characters of a disclaimer; one bare occurrence fails the pair.
    flat = re.sub(r"\s+", " ", doc).lower()
    DISCLAIMERS = ("no host is named as mispriced", "not a mispric", "this is not a misprice",
                   "not about anyone's honesty", "we cannot verify",
                   "none is cleared by this paragraph",
                   "record it as their statement", "is not published here", "our word",
                   "not a contradiction", "no reader is wrong")
    ACCUSING = ("overcharg", "mispric", "cheating", "fraud", "at fault", "broken",
                "not valid", "not a usable", "defect", "dishonest", "wrong")
    names = [h.lower() for h in d["hosts"]] + [r["name"].lower() for r in rows["readers"]]
    for name in names:
        for word in ACCUSING:
            bare = []
            for m in re.finditer(re.escape(word), flat):
                w = flat[max(0, m.start() - 400):m.start() + 400]
                if name in w and not any(x in w for x in DISCLAIMERS):
                    bare.append(m.start())
            chk(not bare,
                f"'{word}' occurs {len(bare)} time(s) within 400 chars of '{name}' with no "
                "disclaimer nearby")

    print(f"\n{PASSED} passed / {len(FAILED)} failed")
    for f in FAILED:
        print("  FAIL ", f)
    return 1 if FAILED else 0


def selftest():
    """Offline checks of this verifier's own logic, including negative controls."""
    global PASSED, FAILED
    ok = True

    def leg(cond, name):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + name)
        if not cond:
            ok = False

    # says() must see through line wrapping, and must still refuse an absent phrase
    PASSED, FAILED = 0, []
    says("a phrase that\nwraps here", "a phrase that wraps here", "wrapped")
    leg(PASSED == 1 and not FAILED, "says() matches a phrase across a line break")
    PASSED, FAILED = 0, []
    says("a phrase that wraps here", "a phrase that does not appear", "absent")
    leg(len(FAILED) == 1, "says() refuses a phrase that is absent (negative control)")

    # derive(): ordering-testable requires BOTH sides to hold 2+ entries
    def rows_of(*rs):
        return {"join": {"rows": list(rs), "verdict_counts": {}, "declared_verdict_counts": {},
                         "our_index": {}, "peer_only_urls": [],
                         "our_probed_urls_the_peer_did_not_read": [], "malformed_peer_rows": [],
                         "rows_our_capture_could_not_resolve_that_the_peer_did": []},
                "readers": []}

    def row(verdict, reason, peer_n, our_n, host="h"):
        return {"verdict": verdict, "declared_verdict": "X", "host": host, "reason": reason,
                "detail": {"peer_tuples": [{}] * peer_n, "our_live_accepts_n": our_n}}

    d = derive(rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 5, 6)))
    leg(d["order_testable"] == 1, "a 5-vs-6 entry row IS ordering-testable")
    d = derive(rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 1, 1)))
    leg(d["order_testable"] == 0 and d["order_single_entry"] == 1,
        "a 1-vs-1 entry row is NOT ordering-testable")
    d = derive(rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 5, 1)))
    leg(d["order_testable"] == 0,
        "a 5-vs-1 row is NOT ordering-testable (one side cannot reorder)")
    d = derive(rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 1, 5)))
    leg(d["order_testable"] == 0,
        "a 1-vs-5 row is NOT ordering-testable either (the other side)")
    d = derive(rows_of(row("INDEX0_AGREE", "both readers saw no payment challenge", 0, 0)))
    leg(d["both_free"] == 1 and d["priced"] == 0,
        "a both-free agreement is counted free, not priced")
    d = derive(rows_of(row("WITHHELD_NO_OBSERVATION", "nothing observed", 0, 0)))
    leg(d["agree"] == 0 and d["verdicts"].get("WITHHELD_NO_OBSERVATION") == 1,
        "a withheld row is not an agreement")

    # the divergence legs must read the ROWS, not the typed summary
    r = rows_of(row("TERMS_DIVERGENT", "an amount differs", 5, 5))
    r["join"]["verdict_counts"] = {"INDEX0_AGREE": 0, "WITHHELD_NO_OBSERVATION": 0}
    d = derive(r)
    leg(d["divergences_total"] == 1 and d["divergence_verdicts"] == {"TERMS_DIVERGENT": 1},
        "a divergent ROW is counted even when the typed summary says zero (mutation control)")
    leg(not d["summary_agrees_with_rows"],
        "a typed summary that disagrees with its rows is detected")
    r = rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 1, 1),
                row("INDEX0_AGREE", "accepts[0] agrees", 5, 1))
    d = derive(r)
    leg(d["order_testable"] == 0 and d["order_single_entry"] == 2
        and not d["order_remainder_all_single_both_sides"],
        "a 5-vs-1 row in the remainder makes 'single-entry on both sides' false (mutation control)")
    r = rows_of(row("INDEX0_AGREE", "accepts[0] agrees", 1, 1))
    leg(derive(r)["order_remainder_all_single_both_sides"],
        "a genuinely 1-vs-1 remainder passes the same leg (control)")

    # the accusation gate must fire on a bare accusation and stay quiet on a disclaimed one
    def gate(text, name="examplehost.test"):
        flat = re.sub(r"\s+", " ", text).lower()
        DISCLAIMERS = ("no host is named as mispriced", "not a mispric")
        bare = []
        for m in re.finditer("mispric", flat):
            w = flat[max(0, m.start() - 400):m.start() + 400]
            if name in w and not any(x in w for x in DISCLAIMERS):
                bare.append(m.start())
        return bare

    leg(gate("examplehost.test is mispricing its routes."),
        "the accusation gate fires on a bare accusation beside a host name")
    leg(not gate("examplehost.test served 5 entries. This is not a mispricing finding."),
        "the accusation gate stays quiet when a disclaimer is nearby (control)")
    leg(not gate("someotherhost is mispricing its routes."),
        "the accusation gate does not fire on a host it was not asked about (control)")

    # the in-repo digest leg must go red when the claimed digest stops matching its file
    if ROWS.exists() and DOC.exists():
        import copy
        base0 = json.loads(ROWS.read_text())
        doc0 = DOC.read_text()
        PASSED, FAILED = 0, []
        check_in_repo_digests(base0, doc0)
        leg(not FAILED, "the live in-repo digest claims all hold")
        tam = copy.deepcopy(base0)
        for r in tam["readers"]:
            if r.get("artifact_in_repo"):
                r["artifact_sha256"] = "0" * 64
        PASSED, FAILED = 0, []
        check_in_repo_digests(tam, doc0)
        leg(any("equals the digest of the file" in x for x in FAILED),
            "a wrong claimed digest for an in-repo file turns the leg red (mutation control)")
        tam = copy.deepcopy(base0)
        for r in tam["readers"]:
            if r.get("artifact_in_repo"):
                r["artifact_bytes"] = 1
        PASSED, FAILED = 0, []
        check_in_repo_digests(tam, doc0)
        leg(any("byte count claimed" in x for x in FAILED),
            "a wrong claimed byte count for an in-repo file turns the leg red (mutation control)")
        tam = copy.deepcopy(base0)
        for r in tam["readers"]:
            if r.get("artifact_in_repo"):
                r["artifact_sha256_at_peer_capture"] = r["artifact_sha256"]
        PASSED, FAILED = 0, []
        check_in_repo_digests(tam, doc0)
        leg(any("superseded digest is recorded as a DIFFERENT value" in x for x in FAILED),
            "a superseded digest equal to the current one turns the leg red (mutation control)")
        tam = copy.deepcopy(base0)
        for r in tam["readers"]:
            r.pop("artifact_in_repo", None)
        PASSED, FAILED = 0, []
        check_in_repo_digests(tam, doc0)
        leg(any("not vacuous" in x for x in FAILED),
            "removing every in-repo artifact fails the non-vacuity leg (mutation control)")
    else:
        leg(False, "paired_readings.json and PAIRED_READINGS.md must exist for these legs")

    # the third-reading block's 'only entry 1 differs' leg must be able to go red
    base = json.loads(ROWS.read_text()) if ROWS.exists() else None
    if base:
        PASSED, FAILED = 0, []
        check_third_reading(base)
        leg(not FAILED, "the live third-reading block passes every leg")
        import copy
        tampered = copy.deepcopy(base)
        t = tampered["third_reading_one_route"]
        rna_t = t["readings"][2]["per_entry_tuples_as_published_by_rna"]
        rna_t[1][1] = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        PASSED, FAILED = 0, []
        check_third_reading(tampered)
        leg(any("entries 2-5" in f for f in FAILED),
            "perturbing a tail asset turns the 4-of-4 leg red (mutation control)")
        tampered = copy.deepcopy(base)
        f = tampered["third_reading_one_route"]["one_field_that_does_not_reconcile"]
        f["rna_published_value"] = f["our_live_value"]
        PASSED, FAILED = 0, []
        check_third_reading(tampered)
        leg(any("'c7'" in x for x in FAILED),
            "making the two values equal turns the c7-deletion leg red (mutation control)")
        tampered = copy.deepcopy(base)
        tampered["third_reading_one_route"]["readings"][2]["raw_bytes_claimed"] = 9999
        PASSED, FAILED = 0, []
        check_third_reading(tampered)
        leg(any("byte count" in x for x in FAILED),
            "a changed byte count turns the equal-bytes leg red (mutation control)")
    else:
        leg(False, "paired_readings.json must exist for the mutation legs")

    print("\n--- PASS ---" if ok else "\n--- FAIL ---")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
