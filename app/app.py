"""Kitchen display: shows the latest recipe link chosen in Home Assistant.

GET  /              display page (polls /api/current)
GET  /api/current   current link + parsed recipe
POST /api/set       {"url": "...", "mode": "recipe"|"site"}  (Authorization: Bearer <KITCHEN_SECRET>)
GET  /healthz

Home Assistant pushes to /api/set (rest_command); the app never connects to HA.
"""
import asyncio
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from recipe import extract_recipe

log = logging.getLogger("kitchen")
DATA = Path(os.environ.get("DATA_DIR", "/data"))
STATE_FILE = DATA / "state.json"
SECRET = os.environ.get("KITCHEN_SECRET", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
STATIC = Path(__file__).parent / "static"
MAX_PAGE = 6 * 1024 * 1024

state = {"id": 0, "url": None, "mode": "recipe", "show": "idle", "recipe": None,
         "error": None, "updated": None}
lock = asyncio.Lock()


def load_state():
    try:
        state.update(json.loads(STATE_FILE.read_text()))
    except FileNotFoundError:
        pass
    except Exception as e:  # corrupt file: start empty, keep the bad file for inspection
        log.error("state file unreadable (%s); starting empty", e)
        STATE_FILE.rename(STATE_FILE.with_suffix(".bad"))


def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def mode_from(value):
    v = (value or "").strip().lower()
    if v in ("site", "full website", "website", "full site"):
        return "site"
    return "recipe"


def valid_url(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc) and len(url) <= 2048


async def fetch_page(session, url):
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20),
                           max_redirects=10) as r:
        if r.status >= 400:
            raise RuntimeError(f"site returned HTTP {r.status}")
        chunks, size = [], 0
        async for chunk in r.content.iter_chunked(64 * 1024):  # read(n) may return a partial body
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_PAGE:
                break
        return b"".join(chunks).decode(r.charset or "utf-8", errors="replace"), str(r.url)


async def set_link(app, url, mode, source):
    url = url.strip()
    if not valid_url(url):
        raise ValueError("not an http(s) URL")
    recipe, error = None, None
    if mode == "recipe":
        try:
            html, final_url = await fetch_page(app["http"], url)
            recipe = extract_recipe(html, final_url)
            if not recipe:
                error = "no recipe data found on the page"
        except Exception as e:
            error = str(e) or e.__class__.__name__
    # recipe view when we have a recipe; otherwise show the site itself
    show = "recipe" if recipe else "site"
    async with lock:
        state.update({"id": state["id"] + 1, "url": url, "mode": mode, "show": show,
                      "recipe": recipe, "error": error,
                      "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        save_state()
    log.info("set from %s: id=%s mode=%s show=%s url=%s%s", source, state["id"], mode, show,
             url, f" (recipe parse failed: {error})" if error else "")
    return dict(state)


# ---------------- HTTP ----------------

async def index(request):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def current(request):
    return web.json_response(state, headers={"Cache-Control": "no-store"})


async def api_set(request):
    auth = request.headers.get("Authorization", "")
    given = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-Kitchen-Secret", "")
    if not SECRET or not hmac.compare_digest(given.encode(), SECRET.encode()):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        res = await set_link(request.app, str(body.get("url", "")), mode_from(body.get("mode")), "api")
    except (ValueError, json.JSONDecodeError, AttributeError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "id": res["id"], "show": res["show"], "error": res["error"]})


async def healthz(request):
    return web.json_response({"ok": True})


async def on_startup(app):
    app["http"] = aiohttp.ClientSession()


async def on_cleanup(app):
    await app["http"].close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not SECRET or len(SECRET) < 32:
        raise SystemExit("KITCHEN_SECRET must be set (32+ chars)")
    DATA.mkdir(parents=True, exist_ok=True)
    load_state()
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/current", current)
    app.router.add_post("/api/set", api_set)
    app.router.add_get("/healthz", healthz)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None)


if __name__ == "__main__":
    main()
