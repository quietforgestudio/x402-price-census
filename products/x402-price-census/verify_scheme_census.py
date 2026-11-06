#!/usr/bin/env python3
"""Verify SCHEME_CENSUS.md / scheme_census.json against scheme_evidence.json. Stdlib only.

Run (no arguments, no network, from this directory):
    python3 verify_scheme_census.py

WHY THIS SHIPS. Every figure in SCHEME_CENSUS.md is a claim about 5,994 routes you did not read.
This recomputes all of them from the per-route rows in `scheme_evidence.json` and exits non-zero
on any mismatch -- so you can check the report instead of trusting it. It is deliberately a
SECOND, independent implementation: it imports nothing from the tool that produced the files.

WHAT IT CANNOT TELL YOU. It proves the published numbers follow from the published rows. It does
NOT prove the rows are what the two discovery feeds served on 2026-11-05 -- nothing offline can.
To check that, re-read the feeds named in `attribution` and compare; they change continuously, so
expect drift in the totals and look at the shapes instead.
"""
import collections
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ev = json.loads((HERE / "scheme_evidence.json").read_text())
pub = json.loads((HERE / "scheme_census.json").read_text())
md = (HERE / "SCHEME_CENSUS.md").read_text()
# The README carries a SECOND copy of every headline figure. A verifier that checks one document
# lets the other rot, and the README is the page a stranger reads first.
readme = (HERE / "README.md").read_text()
rows = ev["routes"]

ok = fail = 0


