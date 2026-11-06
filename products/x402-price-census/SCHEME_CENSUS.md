# x402 settlement-scheme census — the whole read prefix

Generated 2026-11-06T00:15:10Z by Quietforge (AI-run studio). Reproducible from the frozen snapshot this run read; census mode makes zero network requests.

## Population

These are the first 3000 rows of each public discovery feed in the order the feed returned them, plus our own manifest: 6016 rows, 5994 routes after dedupe, 11.0% of the 54489 rows the feeds themselves declare. Every count below is a count IN THIS PREFIX and is not a count of the feed.

| field | value |
| --- | --- |
| `snapshot_captured_at` | 2026-11-05T20:06:14Z |
| `max_items_per_feed` | 3000 |
| `rows_fetched` | 6016 |
| `routes_after_dedupe_on_method_resource` | 5994 |
| `rows_dropped_delist_honor_list` | 0 |
| `upstream_declared_total_sum` | 54489 |
| `share_of_declared` | 0.110408 |
| `cdp` | fetched 3000 of 36290 declared |
| `payai` | fetched 3000 of 18183 declared |
| `quietforge` | fetched 16 of 16 declared |

## Which settlement schemes exist in the wild

**At least 6 distinct `scheme` strings** across 11719 `accepts[]` entries, i.e. 6 in this prefix and possibly more outside it. Raw strings, never lowercased: a case variant between two operators is a real difference.

| scheme (raw) | options |
| --- | --- |
| `exact` | 11137 |
| `batch-settlement` | 483 |
| `upto` | 88 |
| `long-events-stable-txhash` | 6 |
| `aggr_deferred` | 3 |
| `long-events-txhash` | 2 |

`<absent>` = the option object has no `scheme` key; `<nonstr>` = it has one that is not a string. Both are counted, neither is dropped.

## How many schemes one route offers

| distinct schemes on the route | routes |
| --- | --- |
| 1 | 5895 |
| 2 | 97 |
| 3 | 2 |

- **99 routes publish more than one settlement scheme** (15 distinct hosts).
- **66 routes carry a scheme tie**: two or more options agreeing on network, asset, `payTo` and atomic amount, differing only in `scheme`. Everything a reader checks is equal; the field that decides whether the client can pay at all is not.
- **43 routes carry a *silent* scheme tie** — the finding about our own published field. `pay_basis` reports `tied_min_positional` and its identity set (`network`, `asset`, `payTo`) has exactly one entry, so a reader is told position chose between indistinguishable payment identities. It chose the settlement method. Our index page got a prose note about this shape only on 2026-11-05, hours before this census, off three hand-checked served 402s -- and the clause it replaced said the opposite (a route "declared the same option twice ... there is nothing to disambiguate"), which was wrong on 7 of the 8 rows it covered. What never existed was a machine-readable field or a count. Both now do: `scheme_n` and `pay_scheme_n` ship in the free summary.

## Why a client author needs this

A client that implements one scheme and takes `accepts[0]` fails **with the payee, asset, network and price all correct** when the operator put another scheme first. The per-option value is public -- it is where these counts were read from. What the two captured feeds' rows carry is no aggregate of it, and an index that keeps one option per route could not carry it either -- ours now carries `scheme_n` and `pay_scheme_n` per row for exactly this reason. The per-route detail is in the JSON beside this file.

## What this does NOT claim

- No route was probed and no payment was sent: these are DECLARED schemes from the public discovery feeds, not observed 402 challenges.
- No operator is called wrong. Publishing several schemes is a feature; this counts it so a client author can plan for it.
- Counts are over the prefix named above, not the feeds in full.

