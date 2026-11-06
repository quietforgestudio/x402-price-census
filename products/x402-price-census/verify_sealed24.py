#!/usr/bin/env python3
"""Gate every figure in SEALED_24.md against the data files it cites.

A prose document that states 20 numbers is 20 chances to be wrong, and a reader cannot tell a
typo from a lie. So each number in SEALED_24.md is re-derived here from the artifacts and
compared against the literal string in the document. A figure that moves turns this red.

    python3 verify_sealed24.py          # all legs; re-derives the comparison if the peer file is here
    python3 verify_sealed24.py --no-peer  # skip only the re-derivation leg

Most legs read `two_reader_compare.json`, which is OUR derived artifact -- checking prose against
it proves our arithmetic is self-consistent and nothing more. The leg that actually closes the
loop is the LAST one: with reader B's file present (rebuild it per SEALED_24.md), it re-runs
compare_two_readers.py from the two SOURCE readings and asserts the published comparison is
exactly what they produce. Without that leg this file is a consistency check, not a verification.

0 network requests. Exit 0 = every leg green.
"""
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "SEALED_24.md")
A = os.path.join(HERE, "sealed_bazaar_vs_live_24.json")
CMP = os.path.join(HERE, "two_reader_compare.json")
SCRIPT = os.path.join(HERE, "compare_two_readers.py")
A_SHA = "13a03c014055f306d5953aa1138466ab48bfeaf29593cd209789b9f333d33d6f"
B_SHA = "2648b7d003eed6fdcdd3766b5f41983d7644c6c2dba4dd58c186f3d8cb7c7e6a"

FAILS = []


