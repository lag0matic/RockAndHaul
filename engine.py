"""Read-only mineral mining planner. No COVAS integration or ship control."""
import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import re
import base64
from .minerals import MINERALS, mineral_name, location_policy, body_request, body_sites
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
TTL = 20 * 60
SOURCES = {
    "overlaps": "https://edtools.cc/miner?a=r&n=Platinum",
    "high_yield": "https://edtools.cc/list.php?a=r&ord=24",
    "buyers": "https://edtools.cc/miner?a=p&cid=46",
}
BODY_URL = "https://spansh.co.uk/api/bodies/search"


def allowed_url(url, body=None):
    if body is not None:
        return url == BODY_URL
    feeds = {f"https://edtools.cc/miner?a=p&cid={s['id']}" for s in MINERALS.values()}
    feeds.update(f"https://edtools.cc/miner?a=r&n={s['overlap']}" for s in MINERALS.values() if s.get("overlap"))
    parts = urlsplit(url)
    coordinate_lookup = (parts.scheme == "https" and parts.netloc == "www.edsm.net" and
                         parts.path == "/api-v1/system" and not parts.fragment and
                         set(parse_qs(parts.query)) == {"systemName", "showCoordinates"} and
                         "'" not in url and "\n" not in url and "\r" not in url)
    return coordinate_lookup or url in feeds or url == SOURCES["high_yield"] or bool(re.fullmatch(r"https://spansh\.co\.uk/api/station/[1-9][0-9]{0,19}", url))


class FeedCache:
    """Persistent per-feed cooldown, including failures; serialized across processes."""

    def __init__(self, path, fetch=None, clock=time.time):
        self.path, self.clock = path, clock
        self.fetch = fetch or self.download
        self.events = []

    @staticmethod
    def download(url, body=None):
        if not allowed_url(url, body):
            raise ValueError("Unrecognized feed URL")
        if sys.platform == "win32":
            # Use the native Windows HTTP stack, as used in the source investigation.
            # Only fixed feed URLs are accepted; no shell interpolation of user input.
            post = ""
            if body is not None:
                encoded = base64.b64encode(json.dumps(body).encode()).decode()
                post = (" -Method Post -ContentType 'application/json' -Body "
                        "([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')))")
            command = ("$ErrorActionPreference='Stop'; "
                       "$r=Invoke-WebRequest -UseBasicParsing -Uri '" + url + "' -TimeoutSec 25" + post + "; "
                       "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                       "[Console]::Write($r.Content)")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                    capture_output=True, timeout=35, check=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if len(result.stdout) > 8_000_000:
                raise ValueError("Feed exceeds size limit")
            return json.loads(result.stdout.decode("utf-8-sig"))
        req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers={"User-Agent": "Mozilla/5.0 CassiaMiningPrototype/0.1",
                               "Accept": "application/json", "Content-Type": "application/json"})
        with urlopen(req, timeout=25) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Feed exceeds size limit")
        return json.loads(raw)

    def get(self, url, body=None):
        key = url if body is None else url + "\n" + json.dumps(body, sort_keys=True, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=90)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS feeds (url TEXT PRIMARY KEY, attempted REAL, payload TEXT, error TEXT)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            row = db.execute("SELECT attempted,payload,error FROM feeds WHERE url=?", (key,)).fetchone()
            if row and now - row[0] < TTL:
                self.events.append({"url": url, "cache": "hit", "snapshot_age_seconds": round(now-row[0])})
                if row[2]:
                    raise RuntimeError("Feed failed recently; retry blocked until cooldown expires: " + row[2])
                return json.loads(row[1])
            try:
                data = self.fetch(url) if body is None else self.fetch(url, body)
                payload, error = json.dumps(data, allow_nan=False), None
            except Exception as exc:
                payload, error = None, f"{type(exc).__name__}: {exc}"
            db.execute("INSERT OR REPLACE INTO feeds VALUES (?,?,?,?)", (key, now, payload, error))
            db.commit()
            self.events.append({"url": url, "cache": "network", "fetched_at": now})
            if error:
                raise RuntimeError(error)
            return data


