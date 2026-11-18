#!/usr/bin/env python3
"""Mix Bench for laptop: a tiny local web server. Standard library only.

Run it (double-click start.bat / start.command, or `python3 server.py`) and it opens
http://localhost:8765 in your browser. Your data is saved in data.json next to this file.
"""
import html
import json
import os
import re
import shutil
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DATA = HERE / "data.json"
BACKUPS = HERE / "backups"
PAGE = HERE / "index.html"
FIRST_PORT = 8765
MAX_BYTES = 8 * 1024 * 1024
KEEP_BACKUPS = 14

write_lock = threading.Lock()

# ---- two-way sync with the phone through a folder kept in step by Google Drive for desktop ----
SYNC_DIR_NAME = "MixBench Sync"
SYNC_CONFIG = HERE / "sync-config.json"      # only holds the folder path on this computer
LAPTOP_FILE = "mixbench-laptop.json"
PHONE_FILE = re.compile(r"^mixbench-phone.*\.json$", re.I)
KEEP_SYNC_BACKUPS = 20


def sync_candidates():
    home = Path.home()
    found = []
    if os.name == "nt":
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            found.append(Path("%s:/My Drive" % letter) / SYNC_DIR_NAME)
    else:
        cloud = home / "Library" / "CloudStorage"
        if cloud.is_dir():
            for d in sorted(cloud.glob("GoogleDrive-*")):
                found.append(d / "My Drive" / SYNC_DIR_NAME)
    found += [home / "Google Drive" / "My Drive" / SYNC_DIR_NAME, home / "My Drive" / SYNC_DIR_NAME,
              home / "Google Drive" / SYNC_DIR_NAME]
    return found


def sync_status():
    try:
        folder = json.loads(SYNC_CONFIG.read_text(encoding="utf-8")).get("folder") or ""
    except (FileNotFoundError, ValueError):
        folder = ""
    if folder:
        return {"folder": folder, "found": Path(folder).is_dir(), "auto": False}
    for p in sync_candidates():
        try:
            if p.is_dir():
                return {"folder": str(p), "found": True, "auto": True}
        except OSError:
            continue
    return {"folder": "", "found": False, "auto": False}


def sync_folder():
    st = sync_status()
    return Path(st["folder"]) if st["found"] else None


def sync_pull():
    folder = sync_folder()
    if folder is None:
        return {"ok": False, "error": "The sync folder can't be found. Check it at the bottom of Shop."}
    files = []
    for f in folder.iterdir():
        if f.is_file() and PHONE_FILE.match(f.name):
            files.append(f)
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for f in files[:6]:
        try:
            out.append({"name": f.name, "mtime": f.stat().st_mtime, "data": json.loads(f.read_text(encoding="utf-8"))})
        except (OSError, ValueError):
            continue        # still downloading or a partial copy; picked up next time
    return {"ok": True, "files": out}


def sync_push(obj):
    folder = sync_folder()
    if folder is None:
        return {"ok": False, "error": "The sync folder can't be found. Check it at the bottom of Shop."}
    target = folder / LAPTOP_FILE
    with write_lock:
        if target.exists():
            keep = BACKUPS / "sync"
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, keep / ("laptop-%s.json" % datetime.now().strftime("%Y-%m-%d-%H%M%S")))
            for old in sorted(keep.glob("laptop-*.json"))[:-KEEP_SYNC_BACKUPS]:
                old.unlink(missing_ok=True)
        tmp = folder / (LAPTOP_FILE + ".part")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    return {"ok": True}


def read_data():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def snapshot(force=False):
    """Keep one copy of data.json per day (last 14 days) before it is overwritten.
    With force=True (used before an import or reset) always take a timestamped copy."""
    if not DATA.exists():
        return
    BACKUPS.mkdir(exist_ok=True)
    if force:
        shutil.copy2(DATA, BACKUPS / ("before-change-%s.json" % datetime.now().strftime("%Y-%m-%d-%H%M%S")))
    day = BACKUPS / ("data-%s.json" % datetime.now().strftime("%Y-%m-%d"))
    if not day.exists():
        shutil.copy2(DATA, day)
    for f in sorted(BACKUPS.glob("data-*.json"))[:-KEEP_BACKUPS]:
        f.unlink(missing_ok=True)
    for f in sorted(BACKUPS.glob("before-change-*.json"))[:-KEEP_BACKUPS]:
        f.unlink(missing_ok=True)


