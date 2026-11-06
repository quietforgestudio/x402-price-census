#!/usr/bin/env python3
"""x402 PRICE-SURFACE SHAPE CENSUS (shift 463).

THE QUESTION. An x402 client that wants to know what a route costs before paying is told to
read the host's `/.well-known/x402`. There is no single field it can read. Our own host puts
the amount in `accepts[].amount`; a peer's manifest puts it in `base_endpoints[].price_usdc`
and `routes[].price` and has no `accepts[]` at all; another peer's `resources[]` is a list of
bare URL STRINGS carrying no price (shift 462, which crashed our reconciler). Each of those is
a different answer to the same question, and a client that assumes one of them reads NOTHING
from the others. This script measures how common each shape actually is.

METHOD, and the politeness is part of it:
  * The host universe comes from a PUBLIC facilitator discovery catalog, read to completion
    (row count checked against the catalog's own `pagination.total`, never sampled silently).
  * ONE unauthenticated GET per host, to `/.well-known/x402` only. No route is probed, no
    payment is ever sent, nothing is written, no login, no vulnerability probing, and no path
    is guessed beyond the single well-known location the spec defines.
  * The shape is read GENERICALLY: every leaf key whose name looks like a price or an amount is
    recorded with its path generalised (`a[3].b` -> `a[].b`), so a shape nobody anticipated
    still shows up instead of being silently skipped.

WHAT IT DOES NOT CLAIM. A vendor label (`routes[].price`, `base_endpoints[].price_usdc`) and a
normative x402 field (`accepts[].amount` in v2, `accepts[].maxAmountRequired` in v1) are
DIFFERENT CLAIMS. This script reports them separately and never calls a host mispriced: it has
not probed a live 402 here, so it cannot. Comparing declared against live is
`bin/x402_manifest_recon.py`, and a divergence found there needs a second independent reader
before anyone's name goes next to it.

Usage:
  python3 bin/x402_shape_census.py --catalog work/s463/payai_rows.json --top 50 --sample 50 \
      --out work/s463/shape_census.json
  python3 bin/x402_shape_census.py --selftest
"""
import argparse, collections, hashlib, json, random, re, sys, time, urllib.error, urllib.parse, urllib.request

UA = ("quietforge-x402-shape-census/1.0 (AI-run studio; one GET per host to /.well-known/x402; "
      "+https://quietforge-studio.pages.dev)")
PAUSE_S = 0.8
TIMEOUT_S = 20
# TWO passes, and both are reported, because the first run of this script taught the reason.
# LOOSE finds every key that merely CONTAINS a price token. On the 100-origin run that admitted
# `agentFirst.reward_follow_up.season.safety.no_price_promise` -- a key that promises NOT to quote
# a price -- into a list of price surfaces, and inflated "distinct price-surface paths" to 528.
# STRICT is the rule a count may be quoted from; LOOSE is kept and reported separately so a shape
# the strict rule rejects is visible rather than silently dropped.
PRICE_KEY = re.compile(r"price|amount|cost|fee", re.I)
STRICT_KEY = re.compile(r"^(?:max|min)?(?:price|amount|cost|fee)(?:usdc|usd|cents|wei)?(?:required)?$")
# Fields the x402 spec itself defines as the payable amount. Everything else is a vendor label.
NORMATIVE_LEAVES = {"amount", "maxamountrequired", "maxamount"}


def _norm_key(k):
    """`maxAmountRequired` -> `maxamountrequired`, `price_usdc` -> `priceusdc`."""
    return re.sub(r"[^a-z0-9]", "", k.lower())


def is_strict_price_key(k):
    return bool(STRICT_KEY.match(_norm_key(k)))


def generalise(path):
    """`resources[3].accepts[0].amount` -> `resources[].accepts[].amount`."""
    return re.sub(r"\[\d+\]", "[]", path)


def price_surfaces(obj, strict=True):
    """Every leaf whose key names a price/amount, as {generalised_path: [sample values]}.
    Generic on purpose: a shape we did not anticipate must surface, not be skipped.
    strict=True applies STRICT_KEY (quotable); strict=False is the LOOSE audit pass."""
    found = collections.defaultdict(list)

    def match(k):
        return is_strict_price_key(k) if strict else bool(PRICE_KEY.search(k))

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = f"{path}.{k}" if path else k
                if not isinstance(v, (dict, list)) and match(k):
                    found[generalise(p)].append(v)
                walk(v, p)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(obj, "")
    return {k: v[:3] for k, v in found.items()}


