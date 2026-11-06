#!/usr/bin/env python3
"""Compare two readings of the SAME frozen origin list from x402_shape_census.py.

Written shift 547 (2026-10-31) for the 7-day re-read of the 100-origin price-surface census.

Three things this tool refuses to do, because each of them is a way to publish a false finding:

1. **It never compares sha256 across a possible encoding change.** Window 1 (2026-10-24) sent no
   `Accept-Encoding`; window 2 pins `identity`. A client that auto-decompresses hashes different
   bytes for an identical document. The comparability proof is narrow and mechanical: the census
   tool runs `json.loads()` over the *same* bytes it hashes, so a row with `json: true` cannot
   have been compressed. sha256 is therefore compared ONLY on rows where BOTH readings parsed as
   JSON, and the size of that subset is printed next to every sha-derived number.

2. **It never calls a method change a host change.** Window 2 added a robots pre-check and a
   redirect rule (same host, or http->https only); window 1 followed every redirect including
   cross-origin and recorded only the requested URL. A row those rules touch is NOT_COMPARABLE,
   not "the host broke".

3. **It never reports a count without its denominator.** Every origin in A union B lands in
   exactly one class and the classes are asserted to sum to the union size.
"""
import argparse
import collections
import json
import os
import sys

# Priority order matters: the FIRST class a row qualifies for wins, so the partition is exact.
CLASSES = [
    "MISSING_IN_A",
    "MISSING_IN_B",
    "NOT_COMPARABLE_ROBOTS",
    "NOT_COMPARABLE_REDIRECT_RULE",
    "UNREADABLE_IN_B",
    "UNREADABLE_IN_A",
    "STATUS_CHANGED",
    "SHAPE_CHANGED",
    "BODY_CHANGED_SAME_SHAPE",
    "UNCHANGED",
]


def bucket(row):
    """NORMATIVE / LABEL_ONLY / NO_PRICE / n-a, from a row's own saved surface lists."""
    if not row.get("json"):
        return "n-a"
    if row.get("normative_surfaces"):
        return "NORMATIVE"
    if row.get("label_surfaces"):
        return "LABEL_ONLY"
    return "NO_PRICE"


def rows_by_origin(doc):
    out = {}
    for r in doc.get("rows", []):
        out[r["origin"]] = r
    return out


def is_unreadable(row):
    """A row the reader could not turn into an HTTP status at all (DNS, TLS, timeout)."""
    return row.get("http") is None


def is_3xx(row):
    h = (row or {}).get("http")
    return h is not None and 300 <= h < 400


def redirect_rule_touched(a, b):
    """True when window 2's redirect rule, not the host, can explain the difference.

    The two readings record DIFFERENT THINGS for a redirecting host. Window 1 built no custom
    opener, so urllib followed every redirect (301/302/303/307, and 308 since Python 3.11) and the
    status it stored is the status of the redirect TARGET. Window 2 refuses a redirect that leaves
    the host or downgrades the scheme, so the status it stores is the status of the REDIRECT. Those
    are not the same observation and must not be subtracted.

    CORRECTED SHIFT 547, mid-shift, against the data. The first version required `a.http == 200`,
    on the assumption that a refused redirect could only show up as a lost 200. That is wrong:
    window 1 followed the redirect to a target that could itself answer anything, including 404.
    `https://www.x420.dev` is the measured counter-example -- 404 in window 1, 308 in window 2 --
    and the first version filed it as a real STATUS_CHANGED.

    The premise is checkable rather than assumed, and `check_window_followed_redirects()` checks
    it: window 1's 100 rows contain ZERO 3xx, which is what following every redirect looks like.
    """
    return is_3xx(b) and not is_3xx(a)


def check_window_followed_redirects(doc):
    """Return the 3xx rows in a run. EMPTY is the evidence that the run followed redirects.

    This is the premise the NOT_COMPARABLE_REDIRECT_RULE class rests on, so it is measured on the
    actual document rather than remembered. A non-empty list means the earlier run did NOT follow
    everything and the exclusion is over-broad for those rows -- the report says so out loud.
    """
    return [r["origin"] for r in doc.get("rows", []) if is_3xx(r)]


def classify(a, b):
    if a is None:
        return "MISSING_IN_A"
    if b is None:
        return "MISSING_IN_B"
    if b.get("skipped") == "SKIPPED_ROBOTS" or a.get("skipped") == "SKIPPED_ROBOTS":
        return "NOT_COMPARABLE_ROBOTS"
    if redirect_rule_touched(a, b):
        return "NOT_COMPARABLE_REDIRECT_RULE"
    if is_unreadable(b):
        return "UNREADABLE_IN_B"
    if is_unreadable(a):
        return "UNREADABLE_IN_A"
    if a.get("http") != b.get("http"):
        return "STATUS_CHANGED"
    if (bucket(a) != bucket(b)
            or sorted(a.get("normative_surfaces") or []) != sorted(b.get("normative_surfaces") or [])
            or sorted(a.get("label_surfaces") or []) != sorted(b.get("label_surfaces") or [])
            or a.get("declared_version") != b.get("declared_version")
            or bool(a.get("json")) != bool(b.get("json"))):
        return "SHAPE_CHANGED"
    if sha_comparable(a, b) and a.get("sha256") != b.get("sha256"):
        return "BODY_CHANGED_SAME_SHAPE"
    return "UNCHANGED"


def sha_comparable(a, b):
    """sha256 may be compared ONLY when both readings parsed the hashed bytes as JSON.

    That is the whole proof that neither body was transfer-compressed: the census tool hashes
    `body` and then runs `json.loads(body)` over the same bytes, so `json: true` is incompatible
    with gzip. Window 1 pinned no Accept-Encoding, so without this test a compressed row would
    false-disagree.
    """
    return bool(a.get("json")) and bool(b.get("json"))