def write_data(obj, force=False):
    with write_lock:
        snapshot(force)
        tmp = DATA.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, DATA)


class Handler(BaseHTTPRequestHandler):
    server_version = "MixBench"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = origin.split("://", 1)[-1].split(":")[0]
            return o in ("localhost", "127.0.0.1", "[::1]")
        return True

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "forbidden"})
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/data":
            return self._json(200, read_data())
        if path == "/api/info":
            return self._json(200, {"file": str(DATA), "exists": DATA.exists()})
        if path == "/api/sync/status":
            return self._json(200, sync_status())
        if path == "/api/sync/pull":
            return self._json(200, sync_pull())
        if path == "/api/update/check":
            return self._json(200, update_check())
        if path == "/api/vapable":
            q = parse_qs(urlsplit(self.path).query)
            return self._json(200, vapable_lookup((q.get("url") or [""])[0]))
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"error": "forbidden"})
        path = self.path.split("?", 1)[0]
        if path not in ("/api/data", "/api/sync/push", "/api/sync/config", "/api/vapable/prices", "/api/update/install"):
            return self._json(404, {"error": "not found"})
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json(415, {"error": "json only"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BYTES:
                return self._json(413, {"error": "bad size"})
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
            if path == "/api/update/install":
                return self._json(200, update_install())
            if path == "/api/vapable/prices":
                return self._json(200, vapable_prices((obj or {}).get("items")))
            if path == "/api/sync/config":
                folder = str((obj or {}).get("folder") or "").strip().strip('"')
                SYNC_CONFIG.write_text(json.dumps({"folder": folder}, indent=2), encoding="utf-8")
                return self._json(200, sync_status())
            if path == "/api/sync/push":
                if not isinstance(obj, dict) or not isinstance((obj.get("state") or {}).get("plan"), dict):
                    return self._json(400, {"ok": False, "error": "not a Mix Bench data file"})
                return self._json(200, sync_push(obj))
            if not isinstance(obj, dict) or not isinstance(obj.get("plan"), dict):
                return self._json(400, {"error": "not a Mix Bench data file"})
            write_data(obj, force="keep=1" in self.path)
        except (ValueError, OSError) as e:
            return self._json(400, {"error": str(e)})
        self._json(200, {"ok": True})


# ---- Shop: read the product and option IDs from a Vapable product page, for the add-to-cart links ----
VAP_PAGE = re.compile(r"^https://(www\.)?vapable\.(com|co\.uk)/product/[a-z0-9\-]+/?$", re.I)


def vapable_lookup(url):
    url = (url or "").strip()
    parts = urlsplit(url)
    page = "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)
    if not VAP_PAGE.match(page):
        return {"ok": False, "error": "That isn't a Vapable product page link."}
    page = re.sub(r"vapable\.co\.uk", "vapable.com", page, flags=re.I)
    try:
        req = Request(page, headers={"User-Agent": "Mozilla/5.0 (Mix Bench)"})
        with urlopen(req, timeout=20) as r:
            text = r.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except OSError as e:
        return {"ok": False, "error": "Couldn't open that page (%s)." % e}
    m = re.search(r'name="add-to-cart" value="(\d+)"', text) or re.search(r"postid-(\d+)", text)
    if not m:
        return {"ok": False, "error": "Couldn't find a product on that page."}
    t = re.search(r"<h1[^>]*product_title[^>]*>([^<]+)", text)
    variations = []
    v = re.search(r'data-product_variations="([^"]*)"', text)
    if v and v.group(1) not in ("", "false"):
        try:
            for x in json.loads(html.unescape(v.group(1))):
                variations.append({"vid": x.get("variation_id"), "attr": x.get("attributes") or {}, "price": x.get("display_price"),
                                   "in_stock": bool(x.get("is_in_stock"))})
        except ValueError:
            pass
    want = {k: vals[0] for k, vals in parse_qs(parts.query).items() if k.startswith("attribute_")}
    # a product with no options says whether it is in stock on the page itself
    in_stock = not re.search(r'class="[^"]*\b(outofstock|out-of-stock)\b', text[:200000])
    return {"ok": True, "pid": int(m.group(1)), "title": html.unescape(t.group(1)).strip() if t else "", "in_stock": in_stock,
            "vars": variations, "want": want, "url": page}


# ---- Shop: live Vapable prices for "Update prices". Read-only: no login, no basket, no account. ----
STORE_API = "https://vapable.com/wp-json/wc/store/v1/products/%d"


def _get(url, limit=4 * 1024 * 1024):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Mix Bench)", "Accept": "text/html,application/json"})
    with urlopen(req, timeout=25) as r:
        return r.read(limit).decode("utf-8", "replace")


def _page_variations(text):
    v = re.search(r'data-product_variations="([^"]*)"', text)
    if not v or v.group(1) in ("", "false"):
        return []
    try:
        return json.loads(html.unescape(v.group(1)))
    except ValueError:
        return []


def _tiers(table_html):
    """the "Discount per Quantity" table: [[from quantity, price each], ...]"""
    rows = re.findall(r'data-qtyMin="(\d+)"[^>]*?data-unformatted_price="([\d.]+)"', table_html or "")
    return sorted([[int(q), round(float(p), 2)] for q, p in rows])


def _store_price(variation_id):
    """price, sale and stock for one option from the WooCommerce Store API (None if it didn't answer)"""
    try:
        d = json.loads(_get(STORE_API % variation_id, 1024 * 1024))
        p = d.get("prices") or {}
        unit = 10 ** int(p.get("currency_minor_unit", 2))
        return {"ok": True, "source": "store", "price": int(p["price"]) / unit, "regular": int(p.get("regular_price") or p["price"]) / unit,
                "on_sale": bool(d.get("on_sale")), "in_stock": bool(d.get("is_in_stock"))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def vapable_price(item):
    pid, vid = int(item.get("pid") or 0), int(item.get("vid") or 0)
    url = str(item.get("url") or "")
    out = {"id": item.get("id"), "ok": False}
    if pid <= 0:
        out["error"] = "no product ID"
        return out
    # 1) WooCommerce Store API: price, sale and stock for the exact option
    try:
        d = json.loads(_get(STORE_API % (vid or pid), 1024 * 1024))
        p = d.get("prices") or {}
        unit = 10 ** int(p.get("currency_minor_unit", 2))
        out.update(ok=True, source="store", price=int(p["price"]) / unit, regular=int(p.get("regular_price") or p["price"]) / unit,
                   on_sale=bool(d.get("on_sale")), in_stock=bool(d.get("is_in_stock")))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # 2) the product page: quantity tiers and pack prices (and everything, if the Store API didn't answer)
    page = url.split("?", 1)[0]
    if VAP_PAGE.match(page):
        try:
            text = _get(re.sub(r"vapable\.co\.uk", "vapable.com", page, flags=re.I))
            variations = _page_variations(text)
            out["_variations"] = variations
            mine = [x for x in variations if int(x.get("variation_id") or 0) == vid]
            if mine:
                x = mine[0]
                out["tiers"] = _tiers(x.get("table_price"))
                if not out["ok"]:
                    out.update(ok=True, source="page", price=float(x.get("display_price")), regular=float(x.get("display_regular_price") or x.get("display_price")),
                               in_stock=bool(x.get("is_in_stock")))
                    out["on_sale"] = out["regular"] > out["price"]
            elif not variations:
                t = re.search(r'<table id="ywdpd-quantity-table".*?</table>', text, re.S)
                out["tiers"] = _tiers(t.group(0) if t else "")
            out["options"] = [{"vid": x.get("variation_id"), "attr": x.get("attributes") or {}, "price": x.get("display_price"),
                               "in_stock": bool(x.get("is_in_stock"))} for x in variations]
        except (OSError, ValueError):
            if not out["ok"]:
                out["error"] = "couldn't read the product page"
    elif not out["ok"]:
        out["error"] = "no product page link"
    # flavours come in two sizes (10ml and 100ml): read both, each with its own price, tiers and stock
    sizes = item.get("sizes") or {}
    if isinstance(sizes, dict) and sizes:
        out["sizes"] = {}
        for sz, v in list(sizes.items())[:4]:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            rec = _store_price(v) or {"ok": False}
            for x in out.get("_variations", []):
                if int(x.get("variation_id") or 0) == v:
                    rec["tiers"] = _tiers(x.get("table_price"))
                    if not rec["ok"]:
                        rec.update(ok=True, source="page", price=float(x.get("display_price")), regular=float(x.get("display_regular_price") or x.get("display_price")),
                                   in_stock=bool(x.get("is_in_stock")))
                        rec["on_sale"] = rec["regular"] > rec["price"]
            out["sizes"][str(sz)] = rec
        out["ok"] = any(r.get("ok") for r in out["sizes"].values()) or out["ok"]
    out.pop("_variations", None)
    return out


def vapable_prices(items):
    items = [i for i in (items or []) if isinstance(i, dict)][:40]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {"ok": True, "items": list(pool.map(vapable_price, items))}


# ---- updates: new versions are published next to the iPhone app; the app asks before installing ----
UPDATE_BASE = "https://keeler0121.github.io/mix-bench/laptop/"
UPDATE_FILES = ("index.html", "server.py")


def _vkey(v):
    try:
        return tuple(int(x) for x in str(v).split("-"))
    except ValueError:
        return ()


def local_version():
    try:
        m = re.search(r'<meta name="mb-version" content="([^"]+)"', PAGE.read_text(encoding="utf-8", errors="replace")[:4000])
        return m.group(1) if m else ""
    except OSError:
        return ""


def _get_bytes(url, limit=8 * 1024 * 1024):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Mix Bench)", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=25) as r:
        return r.read(limit)


def _manifest():
    m = json.loads(_get_bytes(UPDATE_BASE + "version.json?t=" + str(int(datetime.now().timestamp()))).decode("utf-8"))
    if not isinstance(m, dict) or not _vkey(m.get("version")) or not isinstance(m.get("files"), dict):
        raise ValueError("the update list looks wrong")
    return m


def update_check():
    cur = local_version()
    try:
        m = _manifest()
    except Exception as e:  # offline, or GitHub not reachable
        return {"current": cur, "latest": "", "available": False, "error": str(e)}
    return {"current": cur, "latest": m["version"], "available": _vkey(m["version"]) > _vkey(cur)}


def update_install():
    import hashlib
    cur = local_version()
    try:
        m = _manifest()
        if not (_vkey(m["version"]) > _vkey(cur)):
            return {"ok": False, "error": "you already have the newest version"}
        got = {}
        for name in UPDATE_FILES:
            want = m["files"].get(name)
            if not want:
                continue
            data = _get_bytes(UPDATE_BASE + name + "?v=" + m["version"])
            if hashlib.sha256(data).hexdigest() != want:
                return {"ok": False, "error": name + " didn't download properly, try again in a few minutes"}
            got[name] = data
        page = got.get("index.html")
        if not page or b'name="mb-version"' not in page or len(page) < 50000:
            return {"ok": False, "error": "the new page looks incomplete"}
        if "server.py" in got and b"def update_install" not in got["server.py"]:
            return {"ok": False, "error": "the new server file looks incomplete"}
        # keep the current files, so an update can always be undone by copying them back
        keep = BACKUPS / ("app-" + (cur or "old"))
        keep.mkdir(parents=True, exist_ok=True)
        restart = False
        for name, data in got.items():
            dest = HERE / name
            if dest.exists():
                shutil.copy2(dest, keep / name)
                if name == "server.py" and dest.read_bytes() == data:
                    continue
            tmp = dest.with_name(dest.name + ".new")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            if name == "server.py":
                restart = True
        return {"ok": True, "version": m["version"], "restart": restart}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    open_browser = "--no-browser" not in sys.argv
    server = None
    for port in range(FIRST_PORT, FIRST_PORT + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("Could not find a free port. Close other programs and try again.")
        sys.exit(1)
    url = "http://localhost:%d" % server.server_address[1]
    print("Mix Bench is running at " + url)
    print("Your data is saved in: " + str(DATA))
    print("Leave this window open while you use it. Press Ctrl+C (or close the window) to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