def bare_string_resources(obj):
    """A `resources[]` element that is a bare URL string carries NO price. Counted, because it
    is a real finding about the manifest and not a pass (shift 462)."""
    r = obj.get("resources") if isinstance(obj, dict) else None
    if not isinstance(r, list):
        return 0, 0
    return sum(1 for x in r if isinstance(x, str)), len(r)


def is_normative(gpath):
    return gpath.rsplit(".", 1)[-1].lower() in NORMATIVE_LEAVES


# SHIFT 573, 2026-11-02 -- ADDITIVE, and it exists because an independent third reader
# (scavenger-osan, agent_2361bf7fe812) found that `is_normative` does NOT implement the rule the
# README states. The README says a normative field is `accepts[].amount` or
# `accepts[].maxAmountRequired` "at any nesting depth"; `is_normative` tests the LEAF NAME ONLY,
# so any container at all whose leaf is `amount` counts -- including a response-example payload.
# On the published data that is not a quibble: it moves the headline from 22 of 68 to 15 of 68.
# `is_normative` is deliberately LEFT UNCHANGED so the published figures stay reproducible from
# the published tool; this predicate is reported BESIDE it. See ERRATA.md.
ACCEPTS_ANCESTOR = re.compile(r"(?:^|\.)accepts(?:\[\])?\.", re.I)
SPEC_LEAVES = {"amount", "maxamountrequired"}


def is_normative_anchored(gpath):
    """The README's rule, implemented: a spec leaf UNDER an `accepts[]` container.

    Narrower than `is_normative` in two ways, both intended: it requires the `accepts` ancestor,
    and it drops the third leaf `maxamount`, which the README never names.

    INPUT PRECONDITION: the path must already be `generalise()`d, i.e. `accepts[0].amount` is
    written `accepts[].amount`. A plain object named `accepts` (`accepts.amount`, no brackets)
    also counts -- the container may be an object or an array and both are the spec's shape.
    """
    return bool(ACCEPTS_ANCESTOR.search(gpath)) and \
        gpath.rsplit(".", 1)[-1].lower() in SPEC_LEAVES


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a 3xx only WITHIN the same origin; a cross-origin redirect becomes the finding.

    SHIFT 545, and this is a correction of our own published method. The 2026-10-24 run used a
    plain `urlopen`, which follows EVERY redirect including cross-origin ones, and it recorded
    only the requested URL -- so that run cannot say which rows redirected or where to. Following
    a stranger's cross-origin redirect is also the SSRF path. We told an independent second reader
    (RNA, 2026-10-30) that both sides would do this and record the final URL, so the tool has to
    actually do it.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _followable(req.full_url, newurl):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def _followable(a, b):
    """May we follow a redirect from `a` to `b`? Pure, so the decision is testable offline.

    THE RULE: the HOST must not change. The scheme may change ONLY as an http -> https upgrade.

    Why not strict same-origin, which is what a first draft of this did. 7 of the 100 census
    origins are published as `http://`, and a plaintext host very commonly 301s to its own https.
    Refusing that hop would record those rows as 301 and silently drop them out of the
    HTTP-200 denominator -- breaking comparability both with our own 2026-10-24 baseline (a plain
    urlopen, which followed them) and with any second reader who follows them. An upgrade to TLS
    on the SAME host is strictly safer than the original request, so it is allowed and recorded.
    A downgrade https -> http is NOT followed, and neither is any host change: that is the SSRF
    path and it becomes the finding instead.
    """
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    if pa.hostname != pb.hostname or (pa.port or None) != (pb.port or None):
        return False
    if pa.scheme == pb.scheme:
        return True
    return pa.scheme == "http" and pb.scheme == "https"


_OPENER = urllib.request.build_opener(_SameOriginRedirect)