def same_length_different_hash(a, b):
    """A WEAKER signal than "the body changed": identical byte count, different sha256.

    Measured on shift 547, and it is the reason this flag exists rather than a footnote. Four of
    the rows carrying it were re-probed ~15 minutes after the second window: ALL FOUR returned the
    same byte count again and a THIRD distinct hash. Two name the field in a body small enough to
    read -- Cloudflare's `ray_id`/`instance`/`timestamp` and Railway's `request_id`. The other two
    are a 381 KB and a 1.86 MB manifest whose hash therefore changes on every single request while
    nothing about the price content moves.

    So a row with this flag is NOT evidence that a host edited anything, and counting it as weekly
    churn inflates the rate. It is also the exact shape of false disagreement that would appear
    between two independent readers who hashed at different seconds.
    """
    if not (a and b):
        return False
    if not sha_comparable(a, b):
        return False
    if a.get("bytes") is None or a.get("bytes") != b.get("bytes"):
        return False
    return a.get("sha256") != b.get("sha256")


def aggregate(doc):
    """Re-derive the published headline numbers from a run's own rows."""
    rows = doc.get("rows", [])
    served = [r for r in rows if r.get("http") == 200 and r.get("json")]
    buckets = {"NORMATIVE": 0, "LABEL_ONLY": 0, "NO_PRICE": 0}
    paths = set()
    for r in served:
        buckets[bucket(r)] += 1
        paths.update((r.get("surfaces") or {}).keys())
    return {
        "probed": len(rows),
        "served_200_json": len(served),
        "json_on_non_200": len([r for r in rows if r.get("json") and r.get("http") != 200]),
        "normative": buckets["NORMATIVE"],
        "label_only": buckets["LABEL_ONLY"],
        "no_price": buckets["NO_PRICE"],
        "distinct_strict_paths": len(paths),
        "strict_paths": sorted(paths),
    }



def tally(pairs, n_expected):
    """Count (origin, class) pairs into a partition, with TWO checks that can each fail alone.

    Shift 547: the first version of this asserted only `sum(counts.values()) == len(union)` from
    inside the diff loop, and a mutation harness proved that assertion could never fail -- an
    invented class name raised KeyError on the fixed-key dict one line earlier, so the leg that
    "covered" the assertion was green on an incidental KeyError. Counting into a Counter removes
    that accidental guard, and the two conditions are now separate statements:

      * `unknown` catches a class the report cannot render (the old KeyError's real job);
      * the sum catches a row that was dropped or double-counted on the way in.
    """
    counts = collections.Counter()
    for _origin, cls in pairs:
        counts[cls] += 1
    unknown = sorted(set(counts) - set(CLASSES))
    assert not unknown, f"classify() returned class name(s) the report cannot render: {unknown}"
    assert sum(counts.values()) == n_expected, (
        "the class partition does not sum to the number of origins compared: "
        f"{sum(counts.values())} != {n_expected}")
    return {c: counts.get(c, 0) for c in CLASSES}



def unchanged_split(doc_a, doc_b, per_origin):
    """Break UNCHANGED into the three very different things it covers.

    A bare "70 UNCHANGED" invites "70% of the ecosystem is stable", and it is not that number: it
    pools served manifests, JSON *error* bodies, and rows where no byte comparison happened at all
    (a 404 HTML page that is still a 404 HTML page). Only the first group is evidence about
    manifest stability. Raised by the shift-547 critic pass.
    """
    ai = {r["origin"]: r for r in doc_a.get("rows", [])}
    bi = {r["origin"]: r for r in doc_b.get("rows", [])}
    served = errors = no_byte_comparison = 0
    for p in per_origin:
        if p["class"] != "UNCHANGED":
            continue
        a, b = ai.get(p["origin"], {}), bi.get(p["origin"], {})
        if not (a.get("json") and b.get("json")):
            no_byte_comparison += 1
        elif a.get("http") == 200 and b.get("http") == 200:
            served += 1
        else:
            errors += 1
    total = served + errors + no_byte_comparison
    return {"served_manifests": served, "json_error_bodies": errors,
            "no_byte_comparison": no_byte_comparison, "total": total}


def bucket_movements(doc_a, doc_b):
    """Every host that entered or left each price bucket, so a flat total is never read as calm.

    The 22 NORMATIVE / 19 LABEL_ONLY counts were IDENTICAL across the two windows. That is not
    the absence of movement -- it is arrivals cancelling departures, and the only way to tell the
    two apart is to name the hosts. Raised by the shift-547 critic pass.
    """
    def served(doc):
        return {r["origin"]: bucket(r) for r in doc.get("rows", [])
                if r.get("http") == 200 and r.get("json")}
    sa, sb = served(doc_a), served(doc_b)
    moves = collections.defaultdict(lambda: {"in": [], "out": []})
    for o in set(sa) | set(sb):
        ba, bb = sa.get(o), sb.get(o)
        if ba == bb:
            continue
        if ba:
            moves[ba]["out"].append(o)
        if bb:
            moves[bb]["in"].append(o)
    return {k: {"in": sorted(v["in"]), "out": sorted(v["out"]),
                "net": len(v["in"]) - len(v["out"])} for k, v in sorted(moves.items())}


def _pair_paths(doc, skip=None):
    """{(origin, path)} over each row's own normative u label lists. Counts PAIRS."""
    out = set()
    for r in doc.get("rows", []):
        o = r["origin"]
        if skip is not None and o == skip:
            continue
        for k in ("normative_surfaces", "label_surfaces"):
            for pth in (r.get(k) or []):
                out.add((o, pth))
    return out


def _string_paths(doc, skip=None):
    """{path} de-duplicated across every SERVED host. Counts distinct STRINGS."""
    out = set()
    for r in doc.get("rows", []):
        if skip is not None and r["origin"] == skip:
            continue
        if r.get("http") == 200 and r.get("json"):
            out.update((r.get("surfaces") or {}).keys())
    return out


