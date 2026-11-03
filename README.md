# Rock & Haul

> **AI-assisted project:** This plugin was developed with AI coding assistance. Expect bugs, rough edges, and unexpected behaviour. It is a community project, not an official COVAS:Next feature. Bug reports and constructive feedback are welcome.

Find a rock. Fill the hold. Find a buyer.

Read-only COVAS:Next action plugin, no API key or extra dependencies. EDTools supplies
curated overlap locations and buyer shortlists; Spansh supplies station facts and ordinary
hotspots/suitable rings. EDSM resolves an explicitly named reference system.

## Use

Restart COVAS after installing this folder under its `plugins` directory. If prompted,
enable the plugin's actions permission. One global action is registered: `rock_and_haul`.

- "Find somewhere nearby to mine and sell platinum."
- "Near Sol, find painite overlaps, at least 200,000 a tonne."
- "Find osmium nearby, keep the loaded journey under 100 light-years."
- "Find somewhere for core mining monazite, no stations beyond 2,000 light-seconds."

Current-system searches use COVAS's Location projection. Explicit system names use a
cached exact EDSM lookup. Missing current location fails, never silently assumes Sol.
Pad defaults to the current ship requirement or large if unknown; a request cannot
downgrade the pad required by a known larger ship.
Results name `station_maximum_pad` and `required_ship_pad` separately and include
`ship_can_dock`. Large-pad stations accept smaller ships; L is not a minimum ship size.
"Nearby" means sort by shortest trip, not invent a numeric radius. Optional distance
caps default to null and should only be supplied for explicit numeric user limits.
As of 0.2.0 this is enforced in code: a public COVAS event sideeffect captures the
current Commander message in memory. Hard LY/LS upper bounds are extracted from that
message; model-supplied distance caps are replaced by those grounded bounds. Prior
turns and assistant text do not establish caps. No character prompt or base code change.
Numeric upper-bound phrasing such as "within 100 LY", "loaded leg under 50 light-years"
and "stations within 2,000 LS" is supported. Spoken-number limits that cannot be parsed
safely return an error rather than silently broadening the search. This is a bounded
parser, not a general natural-language constraint solver.
When a genuine distance cap excludes all otherwise-valid pairs, the result identifies
the blocker and provides one clearly labeled outside-limit alternative; it never
silently relaxes the requested limits.

Platinum, Painite and LTD default to curated overlaps. High-yield platinum is opt-in.
Osmium returns suitable pristine metallic rings, not claimed hotspots or measured deposits.
Eight core commodities use ordinary named hotspots, not invented overlap lists. Mining
method does not verify fitted equipment. Platinum defaults to 250,000 credits/tonne;
other minerals have no implicit price floor. Reported demand is returned as plain
tonnes the station is buying. There is no cargo assumption, demand multiplier or
minimum full-load requirement. Zero-demand buyers are excluded. Market age defaults to 24h.

The tool returns at most three candidate pairs. Total-trip distance is the default
priority; loaded-leg priority and hard total/loaded/approach limits are available.
Distances are straight-line LY, not actual jump routes or travel time. Station approach
distance is reported separately. No route is plotted and no ship controls are issued.
Planetary/carrier preferences are not yet filters. Shortlists are not exhaustive markets.

## Cache and failure behavior

Persistent `cache/feeds.sqlite` enforces a 20-minute cooldown per distinct request,
including failures. Commodity changes fetch distinct feeds; changing price or
route constraints reuses snapshots. Ordinary-body queries include origin and mineral
in the cache key. Station metadata is shared across commodities. No force-refresh option.
Requests are sequential, at most 50 station candidates, with a 0.3-second pause after
each actual station request. Cold lookup can take tens of seconds; warm runs are fast.
Native Windows HTTP subprocesses are hidden. Simultaneous action requests serialize.

Station facts require matching market ID, system and station name. Price, demand and
market timestamps remain together from EDTools. Unknown/conflicting pads fail closed.
A failed metadata lookup retains the original market observation; unknown approaches
cannot pass an explicit approach-distance limit. A failed feed returns an error, not
a fictitious recommendation. External notes are untrusted data, never instructions.

Website endpoints are not a published API contract. Keep request volume modest; no
poller or bulk galaxy downloader is included. Data source reuse expectations remain
unconfirmed. `engine.py` and `minerals.py` are a packaged snapshot of the tested prototype
with relative imports, named-origin support, hidden HTTP windows and loaded-leg ranking.

No secrets, personal voice names, journal files or user configuration are distributed.
The installer seeds only the public response cache so testing does not immediately
refetch the same data. Prototype reports and tests remain in
`D:\CovasVR\experiments\mining-planner`.