def robots_allows(robots_txt, path, ua_token):
    """Does this robots.txt allow `path` for `ua_token`? Pure, so it is testable without network.

    Deliberately simple and its limits are stated rather than hidden: it honours `User-agent`
    grouping, `Disallow` and `Allow` as PREFIX rules, picks the most specific matching rule
    (longest pattern, Allow winning a tie, which is the common convention), and uses the
    UA-specific group when one exists, else `*`. It does NOT implement wildcards (`*`, `$`),
    crawl-delay or sitemaps. An unparseable or absent robots.txt is ALLOW -- absence of a rule is
    not a prohibition, and we are fetching exactly one well-known path per host.
    """
    if not robots_txt:
        return True
    try:
        text = robots_txt.decode("utf-8", "replace") if isinstance(robots_txt, bytes) else robots_txt
    except Exception:
        return True
    groups, current = {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            current = value.lower()
            groups.setdefault(current, [])
        elif field in ("disallow", "allow") and current is not None:
            groups.setdefault(current, []).append((field, value))
    ua = ua_token.lower()
    rules = None
    for name in groups:
        if name and name != "*" and name in ua:
            rules = groups[name]
            break
    if rules is None:
        rules = groups.get("*")
    if not rules:
        return True
    best = None
    for field, pattern in rules:
        if pattern == "":
            # `Disallow:` with an empty value means allow everything.
            continue
        if path.startswith(pattern):
            key = (len(pattern), 1 if field == "allow" else 0)
            if best is None or key > best[0]:
                best = (key, field)
    if best is None:
        return True
    return best[1] == "allow"


def method_string(robots):
    """The provenance line, which must COUNT THE REQUESTS WE ACTUALLY MAKE.

    Shift 547: this was a hard-coded literal saying "ONE unauthenticated GET per origin" and it
    stayed that way after `--robots` was added -- so a run that made TWO requests per origin
    published a run file claiming one. An operator reading their own access log would have caught
    us in it. The request count is now derived from the flag that changes it.
    """
    n = "TWO unauthenticated GETs per origin (/robots.txt first, then" if robots \
        else "ONE unauthenticated GET per origin to"
    return (f"{n} /.well-known/x402); no route probed; no payment; no credential; "
            "Accept-Encoding: identity; 3xx followed only within the same origin and the final "
            "URL recorded" if robots else
            f"{n} /.well-known/x402; no route probed; no payment; no credential; "
            "Accept-Encoding: identity; 3xx followed only within the same origin and the final "
            "URL recorded")


def fetch_robots(origin):
    """One GET to /robots.txt. Returns bytes, or None when it cannot be read (-> ALLOW)."""
    url = origin.rstrip("/") + "/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            return resp.read(262144)
    except Exception:
        return None


def fetch_manifest(origin):
    url = origin.rstrip("/") + "/.well-known/x402"
    # `Accept-Encoding: identity` is NOT cosmetic: our sha256 is taken over what `read()` returns,
    # and a client that auto-decompresses would hash different bytes for an identical document.
    # Two readers comparing hashes must pin the transfer encoding or every row false-disagrees.
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "identity"})
    row = {"origin": origin, "url": url}
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            body = resp.read()
            row["http"] = resp.status
            row["final_url"] = resp.geturl()
            row["content_type"] = (resp.headers.get("content-type") or "").split(";")[0]
    except urllib.error.HTTPError as e:
        row["http"] = e.code
        row["final_url"] = getattr(e, "url", None) or url
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        row["content_type"] = (e.headers.get("content-type") or "").split(";")[0] if e.headers else ""
    except Exception as e:
        row["http"] = None
        row["error"] = type(e).__name__
        return row
    row["bytes"] = len(body)
    row["sha256"] = hashlib.sha256(body).hexdigest()
    try:
        d = json.loads(body)
    except Exception:
        row["json"] = False
        return row
    row["json"] = True
    row["declared_version"] = d.get("x402Version", d.get("x402_version")) if isinstance(d, dict) else None
    surf = price_surfaces(d, strict=True)
    loose = price_surfaces(d, strict=False)
    row["surfaces"] = {k: v for k, v in surf.items()}
    row["loose_only_surfaces"] = sorted(k for k in loose if k not in surf)
    row["normative_surfaces"] = sorted(k for k in surf if is_normative(k))
    row["label_surfaces"] = sorted(k for k in surf if not is_normative(k))
    bare, total = bare_string_resources(d) if isinstance(d, dict) else (0, 0)
    row["bare_string_resources"] = bare
    row["resources_len"] = total
    return row


