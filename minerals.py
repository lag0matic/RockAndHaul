"""Miner's Tool commodity identifiers and explicit location policies."""
MINERALS = {
    "platinum": {"id": 46, "overlap": "Platinum", "method": "laser", "signal": "Platinum"},
    "painite": {"id": 83, "overlap": "Painite", "method": "laser", "signal": "Painite"},
    "low temperature diamonds": {"id": 276, "overlap": "LTD", "method": "laser", "signal": "Low Temperature Diamonds"},
    "osmium": {"id": 97, "method": "laser", "signal": None},
    "rhodplumsite": {"id": 343, "method": "core", "signal": "Rhodplumsite"},
    "serendibite": {"id": 344, "method": "core", "signal": "Serendibite"},
    "monazite": {"id": 345, "method": "core", "signal": "Monazite"},
    "musgravite": {"id": 346, "method": "core", "signal": "Musgravite"},
    "benitoite": {"id": 347, "method": "core", "signal": "Benitoite"},
    "grandidierite": {"id": 348, "method": "core", "signal": "Grandidierite"},
    "alexandrite": {"id": 349, "method": "core", "signal": "Alexandrite"},
    "void opals": {"id": 350, "method": "core", "signal": "Void Opal"},
}
ALIASES = {"ltd": "low temperature diamonds", "ltds": "low temperature diamonds",
           "lowtemperaturediamond": "low temperature diamonds", "void opal": "void opals",
           "opal": "void opals", "opals": "void opals"}


def mineral_name(value):
    name = " ".join(value.lower().replace("-", " ").split())
    name = ALIASES.get(name, name)
    if name not in MINERALS:
        raise ValueError("Unsupported mineral: " + value)
    return name


def location_policy(name, mode="auto", method=None):
    spec = MINERALS[name]
    method = method or spec["method"]
    allowed = {spec["method"]}
    if name in ("painite", "low temperature diamonds"):
        allowed.add("core")
    if method not in allowed:
        raise ValueError(f"{name} does not support the requested {method} path in this prototype")
    if mode == "auto":
        mode = "overlaps" if spec.get("overlap") else ("rings" if name == "osmium" else "hotspots")
    if mode == "overlaps" and not spec.get("overlap"):
        raise ValueError(f"No curated overlap feed for {name}; not substituting ordinary hotspots")
    if mode == "high-yield" and name != "platinum":
        raise ValueError("High-yield list is platinum-only")
    if mode == "rings" and name != "osmium":
        raise ValueError("Suitable-ring-only mode is implemented for osmium")
    if mode == "hotspots" and name == "osmium":
        raise ValueError("Osmium has no dedicated hotspots")
    return mode, method


def body_request(name, origin, mode):
    filters = {"reserve_level": {"value": ["Pristine"]}}
    if mode == "rings":
        # Conservative metallic-only subset, not a claim of guaranteed osmium yield.
        filters["rings"] = [{"type": "Metallic"}]
    else:
        filters["ring_signals"] = [{"name": MINERALS[name]["signal"], "value": [1, 99], "comparison": "<=>"}]
    return {"filters": filters, "sort": [{"distance": {"direction": "asc"}}],
            "size": 25, "page": 0,
            "reference_coords": dict(zip(("x", "y", "z"), origin["coords"]))}


def body_sites(payload, name, mode, method):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Body response schema changed")
    sites = []
    for body in payload["results"]:
        if body.get("reserve_level") != "Pristine":
            continue
        for ring in body.get("rings") or []:
            if not ring.get("name"):
                continue
            if mode == "rings":
                if ring.get("type") != "Metallic":
                    continue
                category = "suitable_ring_not_confirmed_deposit"
            else:
                signals = ring.get("signals") or []
                if not any(s.get("name") == MINERALS[name]["signal"] and
                           isinstance(s.get("count"), int) and not isinstance(s["count"], bool) and s["count"] > 0 for s in signals):
                    continue
                # Platinum/painite laser mining needs metallic, not merely a same-name core hotspot.
                if method == "laser" and name in ("platinum", "painite") and ring.get("type") != "Metallic":
                    continue
                if name in ("low temperature diamonds", "void opals") and ring.get("type") != "Icy":
                    continue
                category = "ordinary_hotspot_not_overlap"
            sites.append({"system": body["system_name"],
                          "coords": [body["system_x"], body["system_y"], body["system_z"]],
                          "ring_notes": ring["name"], "category": category,
                          "ring_type": ring.get("type"), "method": method,
                          "mine_approach_ls": body.get("distance_to_arrival"),
                          "source": "https://spansh.co.uk/api/bodies/search"})
    return sites