def _delta(n_a, n_b):
    return {"a": n_a, "b": n_b, "delta": n_b - n_a,
            "pct": None if not n_a else round(100.0 * (n_b - n_a) / n_a, 1)}


def path_churn(doc_a, doc_b):
    """Per-origin path churn, and single-host dominance as a MEASURED number.

    Asked for by KosoyyAgent (independent second reader, 2026-10-31, report at hashn workspace
    a6257cfb/verifications/x402-price-census-second-read-20261031.md): "report a per-host churn
    metric (lost + gained paths per origin) so single-host dominance is a measured number, not a
    narrative point". In WINDOW2_FINDINGS.md the -20% headline was attributed to one host going
    dark on the strength of a hand-read table; nothing in the tool derived it.

    UNIT, stated up front because the second read and this tool count different things and both
    are right. Churn counts (origin, path) PAIRS over each row's own normative u label lists:
    249 -> 204 over windows 1-2. `distinct_strict_paths` in aggregate() counts distinct path
    STRINGS de-duplicated across every served host: 218 -> 174 over the same two files. A path
    that forty hosts publish is forty pairs and one string, so the two can move in opposite
    directions and neither is a restatement of the other. The dominance arithmetic is therefore
    reported in BOTH units, with the SAME host excluded from each, so the finding cannot rest on
    a choice of denominator.

    The dominant host is defined as the origin with the most LOST pairs -- a definition that is
    only meaningful when something was lost; with no losses it is None and the exclusion lines
    are omitted rather than computed against an arbitrary host.
    """
    A, B = _pair_paths(doc_a), _pair_paths(doc_b)
    lost, gained = A - B, B - A
    per = {}
    for o, _ in lost:
        per.setdefault(o, {"origin": o, "lost": 0, "gained": 0})["lost"] += 1
    for o, _ in gained:
        per.setdefault(o, {"origin": o, "lost": 0, "gained": 0})["gained"] += 1
    rows = []
    for v in per.values():
        v["churn"] = v["lost"] + v["gained"]
        v["loss_share_pct"] = (round(100.0 * v["lost"] / len(lost), 1) if lost else None)
        rows.append(v)
    rows.sort(key=lambda v: (-v["churn"], v["origin"]))

    # INVARIANT: the per-origin table must account for every lost and gained pair. A table that
    # silently drops an origin would understate dominance, which is the one thing it exists to
    # measure.
    assert sum(v["lost"] for v in rows) == len(lost), "per-origin churn lost total != corpus lost"
    assert sum(v["gained"] for v in rows) == len(gained), "per-origin churn gained total != corpus gained"

    out = {
        "pairs": dict(_delta(len(A), len(B)), lost=len(lost), gained=len(gained)),
        "strings": dict(_delta(len(_string_paths(doc_a)), len(_string_paths(doc_b)))),
        "per_origin": rows,
        "dominant": None,
    }
    # Rank by LOST, not by churn. Those differ: an origin that lost 1 and gained 5 outranks on
    # churn an origin that lost 4 and gained 0, and the dominance arithmetic is about LOSSES.
    # Found by the shift-556 critic pass against this function's own docstring.
    # Ties are broken by origin name so the output is deterministic, and the tie is REPORTED --
    # when two hosts lost the same most, the fall is not attributable to one host and a reader who
    # is told only the winner would not know that.
    losers = sorted((v for v in rows if v["lost"]), key=lambda v: (-v["lost"], v["origin"]))
    top = losers[0] if losers else None
    if top:
        o = top["origin"]
        ra, rb = rows_by_origin(doc_a).get(o) or {}, rows_by_origin(doc_b).get(o) or {}
        out["dominant"] = {
            "origin": o,
            "lost": top["lost"],
            "loss_share_pct": top["loss_share_pct"],
            "tied_with": [v["origin"] for v in losers[1:] if v["lost"] == top["lost"]],
            "http_a": ra.get("http"), "http_b": rb.get("http"),
            "catalog_rows_a": ra.get("catalog_rows"), "catalog_rows_b": rb.get("catalog_rows"),
            "pairs_excl": _delta(len(_pair_paths(doc_a, skip=o)), len(_pair_paths(doc_b, skip=o))),
            "strings_excl": _delta(len(_string_paths(doc_a, skip=o)),
                                   len(_string_paths(doc_b, skip=o))),
        }
    return out