def coords(value):
    vals = [value[k] for k in ("x", "y", "z")] if isinstance(value, dict) else value
    if not isinstance(vals, (list, tuple)) or len(vals) != 3:
        raise ValueError("Expected three coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vals):
        raise ValueError("Invalid coordinates")
    return tuple(vals)


def distance(a, b):
    return math.dist(coords(a), coords(b))


def latest_location(directory):
    for path in sorted(directory.glob("Journal*.log"), reverse=True):
        found = None
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    e = json.loads(line)
                    if e.get("event") in ("Location", "FSDJump", "CarrierJump") and e.get("StarSystem"):
                        found = {"name": e["StarSystem"], "coords": coords(e["StarPos"]),
                                 "observed_at": e.get("timestamp"), "journal": str(path)}
                except (ValueError, KeyError, TypeError):
                    continue
        if found:
            return found
    raise ValueError("No journal location found; refusing to assume Sol")


def mining_sites(overlaps, high_yield):
    if not isinstance(overlaps, list) or not isinstance(high_yield, dict) or not isinstance(high_yield.get("list"), list):
        raise ValueError("Mining feed schema changed")
    result = []
    for category, rows in (("overlap", overlaps), ("high_yield", high_yield["list"])):
        for row in rows:
            if not isinstance(row.get("name"), str) or not row["name"].strip() or not row.get("comment"):
                raise ValueError("Incomplete mining entry")
            result.append({"system": row["name"], "coords": coords(row["coords"]),
                           "ring_notes": row["comment"], "category": category})
    # Comments are deliberately retained verbatim: no guessing at ring identity or measured yield.
    return result


def load_sites(cache, name, mode, method, origin):
    spec = MINERALS[name]
    if mode in ("overlaps", "high-yield"):
        rows = cache.get(f"https://edtools.cc/miner?a=r&n={spec['overlap']}") if mode == "overlaps" else []
        high = cache.get(SOURCES["high_yield"]) if mode == "high-yield" else {"list": []}
        result = mining_sites(rows, high)
        for site in result:
            site["method"] = method
            site["source"] = (f"https://edtools.cc/miner?a=r&n={spec['overlap']}" if mode == "overlaps" else SOURCES["high_yield"])
        return result
    payload = cache.get(BODY_URL, body_request(name, origin, mode))
    return body_sites(payload, name, mode, method)


def merge_station(buyer, payload, url):
    """Enrich identity-matched station facts without replacing market observations."""
    result = dict(buyer)
    record = payload.get("record") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        raise ValueError("Station record missing")
    if (record.get("market_id") != buyer["market_id"] or
            str(record.get("system_name", "")).casefold() != buyer["system"].casefold() or
            str(record.get("name", "")).casefold() != buyer["station"].casefold()):
        raise ValueError("Station identity mismatch")
    result["station_metadata"] = {"source": url, "updated_at": record.get("updated_at")}
    approach = record.get("distance_to_arrival")
    if isinstance(approach, (int, float)) and not isinstance(approach, bool) and math.isfinite(approach) and approach >= 0:
        result["station_approach_ls"] = approach
    # Explicit counts/boolean only, never infer landing access from station type.
    known_pads = []
    for size, field in (("S", "small_pads"), ("M", "medium_pads"), ("L", "large_pads")):
        count = record.get(field)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            known_pads.append(size)
    if record.get("has_large_pad") is True:
        known_pads.append("L")
    explicit_pad = max(known_pads, key="SML".index) if known_pads else None
    if explicit_pad:
        if buyer.get("pad") in ("S", "M", "L") and buyer.get("pad") != explicit_pad:
            result["pad_conflict"] = {"edtools": buyer["pad"], "spansh": explicit_pad}
        else:
            result["pad"] = explicit_pad
            result["pad_source"] = url
    result["station_type"] = record.get("type")
    result["is_planetary"] = record.get("is_planetary")
    return result


def enrich_buyers(buyers, cache):
    if not isinstance(buyers, list) or len(buyers) > 50:
        raise ValueError("Unexpected buyer feed size; refusing unbounded station requests")
    enriched, errors = [], []
    for buyer in buyers:
        market_id = buyer.get("market_id")
        if isinstance(market_id, bool) or not isinstance(market_id, int) or not 0 < market_id < 10**20:
            enriched.append(dict(buyer))
            errors.append({"station": buyer.get("station"), "error": "Invalid/missing market ID"})
            continue
        url = f"https://spansh.co.uk/api/station/{market_id}"
        try:
            enriched.append(merge_station(buyer, cache.get(url), url))
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            enriched.append(dict(buyer))
            errors.append({"station": buyer.get("station"), "url": url, "error": str(exc)})
        # Bounded sequential lookup; pause only after a real network attempt.
        if cache.events and cache.events[-1].get("url") == url and cache.events[-1]["cache"] == "network":
            time.sleep(0.3)
    return enriched, errors


def rank(origin, sites, buyers, *, min_price=250000, cargo=256, demand_multiplier=4,
         max_age_hours=24, pad="L", max_total_ly=None, max_approach_ls=None, now=None,
         priority="total", max_loaded_ly=None):
    now = time.time() if now is None else now
    if (cargo is not None and cargo <= 0) or demand_multiplier < 1 or min_price < 0 or max_age_hours <= 0:
        raise ValueError("Invalid planner constraints")
    if pad not in ("S", "M", "L"):
        raise ValueError("Unknown required pad")
    if not isinstance(buyers, list):
        raise ValueError("Buyer feed schema changed")
    rejected, accepted = {}, []
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for b in buyers:
        try:
            for field in ("price", "demand", "timestamp"):
                if isinstance(b[field], bool) or not isinstance(b[field], (int, float)) or not math.isfinite(b[field]) or b[field] < 0:
                    raise ValueError("Invalid market field")
            coords(b["coords"])
            if not b.get("system") or not b.get("station"):
                raise ValueError("Missing buyer identity")
            age = now - b["timestamp"]
            if age < -300:
                reject("future_timestamp")
            elif age > max_age_hours * 3600:
                reject("stale_market")
            elif b["price"] < min_price:
                reject("below_price_floor")
            elif b["demand"] <= 0:
                reject("no_reported_demand")
            elif cargo is not None and b["demand"] < cargo * demand_multiplier:
                reject("insufficient_demand_margin")
            elif b.get("pad_conflict"):
                reject("pad_conflict")
            elif b.get("pad") not in ("S", "M", "L"):
                reject("unknown_pad")
            elif "SML".index(b["pad"]) < "SML".index(pad):
                reject("pad_too_small")
            elif max_approach_ls is not None and b.get("station_approach_ls") is None:
                reject("unknown_approach_distance")
            elif max_approach_ls is not None and b["station_approach_ls"] > max_approach_ls:
                reject("approach_too_far")
            else:
                accepted.append((b, max(0, age)))
        except (KeyError, ValueError, TypeError):
            reject("malformed_buyer")
    pairs = []
    for site in sites:
        outbound = distance(origin["coords"], site["coords"])
        for b, age in accepted:
            loaded = distance(site["coords"], b["coords"])
            if max_loaded_ly is not None and loaded > max_loaded_ly:
                continue
            total = outbound + loaded
            if max_total_ly is not None and total > max_total_ly:
                continue
            pairs.append({"mine": site, "buyer_system": b["system"], "station": b["station"],
                          "market_id": b.get("market_id"), "pad": b["pad"],
                          "price_per_tonne": b["price"], "reported_demand": b["demand"],
                          "market_timestamp": b["timestamp"], "market_age_hours": round(age/3600, 2),
                          "outbound_ly": outbound, "loaded_ly": loaded, "total_ly": total,
                          "gross_at_reported_price": cargo*b["price"] if cargo is not None else None,
                          "station_approach_ls": b.get("station_approach_ls"),
                          "station_metadata": b.get("station_metadata"),
                          "pad_source": b.get("pad_source", b.get("market_source", SOURCES["buyers"])),
                          "is_planetary": b.get("is_planetary")})
    if priority not in ("total", "loaded"):
        raise ValueError("Unknown route priority")
    pairs.sort(key=lambda p: (p["loaded_ly"] if priority == "loaded" else p["total_ly"], p["total_ly"], p["station_approach_ls"] if p["station_approach_ls"] is not None else math.inf,
                              -p["price_per_tonne"], p["market_age_hours"]))
    # One alternative per mining system/buyer station; retain other provenance in sites input.
    unique, seen = [], set()
    for pair in pairs:
        key = (pair["mine"]["system"].casefold(), pair["buyer_system"].casefold(), pair["station"].casefold())
        if key not in seen:
            seen.add(key)
            unique.append(pair)
    return {"origin": origin, "qualifying_buyers": len(accepted), "rejected_buyers": rejected,
            "evaluated_pairs": len(pairs), "recommendations": unique[:3]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commodity", type=mineral_name, default="platinum")
    parser.add_argument("--site-mode", choices=["auto", "overlaps", "high-yield", "hotspots", "rings"], default="auto")
    parser.add_argument("--method", choices=["laser", "core"])
    parser.add_argument("--min-price", type=int, help="Default: 250000 for platinum, no floor for other minerals")
    parser.add_argument("--cargo", type=int, default=256)
    parser.add_argument("--demand-multiplier", type=float, default=4)
    parser.add_argument("--max-age-hours", type=float, default=24)
    parser.add_argument("--pad", choices=["S", "M", "L"], default="L")
    parser.add_argument("--max-total-ly", type=float)
    parser.add_argument("--max-approach-ls", type=float, help="Optional hard station-arrival-distance limit")
    parser.add_argument("--journal-dir", type=Path, default=Path.home()/"Saved Games/Frontier Developments/Elite Dangerous")
    args = parser.parse_args()
    try:
        mode, method = location_policy(args.commodity, args.site_mode, args.method)
    except ValueError as exc:
        parser.error(str(exc))
    if args.min_price is None:
        args.min_price = 250000 if args.commodity == "platinum" else 0
    # Validate CLI before making network requests.
    if args.cargo <= 0 or args.min_price < 0 or args.demand_multiplier < 1 or args.max_age_hours <= 0 or (args.max_total_ly is not None and args.max_total_ly <= 0):
        parser.error("Quantities, age and distance must be positive; demand multiplier >= 1")
    if args.max_approach_ls is not None and (not math.isfinite(args.max_approach_ls) or args.max_approach_ls < 0):
        parser.error("Approach limit must be finite and nonnegative")
    location = latest_location(args.journal_dir)
    cache = FeedCache(ROOT/"cache/feeds.sqlite")
    buyer_url = f"https://edtools.cc/miner?a=p&cid={MINERALS[args.commodity]['id']}"
    raw_buyers = cache.get(buyer_url)
    buyers, enrichment_errors = enrich_buyers(raw_buyers, cache)
    for buyer in buyers:
        buyer["market_source"] = buyer_url
    origins = [{"name": "Sol", "coords": (0, 0, 0)}, location]
    sites = load_sites(cache, args.commodity, mode, method, origins[0])
    musca = next((s for s in sites if s["system"] == "Musca Dark Region IR-W d1-88"), None) if args.commodity == "platinum" else None
    if musca:
        origins.append({"name": musca["system"], "coords": musca["coords"]})
    constraints = {k: v for k, v in vars(args).items() if k not in ("journal_dir", "commodity", "site_mode", "method")}
    runs = []
    for origin in origins:
        origin_sites = sites if mode in ("overlaps", "high-yield") else load_sites(cache, args.commodity, mode, method, origin)
        runs.append(rank(origin, origin_sites, buyers, **constraints))
    report = {"generated_at": time.time(), "commodity": args.commodity, "site_mode": mode, "method": method,
              "constraints": constraints, "feed_requests": cache.events,
              "enrichment": {"matched": sum("station_metadata" in b for b in buyers),
                             "approach_known": sum(b.get("station_approach_ls") is not None for b in buyers),
                             "pads_resolved": sum(b.get("pad") not in ("S", "M", "L") and e.get("pad") in ("S", "M", "L") for b, e in zip(raw_buyers, buyers)),
                             "errors": enrichment_errors},
              "enriched_buyers": buyers,
              "source_counts": {"sites_from_sol": len(sites), "buyers": len(raw_buyers)},
              "limitations": ["Best among retrieved sites and buyer shortlist, not all markets.",
                              "Ordinary hotspots are not overlaps. Osmium rings are suitable candidates, not confirmed deposits or yields.",
                              "Body searches are limited to 25 nearest returned bodies per origin, not exhaustive.",
                              "Straight-line LY only; not routed jump counts or travel time.",
                              "Station approach distance is a separate field/optional hard limit, not converted into travel time. Planetary/carrier preferences not filtered.",
                              "Unknown pad codes excluded. No inferred pad size from station type.",
                              "Demand multiplier is a conservative configurable policy, not a guaranteed bulk-price formula.",
                              "Market prices/demand are reported observations, not guaranteed on arrival.",
                              "Mining comments retain source evidence; percentages are not mining-rate forecasts."],
              "runs": runs}
    out = ROOT/"outputs"
    out.mkdir(exist_ok=True)
    digest = hashlib.sha256(json.dumps(report).encode()).hexdigest()[:8]
    path = out/f"report-{args.commodity.replace(' ', '-')}-{time.strftime('%Y%m%d-%H%M%S')}-{digest}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", path)
    print("Mineral:", args.commodity, "|", method, "|", mode)
    print("Requests:", {kind: sum(e["cache"] == kind for e in cache.events) for kind in ("hit", "network")})
    print("Source counts:", report["source_counts"])
    print("Enrichment:", report["enrichment"])
    for run in report["runs"]:
        print(f"\nFROM {run['origin']['name']} ({run['qualifying_buyers']} qualifying buyers)")
        print("Excluded:", run["rejected_buyers"])
        for p in run["recommendations"]:
            print(f"  {p['mine']['system']} / {p['mine']['ring_notes']} -> {p['buyer_system']} / {p['station']}: "
                  f"{p['price_per_tonne']:,}/t, demand {p['reported_demand']:,}, age {p['market_age_hours']}h, "
                  f"{p['outbound_ly']:.2f} + {p['loaded_ly']:.2f} = {p['total_ly']:.2f} LY; "
                  f"station approach {p['station_approach_ls']} LS")
        if not run["recommendations"]:
            print("  No qualifying pairs; constraints were not relaxed.")


if __name__ == "__main__":
    main()
