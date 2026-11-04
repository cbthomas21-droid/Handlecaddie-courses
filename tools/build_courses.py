#!/usr/bin/env python3
"""
Builds the hosted course data for the HandleCaddie app from OpenStreetMap (Overpass).

Writes into docs/ (served by GitHub Pages):
  docs/index.json        every named golf course in the region (same shape the app already parses)
  docs/c/<type>_<id>.json  the raw hole data of one course (same shape the app already parses)

A course whose download fails keeps its previous file, so a bad night never deletes data.
Region: set REGION to an ISO 3166-2 code (default US-UT = Utah).
"""
import json, os, sys, time, urllib.parse, urllib.request, datetime

REGION = os.environ.get("REGION", "US-UT")
OUT = os.environ.get("OUT", "docs")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "HandleCaddie-courses-builder/1.0 (golf push-cart companion app)"
KINDS = "^(hole|green|fairway|bunker|tee|water_hazard|lateral_water_hazard)$"
PAUSE = float(os.environ.get("PAUSE", "3"))     # polite gap between requests to the shared servers


def overpass(query, timeout=240, tries=4):
    """POST to the Overpass servers in turn; retry with growing pauses. Returns parsed JSON or raises."""
    last = None
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(ep, data=("data=" + urllib.parse.quote(query)).encode(),
                                             headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    text = r.read().decode("utf-8")
                if "runtime error" in text[-500:]:
                    raise RuntimeError("server gave up (query timed out)")
                return json.loads(text)
            except Exception as e:                       # busy, timeout, bad JSON ...
                last = e
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"all servers failed: {last}")


def count_holes(data):
    return sum(1 for e in data.get("elements", []) if e.get("type") == "way" and e.get("tags", {}).get("golf") == "hole")


def index_query():
    return (f'[out:json][timeout:180];area["ISO3166-2"="{REGION}"]->.s;'
            f'nwr["leisure"="golf_course"](area.s);out center tags;')


def holes_in_course(c):
    sel = {"way": f"way({c['id']})", "relation": f"rel({c['id']})"}.get(c["type"])
    if not sel:
        return None
    return (f'[out:json][timeout:60];{sel};map_to_area->.a;'
            f'(way["golf"~"{KINDS}"](area.a);node["golf"="pin"](area.a););out geom;')


def holes_around(c, r=2000):
    la, lo = c["lat"], c["lon"]
    return (f'[out:json][timeout:60];(way["golf"~"{KINDS}"](around:{r},{la},{lo});'
            f'node["golf"="pin"](around:{r},{la},{lo}););out geom;')


def trim_index(data):
    """Keep only named courses, with just the name tag and a centre point."""
    out = []
    for e in data.get("elements", []):
        name = (e.get("tags") or {}).get("name", "").strip()
        if not name or "id" not in e:
            continue
        if "center" in e:
            la, lo = e["center"]["lat"], e["center"]["lon"]
        elif "lat" in e:
            la, lo = e["lat"], e["lon"]
        else:
            continue
        out.append({"type": e["type"], "id": e["id"], "center": {"lat": la, "lon": lo}, "tags": {"name": name}})
    return out


def main():
    os.makedirs(os.path.join(OUT, "c"), exist_ok=True)
    idx = trim_index(overpass(index_query()))
    if not idx:
        sys.exit("no courses found - refusing to overwrite the index")
    print(f"{len(idx)} named courses in {REGION}")
    ok = kept = failed = unmapped = 0
    for n, e in enumerate(idx, 1):
        c = {"type": e["type"], "id": e["id"], "lat": e["center"]["lat"], "lon": e["center"]["lon"]}
        path = os.path.join(OUT, "c", f'{c["type"]}_{c["id"]}.json')
        data, err = None, None
        for q in (holes_in_course(c), holes_around(c)):
            if not q:
                continue
            try:
                time.sleep(PAUSE)
                d = overpass(q)
                if count_holes(d) >= 3:
                    data = d
                    break
                err = f"only {count_holes(d)} holes"
            except Exception as ex:
                err = str(ex)
        name = e["tags"]["name"]
        if data:
            with open(path, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            ok += 1
            print(f"[{n}/{len(idx)}] OK    {name} ({count_holes(data)} holes)")
        elif os.path.exists(path):
            kept += 1
            print(f"[{n}/{len(idx)}] KEPT  {name} (old file; {err})")
        else:
            unmapped += 1 if err and "holes" in err else 0
            failed += 0 if err and "holes" in err else 1
            print(f"[{n}/{len(idx)}] SKIP  {name} ({err})")
    # the index goes out last, so a crash mid-run never publishes a half-built set
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump({"generated": datetime.datetime.utcnow().isoformat() + "Z", "region": REGION, "elements": idx},
                  f, separators=(",", ":"))
    print(f"done: {ok} built, {kept} kept, {unmapped} not mapped, {failed} failed")


if __name__ == "__main__":
    main()