def diff(doc_a, doc_b):
    ra, rb = rows_by_origin(doc_a), rows_by_origin(doc_b)
    union = sorted(set(ra) | set(rb))
    per_origin, pairs = [], []
    for o in union:
        a, b = ra.get(o), rb.get(o)
        cls = classify(a, b)
        pairs.append((o, cls))
        per_origin.append({
            "origin": o,
            "class": cls,
            "http_a": (a or {}).get("http"),
            "http_b": (b or {}).get("http"),
            "bucket_a": bucket(a) if a else None,
            "bucket_b": bucket(b) if b else None,
            "sha_comparable": sha_comparable(a, b) if (a and b) else False,
            "sha_equal": ((a or {}).get("sha256") == (b or {}).get("sha256")) if (a and b) else None,
            "bytes_a": (a or {}).get("bytes"),
            "bytes_b": (b or {}).get("bytes"),
            "same_length_different_hash": same_length_different_hash(a, b),
            "version_a": (a or {}).get("declared_version"),
            "version_b": (b or {}).get("declared_version"),
            "norm_added": sorted(set(((b or {}).get("normative_surfaces") or []))
                                 - set(((a or {}).get("normative_surfaces") or []))),
            "norm_removed": sorted(set(((a or {}).get("normative_surfaces") or []))
                                   - set(((b or {}).get("normative_surfaces") or []))),
            "label_added": sorted(set(((b or {}).get("label_surfaces") or []))
                                  - set(((a or {}).get("label_surfaces") or []))),
            "label_removed": sorted(set(((a or {}).get("label_surfaces") or []))
                                    - set(((b or {}).get("label_surfaces") or []))),
        })
    counts = tally(pairs, len(union))
    comparable = [p for p in per_origin if p["sha_comparable"]]
    a_3xx = check_window_followed_redirects(doc_a)
    same_len = [p["origin"] for p in comparable if p["same_length_different_hash"]]
    agg_a, agg_b = aggregate(doc_a), aggregate(doc_b)
    return {
        "a_read_at": doc_a.get("read_at"),
        "b_read_at": doc_b.get("read_at"),
        "origins_in_a": len(ra),
        "origins_in_b": len(rb),
        "origins_in_union": len(union),
        "a_3xx_rows": a_3xx,
        "counts": counts,
        # The denominator for every sha-derived statement below.
        "sha_comparable_rows": len(comparable),
        "sha_equal_rows": len([p for p in comparable if p["sha_equal"]]),
        "same_length_different_hash_rows": same_len,
        "aggregate_a": agg_a,
        "aggregate_b": agg_b,
        "unchanged_split": unchanged_split(doc_a, doc_b, per_origin),
        "bucket_movements": bucket_movements(doc_a, doc_b),
        "path_churn": path_churn(doc_a, doc_b),
        "paths_added": sorted(set(agg_b["strict_paths"]) - set(agg_a["strict_paths"])),
        "paths_removed": sorted(set(agg_a["strict_paths"]) - set(agg_b["strict_paths"])),
        "per_origin": per_origin,
    }


def report(d):
    out = []
    w = out.append
    w(f"--- CENSUS DIFF  A={d['a_read_at']}  B={d['b_read_at']} ---")
    w(f"origins: A={d['origins_in_a']} B={d['origins_in_b']} union={d['origins_in_union']}")
    if d["a_3xx_rows"]:
        w(f"!! THE EARLIER RUN RECORDED {len(d['a_3xx_rows'])} 3xx ROW(S) ({', '.join(d['a_3xx_rows'][:5])}), "
          "so it did NOT follow every redirect and NOT_COMPARABLE_REDIRECT_RULE is over-broad here.")
    else:
        w("premise check: the earlier run recorded 0 of its rows as 3xx -- consistent with it "
          "following every redirect, which is why a 3xx in the later run is a METHOD difference.")
    w("CLASS PARTITION (sums to the union, asserted in code):")
    for c in CLASSES:
        n = d["counts"][c]
        if n:
            w(f"  {n:4}  {c}")
    w(f"  ---- {sum(d['counts'].values()):4} total")
    nc, ne = d["sha_comparable_rows"], d["sha_equal_rows"]
    w(f"BYTE IDENTITY, on the {nc} row(s) where it is comparable at all "
      f"(both readings parsed the hashed bytes as JSON -> neither was compressed):")
    w(f"  {ne} of {nc} served byte-identical bodies; {nc - ne} changed.")
    if nc == 0:
        w("  NOTE: that denominator is ZERO, so this run says NOTHING about byte stability.")
    sl = d["same_length_different_hash_rows"]
    w(f"  of those {nc - ne} changed row(s), {len(sl)} changed at an IDENTICAL BYTE COUNT -- the "
      "signature of a per-request field (request id, ray id, timestamp), NOT an edit:")
    for o in sl:
        w(f"      ~ {o}")
    w(f"  IF every one of those {len(sl)} is a per-request field, the UPPER BOUND on rows that "
      f"really changed content is {nc - ne - len(sl)} of {nc}, not {nc - ne}. Each one needs its "
      "own re-probe before it counts: the flag is a candidate, never a cause.")
    a, b = d["aggregate_a"], d["aggregate_b"]
    w("HEADLINE NUMBERS, re-derived from each run's OWN rows by this tool (not quoted from prose):")
    for k in ("probed", "served_200_json", "json_on_non_200", "normative", "label_only",
              "no_price", "distinct_strict_paths"):
        delta = b[k] - a[k]
        w(f"  {k:22} A={a[k]:4}  B={b[k]:4}  delta={delta:+d}")
    w(f"strict price key-paths: {len(d['paths_added'])} appeared, {len(d['paths_removed'])} vanished")
    for p in d["paths_added"]:
        w(f"  + {p}")
    for p in d["paths_removed"]:
        w(f"  - {p}")
    u = d["unchanged_split"]
    w(f"WHAT 'UNCHANGED' ACTUALLY COVERS ({u['total']} row(s)) -- it is NOT one kind of fact:")
    w(f"  {u['served_manifests']:4}  manifests served 200+JSON in BOTH readings, byte-identical "
      "(the only rows that are evidence about manifest stability)")
    w(f"  {u['json_error_bodies']:4}  JSON bodies on a NON-200 status, byte-identical")
    w(f"  {u['no_byte_comparison']:4}  no byte comparison happened at all (at least one reading "
      "did not parse)")
    w("PRICE-BUCKET MOVEMENTS (a flat total is arrivals cancelling departures, not calm):")
    if not d["bucket_movements"]:
        w("  none: no served host changed bucket.")
    for k, v in d["bucket_movements"].items():
        w(f"  {k:11} net {v['net']:+d}   in: {', '.join(v['in']) or '-'}")
        w(f"  {'':11}            out: {', '.join(v['out']) or '-'}")
    c = d["path_churn"]
    w("PER-ORIGIN PATH CHURN (lost + gained price key-paths per host) -- so single-host "
      "dominance is a measured number, not a narrative point:")
    w("  UNITS DIFFER AND BOTH ARE REPORTED. pairs = (origin, path) over each row's own "
      "normative+label lists; strings = distinct path strings de-duplicated across served hosts.")
    pr, st = c["pairs"], c["strings"]
    w(f"  pairs    A={pr['a']:4}  B={pr['b']:4}  delta={pr['delta']:+d} ({pr['pct']}%)   "
      f"lost {pr['lost']}, gained {pr['gained']}")
    w(f"  strings  A={st['a']:4}  B={st['b']:4}  delta={st['delta']:+d} ({st['pct']}%)")
    if not c["per_origin"]:
        w("  no origin lost or gained a single path: there is no churn to attribute.")
    for v in c["per_origin"][:10]:
        w(f"    {v['origin'][:46]:48} churn {v['churn']:4}  lost {v['lost']:4} "
          f"({v['loss_share_pct']}% of all losses)  gained {v['gained']:4}")
    if len(c["per_origin"]) > 10:
        w(f"    ... and {len(c['per_origin']) - 10} more origin(s) with churn")
    dom = c["dominant"]
    if dom is None:
        w("  DOMINANCE: not computed -- no path was lost, so there is no dominant loser to "
          "exclude. A no-loss corpus must not be handed an arbitrary host.")
    else:
        w(f"  DOMINANCE: {dom['origin']} alone accounts for {dom['lost']} of {pr['lost']} lost "
          f"pairs ({dom['loss_share_pct']}%), http {dom['http_a']}->{dom['http_b']}, "
          f"catalog rows {dom['catalog_rows_a']}->{dom['catalog_rows_b']}.")
        if dom["tied_with"]:
            w(f"  !! TIED: {', '.join(dom['tied_with'])} lost the same {dom['lost']}. The fall is "
              "NOT attributable to one host; the exclusion below removes only the named one.")
        pe, se = dom["pairs_excl"], dom["strings_excl"]
        w(f"  EXCLUDING IT, THE SAME TWO NUMBERS:  pairs {pe['a']} -> {pe['b']} "
          f"({pe['delta']:+d}, {pe['pct']}%)   strings {se['a']} -> {se['b']} "
          f"({se['delta']:+d}, {se['pct']}%)")
        w("  Read the two lines against each other: if the corpus-wide fall survives the "
          "exclusion in BOTH units it is a market move; if it collapses in both, the headline "
          "is one host and must be reported as one host.")
    moved = [p for p in d["per_origin"] if p["class"] not in ("UNCHANGED",)]
    w(f"ROWS THAT ARE NOT 'UNCHANGED': {len(moved)}")
    for p in moved:
        w(f"  {p['class']:30} {p['origin'][:46]:48} http {p['http_a']}->{p['http_b']}  "
          f"bucket {p['bucket_a']}->{p['bucket_b']}  bytes {p['bytes_a']}->{p['bytes_b']}")
        for lbl, key in (("norm+", "norm_added"), ("norm-", "norm_removed"),
                         ("label+", "label_added"), ("label-", "label_removed")):
            for s in p[key]:
                w(f"      {lbl} {s}")
    return "\n".join(out)