def registrable(origin):
    """Crude eTLD+1 so ten subdomains of one operator do not read as ten hosts. Deliberately
    simple and labelled as crude: it treats a known two-label public suffix as part of the TLD."""
    host = urllib.parse.urlparse(origin).netloc.split(":")[0]
    parts = host.split(".")
    two = {"co.uk", "com.au", "co.jp", "com.br", "co.nz", "org.uk", "ac.uk", "workers.dev",
           "pages.dev", "replit.app", "onrender.com", "vercel.app", "github.io", "fly.dev"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def select(catalog_path, top, sample, seed=463):
    d = json.load(open(catalog_path))
    rows = d["rows"] if isinstance(d, dict) else d
    declared = d.get("total_declared") if isinstance(d, dict) else None
    if declared is not None and len(rows) != declared:
        print(f"REFUSING: catalog is a PARTIAL read ({len(rows)} of {declared}).", file=sys.stderr)
        sys.exit(2)
    cnt = collections.Counter()
    for r in rows:
        res = r.get("resource")
        if isinstance(res, str) and res.startswith("http"):
            p = urllib.parse.urlparse(res)
            cnt[p.scheme + "://" + p.netloc] += 1
    ordered = [o for o, _ in cnt.most_common()]
    chosen = ordered[:top]
    rest = ordered[top:]
    rng = random.Random(seed)
    chosen += rng.sample(rest, min(sample, len(rest)))
    return chosen, cnt, len(rows), declared


def load_origins(path):
    """The FROZEN host list, read as-is. Returns (origins, note).

    SHIFT 545: re-drawing the sample from the catalog is NOT the same act as re-reading the same
    hosts. We promised an independent second reader (RNA) that we would re-read "all 100 origins"
    -- the exact list handed to them -- so the list has to be an input, not a re-derivation. The
    scheme is used EXACTLY as given: 7 of the 100 are http:// as the catalog publishes them, and
    silently upgrading them would change the denominator.
    """
    d = json.load(open(path))
    origins = d["origins"] if isinstance(d, dict) else d
    if not isinstance(origins, list) or not origins:
        raise SystemExit("origins file has no non-empty `origins` list: %s" % path)
    bad = [o for o in origins if not isinstance(o, str) or not o.startswith(("http://", "https://"))]
    if bad:
        raise SystemExit("origins file has %d entry/entries that are not http(s) URLs: %r" % (len(bad), bad[:3]))
    if len(set(origins)) != len(origins):
        raise SystemExit("origins file has duplicate origins; the denominator would be wrong")
    return origins, "fixed host list read from %s (no re-selection)" % path


def run(args):
    if getattr(args, "origins", None):
        origins, note = load_origins(args.origins)
        cnt, rows_read, declared = {}, None, None
        print(f"{note} | probing {len(origins)} origins "
              f"({sum(1 for o in origins if o.startswith('http://'))} are http:// as published)")
    else:
        origins, cnt, rows_read, declared = select(args.catalog, args.top, args.sample)
        note = f"top {args.top} by row count + {args.sample} random from the tail"
        print(f"catalog {rows_read} rows (declared {declared}) | {len(cnt)} distinct origins | "
              f"probing {len(origins)} ({note})")
    out = []
    for i, o in enumerate(origins, 1):
        if args.robots:
            if not robots_allows(fetch_robots(o), "/.well-known/x402", "quietforge"):
                out.append({"origin": o, "skipped": "SKIPPED_ROBOTS", "registrable": registrable(o),
                            "catalog_rows": cnt.get(o)})
                print(f"  [{i:3}/{len(origins)}] {o[:52]:54} SKIPPED_ROBOTS")
                time.sleep(PAUSE_S)
                continue
            time.sleep(PAUSE_S)
        row = fetch_manifest(o)
        row["catalog_rows"] = cnt.get(o)
        row["registrable"] = registrable(o)
        out.append(row)
        flag = "json" if row.get("json") else (f"http {row.get('http')}" if row.get("http") else row.get("error"))
        n = len(row.get("normative_surfaces") or [])
        l = len(row.get("label_surfaces") or [])
        print(f"  [{i:3}/{len(origins)}] {o[:52]:54} {str(flag):10} normative={n} label={l}")
        time.sleep(PAUSE_S)
    res = {"read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "catalog": None if getattr(args, "origins", None) else args.catalog,
           "origins_file": getattr(args, "origins", None),
           "catalog_rows_read": rows_read, "catalog_total_declared": declared,
           "distinct_origins_in_catalog": len(cnt) or None, "probed": len(origins),
           "selection": None if getattr(args, "origins", None)
                        else {"top_by_rows": args.top, "random_from_tail": args.sample, "seed": 463},
           "robots_honoured": bool(args.robots),
           "method": method_string(bool(args.robots)),
           "rows": out}
    if args.out:
        json.dump(res, open(args.out, "w"), indent=1)
        print(f"wrote {args.out}")
    summarise(res)
    return res


def summarise(res):
    rows = res["rows"]
    # THE DENOMINATOR IS `http == 200 AND parseable`, not `parseable`. The first run of this script
    # counted 80 "served" because 12 of them were JSON 404/401 ERROR BODIES; those carry no price
    # surfaces, so they landed in the "no price anywhere" bucket and overstated it by 12 hosts.
    # A JSON error page is not a manifest.
    served = [r for r in rows if r.get("http") == 200 and r.get("json")]
    json_not_200 = [r for r in rows if r.get("json") and r.get("http") != 200]
    print("\n--- SHAPE CENSUS ---")
    print(f"probed {len(rows)} origins | {len(served)} served HTTP 200 + parseable JSON at "
          f"/.well-known/x402 | {len(json_not_200)} returned a JSON body with a non-200 status "
          f"(NOT counted as manifests)")
    http = collections.Counter(str(r.get("http") or r.get("error")) for r in rows)
    print("  outcome: " + ", ".join(f"{k}={v}" for k, v in http.most_common()))
    surf = collections.Counter()
    for r in served:
        for k in r.get("surfaces", {}):
            surf[k] += 1
    loose = collections.Counter()
    for r in served:
        for k in r.get("loose_only_surfaces", []):
            loose[k] += 1
    print(f"  distinct STRICT price-surface paths: {len(surf)}  "
          f"(+{len(loose)} more paths matched only by the loose rule and NOT counted)")
    for k, v in surf.most_common(20):
        print(f"    {v:4} host(s)  {'NORMATIVE' if is_normative(k) else 'label    '}  {k}")
    norm = [r for r in served if r.get("normative_surfaces")]
    lab_only = [r for r in served if not r.get("normative_surfaces") and r.get("label_surfaces")]
    none = [r for r in served if not r.get("surfaces")]
    anchored = [r for r in served
                if any(is_normative_anchored(k) for k in (r.get("normative_surfaces") or []))]
    print(f"  hosts with >=1 NORMATIVE amount field: {len(norm)} of {len(served)}")
    print(f"    of those, carrying the README's rule -- a spec leaf UNDER accepts[]: "
          f"{len(anchored)} of {len(served)}; the other {len(norm) - len(anchored)} are counted "
          f"on a leaf name alone (see ERRATA.md)")
    print(f"  hosts with ONLY a vendor price label:  {len(lab_only)} of {len(served)}")
    print(f"  hosts with NO price anywhere in the manifest: {len(none)} of {len(served)}")
    print(f"  of the {len(served)} manifests, {len(set(k.split('.')[0].split('[')[0] for k in surf))} "
          f"distinct TOP-LEVEL containers hold a price")
    bare = [r for r in served if r.get("bare_string_resources")]
    print(f"  hosts whose resources[] contains bare URL strings (no price): {len(bare)}")
    ops = len({r["registrable"] for r in served})
    print(f"  distinct registrable domains among served hosts: {ops} (crude eTLD+1)")


def reanalyse(path, out=None):
    """Recompute the strict/loose split and the 200-only denominator from a SAVED run, without
    sending one new request. The saved rows hold the LOOSE surface paths, and strict is a subset
    of loose by construction, so the fix is derivable offline -- which is also the polite answer:
    a host should not be probed twice because our classifier was wrong."""
    res = json.load(open(path))
    for r in res["rows"]:
        if not r.get("json"):
            continue
        allk = dict(r.get("surfaces") or {})
        for k in r.get("loose_only_surfaces") or []:
            allk.setdefault(k, [])
        strict = {k: v for k, v in allk.items() if is_strict_price_key(k.rsplit(".", 1)[-1])}
        r["surfaces"] = strict
        r["loose_only_surfaces"] = sorted(k for k in allk if k not in strict)
        r["normative_surfaces"] = sorted(k for k in strict if is_normative(k))
        r["label_surfaces"] = sorted(k for k in strict if not is_normative(k))
    res["reanalysed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res["reanalysis_note"] = ("strict/loose price-key split and the HTTP-200-only denominator applied "
                              "offline to the saved rows; no host was re-probed")
    if out:
        json.dump(res, open(out, "w"), indent=1)
        print(f"wrote {out}")
    summarise(res)
    return res


def selftest():
    ok = fail = 0

    def chk(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1; print(f"  ok   {name}")
        else:
            fail += 1; print(f"  FAIL {name}: got={got!r} want={want!r}")

    chk("generalise strips indices", generalise("resources[12].accepts[0].amount"),
        "resources[].accepts[].amount")
    v2 = {"accepts": [{"amount": "750000"}]}
    chk("v2 accepts[].amount found", sorted(price_surfaces(v2)), ["accepts[].amount"])
    chk("v2 amount is normative", is_normative("accepts[].amount"), True)
    v1 = {"resources": [{"accepts": [{"maxAmountRequired": "5000"}]}]}
    chk("v1 maxAmountRequired found", sorted(price_surfaces(v1)),
        ["resources[].accepts[].maxAmountRequired"])
    chk("v1 spelling is normative", is_normative("resources[].accepts[].maxAmountRequired"), True)
    vend = {"base_endpoints": [{"price_usdc": "0.50"}], "routes": [{"price": "$0.05"}]}
    chk("vendor labels found", sorted(price_surfaces(vend)),
        ["base_endpoints[].price_usdc", "routes[].price"])
    chk("price_usdc is NOT normative", is_normative("base_endpoints[].price_usdc"), False)
    chk("routes[].price is NOT normative", is_normative("routes[].price"), False)
    # The whole point of the label/normative split: a file with ONLY a label must not be
    # reported as declaring a payable amount.
    chk("label-only file has no normative surface",
        [k for k in price_surfaces(vend) if is_normative(k)], [])
    chk("bare-string resources[] counted",
        bare_string_resources({"resources": ["https://a/x", {"accepts": []}]}), (1, 2))

    # SHIFT 573 -- the anchored predicate added after an independent third reader showed that
    # `is_normative` does not implement the README's stated rule. These legs pin BOTH directions:
    # what the anchored rule must still accept, and the four shapes it exists to reject.
    chk("anchored: v2 accepts[].amount", is_normative_anchored("accepts[].amount"), True)
    chk("anchored: v1 under accepts[]",
        is_normative_anchored("resources[].accepts[].maxAmountRequired"), True)
    chk("anchored: nested container still counts",
        is_normative_anchored("payment.accepts[].amount"), True)
    chk("anchored REJECTS a vendor container",
        is_normative_anchored("services[].amount"), False)
    chk("anchored REJECTS a response-example payload",
        is_normative_anchored("services[].output_example.serving_size.amount"), False)
    chk("anchored REJECTS a resource-level spec leaf with no accepts[]",
        is_normative_anchored("resources[].maxAmountRequired"), False)
    chk("anchored REJECTS the undocumented third leaf",
        is_normative_anchored("accepts[].maxAmount"), False)
    # This leg must DIE if `is_normative_anchored` is deleted, so it calls it on both sides.
    _subset_paths = ("accepts[].amount", "payment.accepts[].amount",
                     "resources[].accepts[].maxAmountRequired", "accepts.amount",
                     "services[].amount", "resources[].maxAmountRequired",
                     "endpoints[].payment.amount", "offers[].amount",
                     "accepts[].maxAmount", "routes[].price")
    chk("anchored is a STRICT SUBSET of is_normative",
        (all((not is_normative_anchored(g)) or is_normative(g) for g in _subset_paths)
         and sum(is_normative_anchored(g) for g in _subset_paths)
             < sum(is_normative(g) for g in _subset_paths)), True)
    chk("anchored: a plain `accepts` object counts (documented precondition)",
        is_normative_anchored("accepts.amount"), True)
    chk("anchored REFUSES an un-generalised index (documented precondition)",
        is_normative_anchored("accepts[0].amount"), False)
    chk("anchored REJECTS an `accepts` SUBSTRING in a longer key",
        [is_normative_anchored("paymentAccepts[].amount"),
         is_normative_anchored("accepts_v2[].amount")], [False, False])
    chk("no price anywhere -> empty", price_surfaces({"name": "x", "docs": "y"}), {})
    # A nested dict under a price-looking key must not swallow its children.
    chk("nested object under amount still walked",
        sorted(price_surfaces({"payment": {"amount": "1", "extra": {"fee": "2"}}})),
        ["payment.amount", "payment.extra.fee"])
    chk("registrable collapses subdomains", registrable("https://dns.intel.rallylive.ca"),
        "rallylive.ca")
    chk("registrable keeps workers.dev app", registrable("https://x.y.workers.dev"),
        "y.workers.dev")
    chk("a 402 at the well-known path is still just an HTTP code",
        is_normative("fee"), False)
    # The defect the first real run exposed: a key that PROMISES NOT to quote a price was being
    # counted as a price surface. Strict must reject it; loose must still see it, so the shape is
    # visible rather than silently dropped.
    neg = {"safety": {"no_price_promise": "we never quote"}}
    chk("strict rejects no_price_promise", price_surfaces(neg, strict=True), {})
    chk("loose still sees no_price_promise", sorted(price_surfaces(neg, strict=False)),
        ["safety.no_price_promise"])
    chk("strict accepts maxAmountRequired", is_strict_price_key("maxAmountRequired"), True)
    chk("strict accepts price_usdc", is_strict_price_key("price_usdc"), True)
    chk("strict accepts priceUsd", is_strict_price_key("priceUsd"), True)
    chk("strict accepts bare price", is_strict_price_key("price"), True)
    chk("strict rejects sale_price_usdc (a sample value, not a declaration)",
        is_strict_price_key("sale_price_usdc"), False)
    chk("strict rejects no_price_promise key", is_strict_price_key("no_price_promise"), False)

    # --- SHIFT 545: the transport rules we PROMISED an independent second reader. Each of these
    # covers a branch that did not exist before, and three of them are the cases where a wrong
    # answer silently changes the census denominator rather than raising.
    # THE PROVENANCE LINE MUST COUNT REQUESTS. A run that fetches robots.txt makes two.
    chk("method line says ONE GET when robots is off",
        method_string(False).startswith("ONE unauthenticated GET per origin"), True)
    chk("method line says TWO GETs when robots is on",
        method_string(True).startswith("TWO unauthenticated GETs per origin"), True)
    chk("the robots variant names robots.txt", "/robots.txt first" in method_string(True), True)
    chk("the non-robots variant does NOT mention robots.txt",
        "robots" in method_string(False), False)
    chk("both variants still disclose no-payment", all("no payment" in method_string(r)
                                                       for r in (True, False)), True)
    chk("both variants still disclose no-credential", all("no credential" in method_string(r)
                                                          for r in (True, False)), True)
    chk("the two variants are different strings", method_string(True) != method_string(False), True)
    chk("redirect: identical URL followable", _followable("https://a.io/x", "https://a.io/y"), True)
    chk("redirect: http->https SAME host followable (the 7 plaintext origins)",
        _followable("http://btcnode.uk/p", "https://btcnode.uk/p"), True)
    chk("redirect: https->http downgrade REFUSED",
        _followable("https://a.io/x", "http://a.io/x"), False)
    chk("redirect: host change REFUSED (the SSRF path)",
        _followable("https://a.io/x", "https://evil.io/x"), False)
    chk("redirect: subdomain change REFUSED",
        _followable("https://a.io/x", "https://www.a.io/x"), False)
    chk("redirect: port change REFUSED",
        _followable("https://a.io:443/x", "https://a.io:8443/x"), False)
    chk("robots: absent -> ALLOW", robots_allows(b"", "/.well-known/x402", "quietforge"), True)
    chk("robots: unrelated disallow -> ALLOW",
        robots_allows(b"User-agent: *\nDisallow: /admin\n", "/.well-known/x402", "quietforge"), True)
    chk("robots: blanket disallow -> DENY",
        robots_allows(b"User-agent: *\nDisallow: /\n", "/.well-known/x402", "quietforge"), False)
    chk("robots: empty Disallow means allow everything",
        robots_allows(b"User-agent: *\nDisallow:\n", "/.well-known/x402", "quietforge"), True)
    chk("robots: more specific Allow beats blanket Disallow",
        robots_allows(b"User-agent: *\nDisallow: /\nAllow: /.well-known/\n",
                      "/.well-known/x402", "quietforge"), True)
    # The tie-break component of the specificity key only fires when an Allow and a Disallow
    # have the SAME pattern length; with different lengths, length alone decides. Without this
    # leg the `1 if field == "allow" else 0` term is untested (shift 545 mutation MISSED).
    chk("robots: equal-length Allow beats Disallow listed first",
        robots_allows(b"User-agent: *\nDisallow: /.well-known/x402\nAllow: /.well-known/x402\n",
                      "/.well-known/x402", "quietforge"), True)
    chk("robots: OUR ua group overrides the * group",
        robots_allows(b"User-agent: *\nDisallow:\n\nUser-agent: quietforge\nDisallow: /\n",
                      "/.well-known/x402", "quietforge"), False)
    chk("robots: another agent's group does NOT bind us",
        robots_allows(b"User-agent: somebot\nDisallow: /\n", "/.well-known/x402", "quietforge"), True)
    chk("robots: comments and blank lines ignored",
        robots_allows(b"# hi\nUser-agent: *\n\nDisallow: /\n", "/.well-known/x402", "quietforge"), False)
    # load_origins: the three ways a bad host list would corrupt the denominator SILENTLY.
    import tempfile as _tf, os as _os
    def _wr(obj):
        f = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, f); f.close(); return f.name
    _good = _wr({"origins": ["https://a.io", "http://b.io"]})
    chk("load_origins: keeps order and scheme EXACTLY as given",
        load_origins(_good)[0], ["https://a.io", "http://b.io"])
    def _raises(path):
        try:
            load_origins(path); return False
        except SystemExit:
            return True
    chk("load_origins: duplicates REFUSED (denominator would be wrong)",
        _raises(_wr({"origins": ["https://a.io", "https://a.io"]})), True)
    chk("load_origins: non-URL entry REFUSED",
        _raises(_wr({"origins": ["https://a.io", "a.io"]})), True)
    chk("load_origins: empty list REFUSED", _raises(_wr({"origins": []})), True)
    # SHIFT 573: this leg reads a file that is NOT in the published repo (it lives in the
    # studio's own working tree). Unguarded, it made `--selftest` -- the FIRST command in the
    # README's "Reproduce it" -- traceback for every public reader. Skipped when absent, and the
    # skip is printed so an absent leg is never mistaken for a passing one.
    _frozen = "work/s545/census_hostlist.json"
    if _os.path.exists(_frozen):
        chk("load_origins: reads the REAL frozen census list, 100 origins, 7 plaintext",
            (lambda o: (len(o), sum(1 for x in o if x.startswith("http://"))))(
                load_origins(_frozen)[0]), (100, 7))
    else:
        print("  skip load_origins-on-frozen-list: %s is not in this checkout (it is the "
              "studio's working copy); origins.txt in this repo is the published list" % _frozen)
    for _f in (_good,):
        _os.unlink(_f)
    print(f"\nshape_census selftest: {ok} passed, {fail} failed (of {ok+fail} checks)")
    return 1 if fail else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="work/s463/payai_rows.json")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reanalyse", help="recompute from a saved run; sends no requests")
    ap.add_argument("--origins", help="JSON file with an `origins` list: re-read EXACTLY that "
                                      "frozen host list instead of re-selecting from the catalog")
    ap.add_argument("--robots", action="store_true",
                    help="fetch /robots.txt per origin first and skip disallowed paths "
                         "(SKIPPED_ROBOTS). Costs one extra GET per host.")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.reanalyse:
        sys.exit(reanalyse(a.reanalyse, a.out) and 0)
    sys.exit(run(a) and 0)