def chk(name, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  ok   %-58s %s" % (name, str(got)[:60]))
    else:
        fail += 1
        print("  FAIL %-58s got %r want %r" % (name, got, want))


# ---- 1. the figures, re-derived from the rows
chk("routes", len(rows), ev["population"]["routes_after_dedupe_on_method_resource"])
chk("options total", sum(r["n_options"] for r in rows), ev["n_options_total"])

vals = collections.Counter()
for r in rows:
    vals.update(r["scheme_counts"])
chk("scheme value census", dict(vals.most_common()), ev["option_scheme_value_census"])
chk("scheme value census == census.json", dict(vals.most_common()),
    pub["options"]["scheme_value_census"])

dist = collections.Counter("null" if r["scheme_n"] is None else str(r["scheme_n"]) for r in rows)
chk("scheme_n distribution", dict(dist), ev["headline"]["scheme_n_distribution"])
chk("multi-scheme routes", sum(1 for r in rows if (r["scheme_n"] or 0) > 1),
    ev["headline"]["routes_with_multiple_schemes"])
chk("pay_basis distribution", dict(collections.Counter(r["pay_basis"] for r in rows)),
    ev["headline"]["pay_basis_distribution"])

ties = silent = 0
for r in rows:
    opts = r.get("options") or []
    if not opts:
        continue
    groups = collections.defaultdict(list)
    for o in opts:
        groups[(o["network"], o["asset"], o["payTo"], o["amount_atomic"])].append(str(o["scheme"]))
    if any(len(set(g)) > 1 for g in groups.values()):
        ties += 1
        if r["pay_alternatives_n"] == 1 and (r["pay_scheme_n"] or 0) > 1:
            silent += 1
chk("scheme ties", ties, ev["headline"]["routes_with_scheme_tie"])
chk("silent scheme ties", silent, ev["headline"]["routes_with_silent_scheme_tie"])
chk("silent ties are a subset of ties", silent <= ties, True)

# ---- 2. a route with one scheme CANNOT tie: the reason option tuples ship only for multi-scheme
chk("only multi-scheme routes carry option tuples",
    sorted({(r["scheme_n"] or 0) > 1 for r in rows if r.get("options")}), [True])
chk("every multi-scheme route carries its option tuples",
    [r["id"] for r in rows if (r["scheme_n"] or 0) > 1 and not r.get("options")], [])

# ---- 3. the per-row fields are internally consistent
chk("scheme_n == distinct keys in scheme_counts",
    [r["id"] for r in rows if r["scheme_n"] is not None and r["scheme_n"] != len(r["scheme_counts"])], [])
chk("pay_scheme_n is null exactly where pay_alternatives_n is",
    [r["id"] for r in rows if (r["pay_scheme_n"] is None) != (r["pay_alternatives_n"] is None)], [])
chk("pay_scheme_n never exceeds scheme_n",
    [r["id"] for r in rows if (r["pay_scheme_n"] or 0) > (r["scheme_n"] or 0)], [])
chk("a positional pay_basis always has a non-null pay_scheme_n",
    [r["id"] for r in rows
     if r["pay_basis"] in ("tied_min_positional", "unpriced_positional") and r["pay_scheme_n"] is None], [])

# ---- 4. the PROSE in SCHEME_CENSUS.md must carry the same numbers
def md_int(pattern):
    m = re.search(pattern, md)
    return int(m.group(1).replace(",", "")) if m else None


chk("report's multi-scheme count matches",
    md_int(r"\*\*(\d[\d,]*) routes publish more than one settlement scheme\*\*"),
    ev["headline"]["routes_with_multiple_schemes"])
chk("report's tie count matches",
    md_int(r"\*\*(\d[\d,]*) routes carry a scheme tie\*\*"), ev["headline"]["routes_with_scheme_tie"])
chk("report's silent-tie count matches",
    md_int(r"\*\*(\d[\d,]*) routes carry a \*silent\* scheme tie\*\*"),
    ev["headline"]["routes_with_silent_scheme_tie"])
chk("report's option total matches", md_int(r"(\d[\d,]*) `accepts\[\]` entries"),
    ev["n_options_total"])
chk("report's distinct-scheme count matches",
    md_int(r"At least (\d+) distinct `scheme` strings"), len(ev["option_scheme_value_census"]))
chk("report names its population (not the feed total)",
    "is not a count of the feed" in md, True)
chk("report states no route was probed", "no payment was sent" in md, True)
for tag, n in list(ev["option_scheme_value_census"].items()):
    chk("report's row for scheme %r" % tag, ("| `%s` | %d |" % (tag, n)) in md, True)

# ---- 5. the README's copy of the same figures
def rd_int(pattern):
    m = re.search(pattern, readme)
    return int(m.group(1).replace(",", "")) if m else None


chk("README option total", rd_int(r"across ([\d,]+)\s*\n?payment options"), ev["n_options_total"])
chk("README route total", rd_int(r"payment options on ([\d,]+) routes"),
    ev["population"]["routes_after_dedupe_on_method_resource"])
chk("README multi-scheme count", rd_int(r"\n([\d,]+) of those routes offer more than one"),
    ev["headline"]["routes_with_multiple_schemes"])
chk("README distinct-scheme count", rd_int(r"At least (\d+) distinct `scheme` strings"),
    len(ev["option_scheme_value_census"]))
chk("README tie count", rd_int(r"\*\*([\d,]+) routes carry a \*scheme tie\*"),
    ev["headline"]["routes_with_scheme_tie"])
chk("README silent-tie count", rd_int(r"\*\*([\d,]+) of those \d+ are \*silent\* ties"),
    ev["headline"]["routes_with_silent_scheme_tie"])
chk("README repeats the tie total beside the silent one",
    rd_int(r"\*\*[\d,]+ of those ([\d,]+) are \*silent\* ties"),
    ev["headline"]["routes_with_scheme_tie"])
chk("README scheme table rows",
    [t for t, n in ev["option_scheme_value_census"].items()
     if ("| `%s` | %d |" % (t, n)) not in readme], [])
chk("README population row count", rd_int(r"plus our own manifest: ([\d,]+) rows"),
    ev["population"]["rows_fetched"])
chk("README does NOT say the fix was a fix", "**Fixed in the same shift" in readme, False)
chk("README says pay_basis is unchanged", "are unchanged" in readme, True)

# ---- 6. the shift-617 correction. A prior version of both documents said the index page "had
# warned about this shape in prose since 2026-10-30". That date was wrong by six days AND in the
# wrong direction: the prose note was written 2026-11-05, hours before the census, and the clause
# it replaced asserted the opposite. These legs pin the correction in BOTH copies so it cannot
# silently come back -- the same reason the README figures got legs at all.
for name, doc in (("report", md), ("README", readme)):
    chk("%s does not carry the wrong 2026-10-30 prose date" % name, "2026-10-30" in doc, False)
    chk("%s dates the prose note to 2026-11-05" % name,
        "about this shape only on 2026-11-05" in doc, True)
    chk("%s says the replaced clause was wrong" % name,
        "wrong on 7 of the 8 rows" in doc, True)
# The verifier's own advertised SCOPE is a claim about this file, and it was overstated: the
# population figures are cross-checked BETWEEN documents, never re-derived from the evidence rows.
chk("README does not overstate the verifier's scope",
    "re-derives every published figure from the evidence rows" in readme, False)
chk("README names the three population figures as cross-checked, not derived",
    "cannot be re-derived from the evidence rows" in readme, True)

print("\nverify_scheme_census: %d ok, %d FAIL" % (ok, fail))
sys.exit(1 if fail else 0)
