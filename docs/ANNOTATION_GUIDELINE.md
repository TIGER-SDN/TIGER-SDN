# GOLD-350 Annotation Guideline (v1.0)

You are labeling natural-language SDN intent instructions. For each
instruction you assign exactly one **category**, a **status**, and (for
rejected cases) a **rejection reason**. Label only from the instruction text
and this guideline. Do not consult any other file, dataset, or source.

## 1. Network inventory (normative)

Only these entities exist. Anything else is out of inventory.

| Entity | Names |
|--------|-------|
| Hosts | `h1` = 10.0.0.1, `h2` = 10.0.0.2, `h3` = 10.0.0.3, `h4` = 10.0.0.4 |
| Switches | switch 1 (`s1`), switch 2 (`s2`), switch 3 (`s3`), switch 4 (`s4`) |
| Valid ports | s1: 1, 2, 3, 4, 9 · s2: 1, 2 · s3: 1, 2 · s4: 1, 2, 3, 4 |
| Middleboxes | a firewall attached to switch 1 port 9; switch 2 hosts IDS/DPI/LB/proxy/scrubbing services; switch 3 hosts monitoring/logging services |

Topology: h1, h2 attach to s1; h3, h4 attach to s4. s1 and s4 are joined via
s2 (default path) and via s3 (alternate path).

Service names map to protocols: HTTP/web = tcp/80, HTTPS/secure web =
tcp/443, DNS/name resolution = udp/53, SSH = tcp/22, FTP = tcp/21,
SMTP/mail = tcp/25, telnet = tcp/23, ping = ICMP, NTP = udp/123. Named ports
("TCP port 8080") are always valid.

## 2. What the controller can compile (capability list)

Supported: unidirectional flows between inventory endpoints; TCP/UDP/ICMP and
port matches; pinning a rule to a switch and/or egress port; VLAN tagging;
minimum-bandwidth guarantees, maximum-latency bounds, and queue assignment;
routing a flow through one or more middlebox waypoints; changing a flow's
path or egress port; dropping or allowing flows.

NOT supported (reject as `unsupported`): bandwidth caps / rate limiting
("at most X Mbps"), time-conditioned rules, NAT, VPN/tunnels, encryption,
traffic mirroring/copying, content caching, application-layer/content
filtering, protocol translation, and *dynamic* load balancing across paths
(contrast: steering a flow through "the load balancer on switch 2" is a
waypoint and IS supported).

## 3. Categories (7 labels)

| Label | Meaning |
|-------|---------|
| `forwarding` | Deliver one flow from A to B (optionally protocol/port/switch-specific). |
| `security` | Block/deny/drop one flow, or add an explicit allow/whitelist/ACL entry. |
| `qos` | Give one flow a performance guarantee: bandwidth floor, latency bound, and/or queue. |
| `sfc` | Steer one flow through one or more middlebox/service waypoints (firewall, IDS, DPI, LB, proxy, scrubber, monitor, logging, inspection) before delivery. |
| `reroute` | Change the path or egress port of a flow relative to an existing/default path (reroute, redirect, failover, bypass, avoid, "instead of", "change port X to Y"). |
| `compound` | One instruction containing **two or more separately compilable policy clauses** (see §4 rule 5). |
| `ambiguous_unsupported` | Cannot be compiled; must be rejected (see reasons in §5). |

`ambiguous_unsupported` is the only category with status `rejected`; all
other categories are `accepted`.

## 4. Decision procedure (apply in this order)

1. **Unknown entity.** If the instruction references any host, IP, switch, or
   port outside the inventory of §1 (e.g. `h7`, `10.0.0.50`, "switch 8",
   "the file server", port 6 on s3) → `ambiguous_unsupported`, reason
   `unknown_entity`. This wins even if the rest of the sentence is clear.
2. **Contradiction.** If the instruction demands two things that cannot both
   hold for the same flow (allow and block it; two different queues; two
   different egress ports at once; a guarantee for traffic it also drops;
   numerically impossible bounds) → `ambiguous_unsupported`, reason
   `contradictory`.
3. **Unsupported feature.** If satisfying the request requires a capability
   outside §2 → `ambiguous_unsupported`, reason `unsupported`.
4. **Too vague.** If no concrete flow (endpoints or match) or no concrete
   action can be identified ("make it better", "prioritize what matters") →
   `ambiguous_unsupported`, reason `ambiguous`.
