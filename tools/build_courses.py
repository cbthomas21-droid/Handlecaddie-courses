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


class OutOfTime(Exception):
    pass


START = time.time()
BUDGET_S = float(os.environ.get("BUDGET_MIN", "150")) * 60      # stop starting new work after this long; the next run continues
REQ_TIMEOUT = float(os.environ.get("REQ_TIMEOUT", "75"))        # seconds to wait for ONE server
COURSE_BUDGET_S = float(os.environ.get("COURSE_BUDGET", "120")) # most time one course may use before we move on


def time_left():
    return BUDGET_S - (time.time() - START)


COOLDOWN_S = 600        # a server that just timed out or errored twice in a row is skipped for this long
_bad = {}               # endpoint -> [consecutive failures, skip-until time]


def _usable(ep):
    return time.time() >= _bad.get(ep, [0, 0])[1]


def _note(ep, good):
    if good:
        _bad[ep] = [0, 0]
    else:
        n = _bad.get(ep, [0, 0])[0] + 1
        _bad[ep] = [n, time.time() + COOLDOWN_S if n >= 2 else 0]


def overpass(query, deadline=None, timeout=None, tries=2, endpoints=None):
    """POST to the Overpass servers in turn. Gives up when [deadline] (epoch seconds) or the overall budget runs out."""
    timeout = timeout or REQ_TIMEOUT
    last = None
    for attempt in range(tries):
        for ep in (endpoints or ENDPOINTS):
            if not _usable(ep):
                continue
            if time_left() <= 5:
                raise OutOfTime("overall time budget used up")
            if deadline is not None and time.time() >= deadline:
                raise RuntimeError(f"gave up on this course (time limit); last error: {last}")
            try:
                req = urllib.request.Request(ep, data=("data=" + urllib.parse.quote(query)).encode(),
                                             headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    text = r.read().decode("utf-8")
                if "runtime error" in text[-500:]:
                    raise RuntimeError("server gave up (query timed out)")
                res = json.loads(text)
                _note(ep, True)
                res["_server"] = ep.split("/")[2]
                return res
            except OutOfTime:
                raise
            except Exception as e:                       # busy, timeout, bad JSON ...
                last = e
                _note(ep, False)
                print(f"      {ep.split('/')[2]}: {type(e).__name__}: {e}", flush=True)
        time.sleep(min(10 * (attempt + 1), max(0, time_left() - 5)))
        for v in _bad.values():          # a pause is over: give every server another chance on the second round
            v[1] = 0
    raise RuntimeError(f"all servers failed: {last}")


def kinds_in(data):
    """What kinds of golf features a result holds, e.g. {'green': 18, 'bunker': 40} (holes excluded)."""
    k = {}
    for e in data.get("elements", []):
        g = (e.get("tags") or {}).get("golf")
        if g and g != "hole" and e.get("type") == "way":
            k[g] = k.get(g, 0) + 1
    return k


def count_holes(data):
    return sum(1 for e in data.get("elements", []) if e.get("type") == "way" and e.get("tags", {}).get("golf") == "hole")


# Rough bounding boxes (south, west, north, east). A box is much cheaper for the server than "everything inside the state
# boundary", and it also catches courses just over the border. Add a state here, or set BBOX="s,w,n,e" in the workflow.
BBOXES = {
    "US-UT": "36.99,-114.06,42.01,-109.03",
    "US-CO": "36.99,-109.06,41.01,-102.04",
    "US-AZ": "31.33,-114.82,37.01,-109.04",
    "US-NV": "34.99,-120.01,42.01,-114.03",
    "US-ID": "41.99,-117.25,49.01,-111.04",
    "US-WY": "40.99,-111.06,45.01,-104.05",
    "US-NM": "31.33,-109.06,37.01,-103.00",
}


def index_query():
    box = os.environ.get("BBOX") or BBOXES.get(REGION)
    if box:
        return f'[out:json][timeout:90];nwr["leisure"="golf_course"]({box});out center tags;'
    return (f'[out:json][timeout:180];area["ISO3166-2"="{REGION}"]->.s;'
            f'nwr["leisure"="golf_course"](area.s);out center tags;')


def holes_in_course(c):
    sel = {"way": f"way({c['id']})", "relation": f"rel({c['id']})"}.get(c["type"])
    if not sel:
        return None
    return (f'[out:json][timeout:60];{sel};map_to_area->.a;'
            f'(way["golf"~"{KINDS}"](area.a);node["golf"="pin"](area.a););out geom;')


def holes_around(c, r=3000):
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


def load_status(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


UNMAPPED_RECHECK_DAYS = 7       # a course found NOT mapped is only re-checked this often (it costs two queries every time)


def main():
    os.makedirs(os.path.join(OUT, "c"), exist_ok=True)
    status_path = os.path.join(OUT, "status.json")
    status = load_status(status_path)
    if status.get("_v") != 3:                    # older runs marked busy-server mistakes as "not mapped": look at those again
        status = {k: v for k, v in status.items() if isinstance(v, dict) and v.get("result") != "unmapped"}
        status["_v"] = 3
    now = time.time()
    print(f"budget {BUDGET_S/60:.0f} min, {REQ_TIMEOUT:.0f} s per request, {COURSE_BUDGET_S:.0f} s per course", flush=True)

    print("asking for the list of golf courses...", flush=True)
    idx = trim_index(overpass(index_query(), timeout=150))
    if not idx:
        sys.exit("no courses found - refusing to overwrite the index")
    print(f"{len(idx)} named courses in {REGION}", flush=True)

    def key(e): return f'{e["type"]}_{e["id"]}'
    def has_file(e): return os.path.exists(os.path.join(OUT, "c", key(e) + ".json"))
    # courses with no file yet come first, so an interrupted run still gets the most useful ones done; refreshes come last
    todo = sorted(idx, key=lambda e: (has_file(e), (status.get(key(e)) or {}).get("t", 0)))

    ok = kept = failed = unmapped = skipped = 0
    stopped_early = False
    try:
        for n, e in enumerate(todo, 1):
            k = key(e)
            st = status.get(k, {})
            if not isinstance(st, dict):
                st = {}
            if st.get("result") == "unmapped" and now - st.get("t", 0) < UNMAPPED_RECHECK_DAYS * 86400 and not has_file(e):
                skipped += 1
                continue
            if time_left() < 60:
                stopped_early = True
                print(f"time budget used up after {n-1} of {len(todo)} courses; the next run will continue", flush=True)
                break
            c = {"type": e["type"], "id": e["id"], "lat": e["center"]["lat"], "lon": e["center"]["lon"]}
            path = os.path.join(OUT, "c", k + ".json")
            deadline = time.time() + COURSE_BUDGET_S
            data, err, reached = None, None, False
            seen = []                                           # what each look said: (query, server, holes)
            mapped_kinds = {}                                   # the richest set of other features seen (greens, bunkers ...)
            for qi, q in enumerate((holes_in_course(c), holes_around(c))):
                if not q:
                    continue
                try:
                    time.sleep(PAUSE)
                    d = overpass(q, deadline=deadline)
                    n_h = count_holes(d)
                    kd = kinds_in(d)
                    if sum(kd.values()) > sum(mapped_kinds.values()):
                        mapped_kinds = kd
                    seen.append(("in-course" if qi == 0 else "nearby", d.get("_server", "?"), n_h))
                    if n_h < 3:
                        # A busy server can answer "nothing" for a course that has holes. Ask a DIFFERENT server before believing it.
                        others = [x for x in ENDPOINTS if x.split("/")[2] != d.get("_server")]
                        try:
                            d2 = overpass(q, deadline=deadline, endpoints=others, tries=1)
                            seen.append(("in-course" if qi == 0 else "nearby", d2.get("_server", "?"), count_holes(d2)))
                            if count_holes(d2) > n_h:
                                d, n_h = d2, count_holes(d2)
                        except OutOfTime:
                            raise
                        except Exception:
                            seen.append(("second opinion", "none answered", -1))
                    reached = True
                    if n_h >= 3:
                        data = d
                        break
                    err = f"only {n_h} holes"
                except OutOfTime:
                    raise
                except Exception as ex:
                    err = str(ex)
            name = e["tags"]["name"]
            if data:
                with open(path, "w") as f:
                    json.dump(data, f, separators=(",", ":"))
                status[k] = {"t": now, "result": "ok"}
                ok += 1
                print(f"[{n}/{len(todo)}] OK     {name} ({count_holes(data)} holes)", flush=True)
            elif os.path.exists(path):
                status[k] = {"t": now, "result": "kept"}
                kept += 1
                print(f"[{n}/{len(todo)}] KEPT   {name} (old file; {err})", flush=True)
            elif reached and err and "holes" in err:
                status[k] = {"t": now, "result": "unmapped"}
                unmapped += 1
                confirmed = all(h >= 0 for _, _, h in seen) and sum(1 for q, _, _ in seen if q != "second opinion") >= 2
                print(f"[{n}/{len(todo)}] UNMAPPED {name} ({err}) {'; '.join(f'{q}@{sv}={h}' for q, sv, h in seen)}"
                      f"  check: https://www.openstreetmap.org/{k.replace('_', '/')}", flush=True)
                if mapped_kinds:
                    print("            has no hole lines, but is mapped with: " + ", ".join(f"{v} {n}" for n, v in sorted(mapped_kinds.items())), flush=True)
                if not confirmed:                                # a server did not answer the second opinion: try again next run
                    status[k] = {"t": now - (UNMAPPED_RECHECK_DAYS - 1) * 86400, "result": "unmapped"}
            else:
                failed += 1
                status[k] = {"t": now, "result": "failed"}      # remembered, so next run tries courses never attempted before these
                print(f"[{n}/{len(todo)}] FAILED {name} ({err})", flush=True)
    except OutOfTime:
        stopped_early = True
        print("time budget used up; keeping what was built so far", flush=True)
    finally:
        # always leave consistent files behind (even on a stop), so the workflow can still publish what was built
        with open(status_path, "w") as f:
            json.dump(status, f, separators=(",", ":"))
        with open(os.path.join(OUT, "index.json"), "w") as f:
            json.dump({"generated": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "region": REGION, "elements": idx},
                      f, separators=(",", ":"))
    print(f"done: {ok} built, {kept} kept, {unmapped} not mapped, {skipped} skipped (recently found unmapped), {failed} failed"
          + (" - stopped early, will continue next run" if stopped_early else ""), flush=True)


if __name__ == "__main__":
    main()