def leg(name, cond, detail=""):
    print(("  ok    " if cond else "  FAIL  ") + name + (("  -- " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    doc = open(DOC, encoding="utf-8").read()
    # Markdown wraps sentences across lines, so substring checks run against the doc with runs
    # of whitespace collapsed. Nothing else about the text is normalised.
    flat = " ".join(doc.split())
    a = json.load(open(A))
    c = json.load(open(CMP))
    s = c["summary"]

    # --- the seal itself -------------------------------------------------------------
    leg("sealed file hashes to the digest published before reader B ran",
        hashlib.sha256(open(A, "rb").read()).hexdigest() == A_SHA)
    leg("both digests appear verbatim in the document", A_SHA in doc and B_SHA in doc)
    leg("the comparison script pins both digests in its own source",
        A_SHA in open(SCRIPT).read() and B_SHA in open(SCRIPT).read())

    # --- the sealed file's own content, independent of reader B ----------------------
    rows = a["rows"]
    leg("sealed file holds 24 rows", len(rows) == 24, str(len(rows)))
    leg("sealed tally is 22 AGREE / 2 DIVERGENT / 0 UNREADABLE",
        a["tally"] == {"AGREE": 22, "DIVERGENT": 2, "UNREADABLE": 0}, json.dumps(a["tally"]))
    meth = collections.Counter(r["method"] for r in rows)
    leg("17 GET and 7 POST, as the document says", meth["GET"] == 17 and meth["POST"] == 7,
        json.dumps(dict(meth)))
    div = [r for r in rows if r["verdict"] != "AGREE"]
    leg("both divergent rows are POST", all(r["method"] == "POST" for r in div))
    leg("0 of the 17 GET rows diverge",
        sum(1 for r in rows if r["method"] == "GET" and r["verdict"] != "AGREE") == 0)
    leg("document says 0 of the 17 GET routes diverge", "0 of the 17 `GET` routes diverge" in doc)

    # --- the shared-digest claim, derived not typed ----------------------------------
    cnt = collections.Counter(r["live_challenge_sha256"] for r in rows)
    empty = hashlib.sha256(b"{}").hexdigest()
    leg("the shared digest in the document IS sha256 of the two bytes '{}'", empty in doc, empty)
    leg("exactly 6 bodies share that digest", cnt[empty] == 6, str(cnt[empty]))
    leg("document says six", "Six of the 24 bodies are the two bytes" in doc)

    # --- the one body that changed --------------------------------------------------
    eck = [r for r in rows if "api.eckari.com" in r["resource"]]
    leg("exactly one eckari row in the sealed file", len(eck) == 1)
    if eck:
        e = eck[0]
        leg("A's eckari digest 6007c6b7... is quoted correctly",
            e["live_challenge_sha256"].startswith("6007c6b7") and "6007c6b7" in doc)
        leg("A's four compared fields were all EQUAL on eckari",
            set(e["comparison"].values()) == {"EQUAL"}, json.dumps(e["comparison"]))
        leg("A returned AGREE on eckari", e["verdict"] == "AGREE")

    if True:
        # --- cross-reader legs, read off our derived comparison file -----------------
        leg("route sets identical: 24 in both, 0 only-A, 0 only-B",
            c["route_set"]["in_both"] == 24 and not c["route_set"]["a_only"]
            and not c["route_set"]["b_only"], json.dumps(c["route_set"]))
        leg("24 of 24 verdicts agree, 0 disagree",
            s["verdicts_agree"] == 24 and s["verdicts_disagree"] == 0)
        leg("both readers name the same two divergent routes",
            s["a_divergent"] == s["b_divergent"] and len(s["a_divergent"]) == 2,
            json.dumps(s["a_divergent"]))
        for u in ("x402.aispace.bot/api/v1/video/queue", "www.trezalabs.com/api/x402/speech"):
            leg("divergent route named in the document: " + u,
                u in doc and any(u in x for x in s["a_divergent"]))
        leg("24 of 24 live responses were 402 for reader B", s["b_live_status_402"] == 24)
        leg("A's identity present in B's offer set on 24 of 24",
            s["a_live_tuple_present_in_b_offer_set"] == 24
            and s["a_live_tuple_absent_from_b_offer_set"] == 0)
        leg("exactly 4 of those needed the network-notation rule",
            s["of_those_matched_only_after_network_notation"] == 4)
        notation = sorted(r["resource"] for r in c["rows"]
                          if r["a_tuple_match_needed_network_notation"])
        leg("all 4 notation routes are named in the document",
            all(u.split("://", 1)[1] in doc for u in notation), json.dumps(notation))
        leg("23 of 24 body digests identical across readers",
            s["live_body_hash_identical"] == 23 and s["live_body_hash_comparable"] == 24)
        ec = [r for r in c["rows"] if "api.eckari.com" in r["resource"]]
        leg("eckari is THE row whose body digest differs",
            len(ec) == 1 and ec[0]["body_identical"] is False
            and sum(1 for r in c["rows"] if r["body_identical"] is False) == 1)
        if ec:
            leg("B's eckari digest 53476dbc... is quoted correctly",
                ec[0]["b_body_sha256"].startswith("53476dbc") and "53476dbc" in doc)
            leg("both readers returned AGREE on eckari",
                ec[0]["a_verdict"] == "AGREE" and ec[0]["b_verdict"] == "AGREE")
        hx = [r for r in c["rows"] if "api.hyperextend.xyz" in r["resource"]]
        leg("exactly one row where B sees >1 scheme at A's payment identity",
            s["rows_where_b_sees_multiple_schemes_at_a_s_identity"] == 1)
        leg("that row is hyperextend, with 3 live offers and 2 schemes at one identity",
            len(hx) == 1 and hx[0]["b_offer_set_n"] == 3
            and hx[0]["b_schemes_at_a_identity"] == 2,
            json.dumps(hx[0]) if hx else "no row")
        leg("the sealed schema really does NOT record scheme, as the document claims",
            "scheme" not in json.dumps(a["compared_fields"]).lower()
            and "scheme" not in json.dumps(rows[0]).lower())

    # --- the capture timeline -------------------------------------------------------
    for t in ("2026-11-05T12:13:25Z", "2026-11-05T14:01:28Z", "2026-11-05T14:41:16Z"):
        leg("timeline instant present in the document: " + t, t in doc)
    leg("B's capture window in the comparison file matches the document",
        (c["reader_b"]["captured"][0] or "").startswith("2026-11-05T14:41:16")
        and (c["reader_b"]["captured"][1] or "").startswith("2026-11-05T14:42:07"),
        json.dumps(c["reader_b"]["captured"]))
    leg("the comparison file records that A's digest preceded B's capture",
        c["reader_a"]["digest_published_before_b_captured"] is True)
    leg("the comparison file ALSO records what was NOT blind to B -- the honest half",
        c["reader_a"]["headline_tally_also_disclosed_to_b_at_that_time"] is True
        and len(c["reader_a"]["not_blind_to_b"]) >= 6
        and len(c["reader_a"]["blind_to_b"]) >= 3)
    # Intervals are COMPUTED from the instants, never typed: a stated duration beside two
    # timestamps is a third number that can disagree with both.
    import datetime as _dt

    def _p(t):
        return _dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)

    seal_gap = _p("2026-11-05T14:41:16Z") - _p("2026-11-05T14:01:28Z")
    cap_gap = _p("2026-11-05T14:41:16Z") - _p("2026-11-05T12:13:25Z")

    def _hms(td):
        h, rem = divmod(int(td.total_seconds()), 3600)
        m, sec = divmod(rem, 60)
        return h, m, sec
    h, m, sec = _hms(seal_gap)
    leg("disclosure-to-capture interval in the prose equals the computed gap",
        "%d min %d s later" % (m, sec) in flat, "computed %dh%dm%ds" % (h, m, sec))
    leg("the reader-to-reader gap is stated as a LOWER BOUND, not a measurement",
        "lower bound" in flat and int(cap_gap.total_seconds()) > 0,
        "computed %s" % cap_gap)
    leg("the document states which timeline rows are only our word",
        "are **our word**" in flat and "that is not in this repo" in flat)
    leg("the document does not present A's file-write time as a capture instant",
        "the moment A's\noutput file was *written*".replace("\n", " ") in flat)

    # --- THE PROSE COPY OF EVERY HEADLINE FIGURE ------------------------------------
    # Gating the derived value and not the typed sentence beside it is how a document ends up
    # stating a number its own data contradicts. Each sentence below is BUILT from the data and
    # then required verbatim in SEALED_24.md, so a figure that moves breaks the prose leg too.
    WORDS = {21: "21", 7: "seven", 6: "Six", 5: "Five", 4: "Four", 3: "three", 2: "two", 1: "one"}
    n_rows = len(rows)
    hx = [r for r in c["rows"] if "api.hyperextend.xyz" in r["resource"]][0]
    mstat = s["b_manifest_status_counts"]
    sentences = [
        "**%d of %d verdicts agree**" % (s["verdicts_agree"], s["rows_compared"]),
        "**%d of %d** live responses were HTTP 402" % (s["b_live_status_402"], s["rows_compared"]),
        "on %d of %d routes.**" % (s["a_live_tuple_present_in_b_offer_set"], s["rows_compared"]),
        "On **%d** of" % s["of_those_matched_only_after_network_notation"],
        "**%d of %d raw 402 response bodies hashed identically**"
        % (s["live_body_hash_identical"], s["live_body_hash_comparable"]),
        "%s of the %d bodies are the two bytes" % (WORDS[cnt[empty]], n_rows),
        "B's capture records %s live offers" % WORDS[hx["b_offer_set_n"]],
        "%s of which are identical in amount" % WORDS[hx["b_schemes_at_a_identity"]],
        "There are %d `POST` routes in the set, so %d unordered pairs"
        % (s["post_rows"], s["post_rows"] * (s["post_rows"] - 1) // 2),
        "0 of the %d `GET` routes diverge" % s["get_rows"],
        "`200` on %s, `404` on %s and `405` on %s"
        % (mstat["200"], mstat["404"], mstat["405"]),
    ]
    for sent in sentences:
        leg("prose carries the derived figure verbatim: %r" % sent, sent in flat)

    # --- THE DISCLOSURE SECTION, which is the honest half of the whole document ------
    # An earlier draft of SEALED_24.md claimed "No value from the file is disclosed", which was
    # FALSE: the message carrying the digest also carried A's tally, the 402 count, the GET/POST
    # split, the six-{} finding and the notation rule. These legs exist so that correction cannot
    # be quietly reverted into a cleaner-sounding but untrue story.
    leg("the document does NOT claim nothing was disclosed",
        "No value from the file is disclosed" not in flat)
    for phrase in (
            "also told the other reader our answer",
            "It does not make their reading blind",
            "disclosed to\nB in advance".replace("\n", " "),
            "A supplied the population",
            "is therefore a tautology",
            "both readers graded against the same two public catalogs".lower(),
            "The seal buys one thing only",
            "are **our word**",
            "that is not in this repo",
            "is a **lower bound**",
            "recorded by one reader only",
            "not** independent evidence".replace("**", "**"),
            "as both readers normalised them**"):
        leg("document carries the honesty clause: %r" % phrase[:48], phrase in flat.lower()
            or phrase in flat)
    leg("every item A disclosed to B is listed in the document",
        all(k in flat for k in ("22 AGREE, 2 DIVERGENT, 0 unreadable",
                                "the 24-route list itself, verbatim")))
    leg("the comparison file's not_blind list is at least as long as 6",
        len(c["reader_a"]["not_blind_to_b"]) >= 6)

    # --- THE LEG THAT CLOSES THE LOOP ----------------------------------------------
    import subprocess
    import tempfile
    peer = os.path.join(HERE, "peer_result_cookieos.json")
    if "--no-peer" in sys.argv:
        print("  SKIP  published comparison re-derived from the two source readings (--no-peer)")
    elif not os.path.exists(peer):
        print("  SKIP  published comparison re-derived from the two source readings "
              "-- reader B's file is not here; rebuild it per SEALED_24.md to run this leg")
    else:
        with tempfile.TemporaryDirectory() as td:
            import shutil
            shutil.copy(A, os.path.join(td, os.path.basename(A)))
            r = subprocess.run([sys.executable, SCRIPT, os.path.join(td, os.path.basename(A)), peer],
                               capture_output=True, text=True)
            fresh = os.path.join(td, "two_reader_compare.json")
            leg("re-running the comparison on the two source readings succeeds",
                r.returncode == 0 and os.path.exists(fresh), r.stdout[-300:] + r.stderr[-300:])
            if os.path.exists(fresh):
                leg("the published two_reader_compare.json IS what the two readings produce",
                    json.load(open(fresh)) == json.load(open(CMP)))

    # --- PUBLICATION HYGIENE over every file this document ships with -----------------
    # Shift 603 stripped a swarm mission id, a work-room id and a peer's venue agent id out of
    # two published tools, but the leg enforcing that scanned only those two files. This one
    # scans every file in this bundle, so a new tool cannot reintroduce the same leak.
    import glob
    import re as _re
    BUNDLE = [DOC, A, CMP, SCRIPT, os.path.join(HERE, "extract_peer_artifact.py"),
              os.path.abspath(__file__)]
    BANNED = [(_re.compile(r"room_[0-9a-f]{8}"), "a private work-room id"),
              (_re.compile(r"\bwork/s\d+"), "an unpublishable work/ path"),
              (_re.compile(r"\bagent_[0-9a-f]{12}"), "a peer's venue agent id"),
              (_re.compile(r"mission_[0-9a-f]{6}"), "a swarm mission id")]
    # ONE scoped exemption, and it is forced rather than chosen: the sealed file is byte-frozen
    # by a public commitment, so the internal producer paths inside its own `generated_by` and
    # `live_surface` fields cannot be edited out. The exemption is one file for one rule; the
    # leg below asserts it stays that narrow, so it cannot grow into a general mute.
    EXEMPT = {(os.path.basename(A), "an unpublishable work/ path")}
    leg("the hygiene exemption is exactly one file for exactly one rule", len(EXEMPT) == 1)
    leg("the exempted file is the byte-frozen sealed reading",
        EXEMPT == {(os.path.basename(A), "an unpublishable work/ path")})
    leg("the document explains why the sealed file keeps its internal producer paths",
        "internal producer paths" in flat)
    for f in BUNDLE:
        body = open(f, encoding="utf-8", errors="replace").read()
        for rx, what in BANNED:
            if (os.path.basename(f), what) in EXEMPT:
                print("  EXEMPT  %s / %s -- byte-frozen, see SEALED_24.md"
                      % (os.path.basename(f), what))
                continue
            m = rx.search(body)
            leg("%s carries no %s" % (os.path.basename(f), what), m is None,
                ("found %r" % m.group(0)) if m else "")

    # --- the selftest-count claim ---------------------------------------------------
    r = subprocess.run([sys.executable, SCRIPT, "--selftest"], capture_output=True, text=True)
    n_ok = sum(1 for ln in r.stdout.splitlines() if ln.startswith("  ok "))
    leg("compare_two_readers.py --selftest is green", r.returncode == 0)
    leg("it has exactly the 22 legs the document claims", n_ok == 22 and "22 pure-function legs" in doc,
        "%d legs" % n_ok)

    print("\n%d leg(s) FAILED" % len(FAILS) if FAILS else "\nALL LEGS GREEN")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