5. **Count policy clauses.** A clause is one compilable policy: one flow with
   one action/guarantee. If the instruction contains **two or more clauses**,
   label `compound`. This includes:
   - two different flows ("forward A→B and C→D");
   - one flow listing two or more services/protocols ("block SSH and telnet
     from A to B" = two rules);
   - an exception plus a default ("forward everything from A to B except
     ICMP"; "drop all from A but keep forwarding the rest");
   - a guarantee plus a block ("give A→B queue 1 and block C").
   NOT compound:
   - a service chain is ONE clause even though it compiles to several rules
     ("route A→B through the firewall, then the IDS" → `sfc`);
   - post-inspection outcomes attached to a chain ("inspect at the firewall
     and drop anything malicious") belong to the chain → `sfc`;
   - failure conditions attached to a path change ("if s2 fails, use s3")
     belong to the reroute → `reroute`;
   - one flow with several QoS constraints ("queue 1 and 10 Mbps and under
     5 ms") is ONE qos clause → `qos`.
6. **Middlebox waypoint → `sfc`.** A single clause that sends a flow
   *through* a named middlebox or service function (or "through port 9 of
   switch 1", the firewall port) for inspection/processing → `sfc`. Service
   nouns dominate path verbs: "redirect A→B through the firewall" is `sfc`,
   not `reroute`.
7. **Path change → `reroute`.** A single clause that changes which path or
   egress port a flow uses, motivated by failure, maintenance, congestion, or
   plain preference ("via s3 instead of s2", "avoid s3", "bypass s2",
   "change egress port to 2", "failover", conditional forms included) →
   `reroute`. A waypoint named purely as the alternate *route* (a switch with
   no service role invoked) is `reroute`, not `sfc`.
8. **Performance guarantee → `qos`.** Bandwidth floors, latency bounds, queue
   or priority assignment for one flow → `qos`, even when phrased with a
   forwarding verb ("send A→B through queue 2").
9. **Block or explicit allow-rule → `security`.** Blocking verbs (block,
   deny, drop, prevent, blackhole, cut off, "must not reach", "firewall off")
   → `security`. Explicit allow-rule phrasing (whitelist, "add an
   allow/permit/accept rule", ACL entry, security exception) → `security`.
   Note: a plain connectivity verb ("allow h1 to reach h3", "let A talk to
   B") with no rule/ACL framing is `forwarding`, not `security`.
10. **Otherwise → `forwarding`.** Plain delivery of one flow, including
    switch- or port-pinned delivery ("on switch 1, send traffic for X out
    port 3") when no *change* from an existing path is implied.

## 5. Rejection reasons

For `ambiguous_unsupported` also pick one reason, using the same precedence
as §4 rules 1-4: `unknown_entity` > `contradictory` > `unsupported` >
`ambiguous`.

## 6. Hard-case reference table

| Instruction pattern | Label | Why |
|---|---|---|
| "Firewall off A from B" | security | blocking verb, no waypoint traversal |
| "Redirect A→B through the IDS on s2" | sfc | service noun dominates path verb |
| "Reroute A→B via s3 instead of s2" | reroute | pure path change, no service role |
| "If s2 fails, send A→B via s3" | reroute | condition attached to path change |
| "Inspect A→B at the firewall; drop if malicious" | sfc | outcome attached to chain |
| "Forward A→B except SSH" | compound | exception + default = 2 clauses |
| "Block FTP and SMTP from A to B" | compound | two services = 2 rules |
| "Give A→B queue 1, under 10 ms" | qos | multiple constraints, one clause |
| "Allow h2 to reach h4" | forwarding | connectivity verb, no ACL framing |
| "Whitelist h2 to h4 traffic" | security | explicit allow-rule framing |
| "Guarantee A→B 20 Mbps and block C→B" | compound | qos clause + security clause |
| "Balance load across all paths dynamically" | ambiguous_unsupported/unsupported | no fixed waypoint; dynamic LB unsupported |
| "Prioritize the important traffic" | ambiguous_unsupported/ambiguous | no identifiable flow |
| "Send A→B via switch 9" | ambiguous_unsupported/unknown_entity | switch 9 not in inventory |

## 7. Output format

Write one JSON object per line, in the same order as the input file:

```json
{"blind_id": "B001", "category": "forwarding", "status": "accepted", "rejection_reason": null, "rationale": "one short sentence"}
```

- `category`: one of `forwarding`, `security`, `qos`, `sfc`, `reroute`,
  `compound`, `ambiguous_unsupported`.
- `status`: `accepted`, or `rejected` iff category is `ambiguous_unsupported`.
- `rejection_reason`: `ambiguous` | `contradictory` | `unknown_entity` |
  `unsupported` for rejected cases, else `null`.
- `rationale`: at most one sentence; cite the deciding rule (e.g. "§4.5
  exception+default").

Label every case. Do not skip, do not add fields, do not reorder.