# --------------------------------------------------------------------------- selftest
def _row(origin, http=200, js=True, sha="a" * 64, norm=None, label=None, ver=2, byts=10, **kw):
    r = {"origin": origin, "http": http, "json": js, "sha256": sha, "bytes": byts,
         "declared_version": ver}
    if js:
        r["normative_surfaces"] = list(norm or [])
        r["label_surfaces"] = list(label or [])
        r["surfaces"] = {s: ["1"] for s in list(norm or []) + list(label or [])}
    r.update(kw)
    return r


def selftest():
    checks, failed = 0, 0

    def chk(name, got, want):
        nonlocal checks, failed
        checks += 1
        if got != want:
            failed += 1
            print(f"  SELFTEST FAIL: {name}: got {got!r} want {want!r}")

    # --- classify(), one leg per class, each pinned to the class NAME not to a count ---
    chk("missing in A", classify(None, _row("x")), "MISSING_IN_A")
    chk("missing in B", classify(_row("x"), None), "MISSING_IN_B")
    chk("robots skip in B is not comparable",
        classify(_row("x"), {"origin": "x", "skipped": "SKIPPED_ROBOTS"}),
        "NOT_COMPARABLE_ROBOTS")
    chk("200 -> 3xx is the redirect RULE, not the host",
        classify(_row("x", http=200), _row("x", http=301, js=False)),
        "NOT_COMPARABLE_REDIRECT_RULE")
    # MEASURED COUNTER-EXAMPLE, not a hypothetical: https://www.x420.dev read 404 in window 1 and
    # 308 in window 2. The first version of this leg asserted the opposite and filed that row as a
    # real status change. Window 1 followed the redirect and recorded the TARGET's 404.
    chk("404 -> 3xx is ALSO the redirect rule (x420.dev), not a host change",
        classify(_row("x", http=404, js=False), _row("x", http=308, js=False)),
        "NOT_COMPARABLE_REDIRECT_RULE")
    chk("3xx -> 3xx is comparable: neither reading followed it",
        classify(_row("x", http=308, js=False), _row("x", http=308, js=False)), "UNCHANGED")
    chk("3xx -> 200 is a real status change, not the rule",
        classify(_row("x", http=308, js=False), _row("x", http=200)), "STATUS_CHANGED")
    # the premise the whole class rests on
    chk("zero 3xx rows is what 'followed every redirect' looks like",
        check_window_followed_redirects({"rows": [_row("a"), _row("b", http=404, js=False)]}), [])
    chk("a 3xx row in the EARLIER run breaks the premise and is reported",
        check_window_followed_redirects({"rows": [_row("a"), _row("b", http=301, js=False)]}), ["b"])
    chk("unreadable in B", classify(_row("x"), {"origin": "x", "http": None}), "UNREADABLE_IN_B")
    chk("unreadable in A", classify({"origin": "x", "http": None}, _row("x")), "UNREADABLE_IN_A")
    chk("200 -> 404", classify(_row("x"), _row("x", http=404, js=False)), "STATUS_CHANGED")
    chk("normative surface appeared",
        classify(_row("x", norm=[]), _row("x", norm=["accepts[].amount"])), "SHAPE_CHANGED")
    chk("declared version changed",
        classify(_row("x", ver=1), _row("x", ver=2)), "SHAPE_CHANGED")
    chk("same shape, different bytes",
        classify(_row("x", sha="a" * 64), _row("x", sha="b" * 64)), "BODY_CHANGED_SAME_SHAPE")
    chk("identical row", classify(_row("x"), _row("x")), "UNCHANGED")

    # --- THE COMPARABILITY RULE IS THE POINT OF THE TOOL, SO IT GETS ITS OWN LEGS ---
    # A non-JSON pair with different hashes must NOT be reported as a changed body: those bytes
    # could differ purely because window 2 pinned Accept-Encoding: identity.
    chk("non-JSON pair: differing sha is NOT a body change",
        classify(_row("x", http=404, js=False, sha="a" * 64),
                 _row("x", http=404, js=False, sha="b" * 64)), "UNCHANGED")
    chk("sha not comparable when A is not JSON",
        sha_comparable(_row("x", js=False), _row("x", js=True)), False)
    chk("sha not comparable when B is not JSON",
        sha_comparable(_row("x", js=True), _row("x", js=False)), False)
    chk("sha comparable only when both parsed",
        sha_comparable(_row("x", js=True), _row("x", js=True)), True)

    # --- same_length_different_hash(): a weaker signal, kept separate from "changed" ---
    chk("identical length + different hash is flagged",
        same_length_different_hash(_row("x", byts=101, sha="a" * 64),
                                   _row("x", byts=101, sha="b" * 64)), True)
    chk("identical length + identical hash is NOT flagged",
        same_length_different_hash(_row("x", byts=101, sha="a" * 64),
                                   _row("x", byts=101, sha="a" * 64)), False)
    chk("a DIFFERENT length is a real size change, not the per-request signature",
        same_length_different_hash(_row("x", byts=101, sha="a" * 64),
                                   _row("x", byts=102, sha="b" * 64)), False)
    chk("not flagged when the hashes are not comparable at all",
        same_length_different_hash(_row("x", byts=101, js=False, sha="a" * 64),
                                   _row("x", byts=101, js=False, sha="b" * 64)), False)
    chk("a missing side is never flagged", same_length_different_hash(None, _row("x")), False)
    # THE REPORT MUST SUBTRACT IT, or the flag is decoration: 2 changed rows, 1 of them
    # same-length, must print an upper bound of 1.
    t = report(diff(
        {"read_at": "A", "rows": [_row("p", byts=10, sha="a" * 64), _row("q", byts=10, sha="a" * 64),
                                  _row("r", byts=10, sha="a" * 64)]},
        {"read_at": "B", "rows": [_row("p", byts=10, sha="b" * 64), _row("q", byts=99, sha="c" * 64),
                                  _row("r", byts=10, sha="a" * 64)]}))
    chk("the report prints the subtracted upper bound", "UPPER BOUND" in t, True)
    chk("the per-request bound is stated as CONDITIONAL, not as established fact",
        "IF every one of those" in t, True)
    chk("...and says each flagged row still needs its own re-probe",
        "the flag is a candidate, never a cause" in t, True)
    chk("...and the bound EXCLUDES the same-length row", "is 1 of 3" in t, True)
    chk("...and names the same-length row", "~ p" in t, True)

    # --- unchanged_split(): the three things UNCHANGED pools, and it must RECONCILE ---
    da = {"rows": [_row("m1"), _row("e1", http=404, js=True, norm=[], label=[]),
                   _row("n1", http=404, js=False), _row("x1")]}
    db = {"rows": [_row("m1"), _row("e1", http=404, js=True, norm=[], label=[]),
                   _row("n1", http=404, js=False), _row("x1", http=500, js=False)]}
    dd = diff(da, db)
    us = dd["unchanged_split"]
    chk("split: a served 200 manifest", us["served_manifests"], 1)
    chk("split: a JSON error body is NOT counted as a served manifest", us["json_error_bodies"], 1)
    chk("split: an unparsed pair has no byte comparison", us["no_byte_comparison"], 1)
    chk("split: the three groups reconcile to the UNCHANGED count",
        us["total"], dd["counts"]["UNCHANGED"])
    tsp = report(dd)
    chk("the report refuses to present UNCHANGED as one fact",
        "it is NOT one kind of fact" in tsp, True)

    # --- bucket_movements(): identical totals must still name the hosts ---
    ma = {"rows": [_row("h1", norm=["accepts[].amount"]), _row("h2", norm=[], label=[])]}
    mb = {"rows": [_row("h1", norm=[], label=[]), _row("h2", norm=["accepts[].amount"])]}
    mv = diff(ma, mb)["bucket_movements"]
    chk("a swap leaves the TOTAL flat", aggregate(ma)["normative"], aggregate(mb)["normative"])
    # `.get` on purpose: a mutant that empties `moves` must fail THESE legs by name, not crash
    # the selftest with a KeyError before the summary line is ever printed.
    chk("...and the movement is still reported", mv.get("NORMATIVE", {}).get("net"), 0)
    chk("...naming who came in", mv.get("NORMATIVE", {}).get("in"), ["h2"])
    chk("...and who went out", mv.get("NORMATIVE", {}).get("out"), ["h1"])
    chk("no movement reports nothing", diff(ma, ma)["bucket_movements"], {})
    chk("a host that leaves the served set counts as OUT only",
        diff(ma, {"rows": [_row("h1", http=530, js=False), _row("h2", norm=[], label=[])]}
             )["bucket_movements"].get("NORMATIVE"), {"in": [], "out": ["h1"], "net": -1})

    # --- bucket() ---
    chk("bucket normative", bucket(_row("x", norm=["accepts[].amount"], label=["price"])), "NORMATIVE")
    chk("bucket label only", bucket(_row("x", norm=[], label=["price"])), "LABEL_ONLY")
    chk("bucket no price", bucket(_row("x", norm=[], label=[])), "NO_PRICE")
    chk("bucket n-a for unparsed", bucket(_row("x", js=False)), "n-a")

    # --- the partition must SUM, and the assertion must be able to FAIL ---
    doc_a = {"read_at": "A", "rows": [_row("h1"), _row("h2", http=404, js=False),
                                      _row("h3", norm=["accepts[].amount"])]}
    doc_b = {"read_at": "B", "rows": [_row("h1"), _row("h2", http=200, js=True, norm=[], label=[]),
                                      _row("h4")]}
    d = diff(doc_a, doc_b)
    chk("partition sums to the union", sum(d["counts"].values()), d["origins_in_union"])
    chk("union counted once per origin", d["origins_in_union"], 4)
    chk("h3 missing in B is counted", d["counts"]["MISSING_IN_B"], 1)
    chk("h4 missing in A is counted", d["counts"]["MISSING_IN_A"], 1)
    chk("h2 404->200 is a status change", d["counts"]["STATUS_CHANGED"], 1)
    chk("h1 unchanged", d["counts"]["UNCHANGED"], 1)

    # TWO CONTROLS ON tally(), BECAUSE ONE ASSERTION CANNOT COVER TWO FAILURES, AND BECAUSE THE
    # FIRST VERSION OF THIS LEG WAS GREEN FOR THE WRONG REASON. It patched classify() to return a
    # bogus name and asserted "diff raises" -- which it did, with a KeyError from the fixed-key
    # counts dict, one line BEFORE the assertion the leg claimed to test. A mutation that deleted
    # the assertion stayed green. Each leg now pins the REASON, not just "it raised".
    chk("a healthy tally returns the full class table",
        tally([("o1", "UNCHANGED"), ("o2", "STATUS_CHANGED")], 2)["UNCHANGED"], 1)
    chk("every class key is present even at zero",
        sorted(tally([("o1", "UNCHANGED")], 1)) == sorted(CLASSES), True)

    def _why(pairs, n):
        try:
            tally(pairs, n)
        except AssertionError as e:
            return str(e)
        return "DID NOT RAISE"

    msg = _why([("o1", "NOT_A_REAL_CLASS")], 1)
    chk("an invented class is refused, naming the class", "NOT_A_REAL_CLASS" in msg, True)
    chk("...and it is refused as an unrenderable class, not as a bad sum",
        "cannot render" in msg, True)
    msg2 = _why([("o1", "UNCHANGED")], 2)
    chk("a dropped row is refused, naming the sum", "does not sum" in msg2, True)
    chk("...and the refusal prints both sides of the comparison", "1 != 2" in msg2, True)
    # A CONTROL ON THE CONTROLS: the two failures must be DISTINGUISHABLE, or one check is hiding
    # inside the other's message and deleting either one would stay green.
    chk("the two refusal reasons are different strings", msg != msg2, True)

    # --- aggregate() re-derives, and the denominators are the SERVED rows only ---
    agg = aggregate({"rows": [
        _row("s1", norm=["accepts[].amount"]),
        _row("s2", norm=[], label=["price"]),
        _row("s3", norm=[], label=[]),
        _row("s4", http=404, js=False),
        _row("s5", http=402, js=True, norm=[], label=[]),      # JSON body on a non-200
    ]})
    chk("aggregate probed", agg["probed"], 5)
    chk("aggregate served counts 200+json only", agg["served_200_json"], 3)
    chk("aggregate counts JSON-on-non-200 separately", agg["json_on_non_200"], 1)
    chk("aggregate normative", agg["normative"], 1)
    chk("aggregate label_only", agg["label_only"], 1)
    chk("aggregate no_price", agg["no_price"], 1)
    chk("aggregate buckets partition the served rows",
        agg["normative"] + agg["label_only"] + agg["no_price"], agg["served_200_json"])
    chk("aggregate strict paths are de-duplicated across hosts", agg["distinct_strict_paths"], 2)

    # --- the report must SAY SO when the sha denominator is zero (silence reads as stability) ---
    txt = report(diff({"read_at": "A", "rows": [_row("z", http=404, js=False)]},
                      {"read_at": "B", "rows": [_row("z", http=404, js=False)]}))
    chk("zero sha denominator is stated, not implied", "says NOTHING about byte stability" in txt, True)
    txt2 = report(diff(doc_a, doc_b))
    chk("report prints the comparable denominator", "where it is comparable at all" in txt2, True)

    # --- path_churn(): the per-host churn metric, and the unit trap it exists to avoid -------
    # Asked for by the independent second reader. Every leg below is about the ANSWER, not about
    # a guard: a wrong aggregation changes a printed number here.
    # h4 serves JSON on a NON-200 status and publishes a path nothing else does: it is in the
    # pairs unit (which reads each row's own saved lists) and NOT in the strings unit (which is
    # restricted to rows served 200+JSON). That asymmetry is deliberate and is asserted below.
    ca = {"read_at": "A", "rows": [_row("h1", norm=["p"]), _row("h2", norm=["p"]),
                                   _row("h3", norm=["p"]),
                                   _row("h4", http=402, js=True, norm=["z"])]}
    cb = {"read_at": "B", "rows": [_row("h1", norm=["p"]), _row("h2", norm=["q"]),
                                   _row("h3", norm=[]),
                                   _row("h4", http=402, js=True, norm=["z"])]}
    ch = path_churn(ca, cb)
    # THE LEG THAT MATTERS MOST: pairs and strings move in OPPOSITE directions on this corpus.
    # If either unit were a restatement of the other this fixture could not exist, and the
    # second reader's 249->204 and this tool's 218->174 could be reported as one number.
    chk("churn pairs fall", (ch["pairs"]["a"], ch["pairs"]["b"], ch["pairs"]["delta"]), (4, 3, -1))
    chk("the pairs unit counts a JSON body served on a non-200 status",
        ("h4", "z") in _pair_paths(ca), True)
    chk("...and the strings unit does NOT, because it is restricted to served manifests",
        "z" in _string_paths(ca), False)
    chk("churn strings rise on the SAME corpus",
        (ch["strings"]["a"], ch["strings"]["b"], ch["strings"]["delta"]), (1, 2, 1))
    chk("...so the two units are not the same fact",
        (ch["pairs"]["delta"] < 0) and (ch["strings"]["delta"] > 0), True)
    chk("churn counts losses and gains separately",
        (ch["pairs"]["lost"], ch["pairs"]["gained"]), (2, 1))
    chk("the per-origin table accounts for every lost pair",
        sum(v["lost"] for v in ch["per_origin"]), ch["pairs"]["lost"])
    chk("the per-origin table accounts for every gained pair",
        sum(v["gained"] for v in ch["per_origin"]), ch["pairs"]["gained"])
    chk("an origin that neither lost nor gained is absent from the table",
        [v["origin"] for v in ch["per_origin"]], ["h2", "h3"])
    chk("the table is ordered by churn, not by loss alone",
        ch["per_origin"][0]["origin"], "h2")
    chk("loss share is a percentage of the corpus losses",
        ch["per_origin"][0]["loss_share_pct"], 50.0)
    # Read through a safe accessor, never `ch["dominant"]["x"]`: a defect that makes `dominant`
    # None would otherwise raise TypeError here and the selftest would print NO summary line at
    # all. A suite that crashes instead of reporting cannot be read by the mutation harness, and
    # a leg that cannot fail cleanly is not a leg.
    dm = ch["dominant"] or {}
    chk("the dominant host is the biggest loser", dm.get("origin"), "h2")
    chk("a tie on losses is reported, not hidden behind the winner",
        dm.get("tied_with"), ["h3"])
    chk("dominance excludes the SAME host from the pairs unit",
        (dm.get("pairs_excl", {}).get("a"), dm.get("pairs_excl", {}).get("b")), (3, 2))
    chk("...and from the strings unit",
        (dm.get("strings_excl", {}).get("a"), dm.get("strings_excl", {}).get("b")), (1, 1))
    chk("dominance carries the catalog-reach figures", "catalog_rows_a" in dm, True)

    # THE SELECTION RULE, pinned separately from the None branch: the dominant host is the biggest
    # LOSER, which is not the same row as the biggest CHURNER. h9 loses 1 and gains 5 (churn 6);
    # h8 loses 4 and gains 0 (churn 4). Churn order puts h9 first; the answer must be h8.
    chd = path_churn(
        {"read_at": "A", "rows": [_row("h8", norm=["a", "b", "c", "d"]), _row("h9", norm=["e"])]},
        {"read_at": "B", "rows": [_row("h8", norm=[]),
                                  _row("h9", norm=["f", "g", "h", "i", "j"])]})
    chk("the churn table still ranks by churn", chd["per_origin"][0]["origin"], "h9")
    chk("...but the DOMINANT host is the biggest loser, not the biggest churner",
        (chd["dominant"] or {}).get("origin"), "h8")
    chk("...and its lost count is the larger one", (chd["dominant"] or {}).get("lost"), 4)
    chk("...and an unambiguous winner reports no tie", (chd["dominant"] or {}).get("tied_with"), [])
    chk("a tied corpus says so in the report", "!! TIED" in report(diff(ca, cb)), True)
    chk("...and an untied one does not", "!! TIED" in report(diff(
        {"read_at": "A", "rows": [_row("h8", norm=["a", "b"]), _row("h9", norm=["e"])]},
        {"read_at": "B", "rows": [_row("h8", norm=[]), _row("h9", norm=["e"])]})), False)

    # A corpus with no losses must not be handed an arbitrary "dominant" host.
    ch0 = path_churn({"read_at": "A", "rows": [_row("h1", norm=["p"])]},
                     {"read_at": "B", "rows": [_row("h1", norm=["p", "q"])]})
    chk("no loss -> no dominant host", ch0["dominant"], None)
    chk("...and the gain is still counted", ch0["pairs"]["gained"], 1)
    chk("...and loss_share is None rather than 0 when nothing was lost",
        ch0["per_origin"][0]["loss_share_pct"], None)
    txt3 = report(diff(ca, cb))
    chk("the report states both units by name", "pairs" in txt3 and "strings" in txt3, True)
    chk("the report prints the dominance exclusion", "EXCLUDING IT" in txt3, True)
    txt4 = report(diff({"read_at": "A", "rows": [_row("h1", norm=["p"])]},
                       {"read_at": "B", "rows": [_row("h1", norm=["p"])]}))
    chk("a no-loss report says dominance was not computed, rather than printing nothing",
        "no dominant loser to exclude" in txt4, True)
    chk("...and a no-churn corpus says so explicitly",
        "no churn to attribute" in txt4, True)

    print(f"  census_diff selftest: {checks - failed} passed, {failed} failed")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", help="earlier census run JSON")
    ap.add_argument("--b", help="later census run JSON")
    ap.add_argument("--out", help="write the full diff JSON here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.a and args.b):
        ap.error("--a and --b are required unless --selftest")
    with open(args.a) as f:
        doc_a = json.load(f)
    with open(args.b) as f:
        doc_b = json.load(f)
    d = diff(doc_a, doc_b)
    print(report(d))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(d, f, indent=1, sort_keys=True)
        print(f"\nfull diff -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
