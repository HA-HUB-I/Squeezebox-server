"""
SqueezeCloud — локален сървър имитиращ mysqueezebox.com
Стартирай: python main.py
Порт: 9000 (HTTP) + 3483 (Slim Protocol TCP)
"""

import asyncio
import json
import socket
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Кеш в памет ──────────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str, ttl: int = 3600):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < ttl:
            return val
    return None

def cache_set(key: str, val):
    _cache[key] = (val, time.time())

# ── Глобален MAC на устройството (научаваме от HELO) ─────────────────────────
_device_mac = "00:04:20:2c:90:b1"  # default, обновява се от HELO/Comet

# ── Конфигурация ──────────────────────────────────────────────────────────────
CONFIG = {
    "server_name": "SqueezeCloud",
    "version": "8.5.0",
    "lat": 42.6977,
    "lon": 23.3219,
    "city": "София",
}

# ── Статични станции ──────────────────────────────────────────────────────────
STATIC_STATIONS = [
    {"name": "БНР Хоризонт",        "url": "https://stream.bnr.bg/horizont_24",       "genre": "News",    "country": "BG"},
    {"name": "БНР Христо Ботев",    "url": "https://stream.bnr.bg/hristobotev_24",    "genre": "Culture", "country": "BG"},
    {"name": "БНР Радио България",  "url": "https://stream.bnr.bg/radiobulgaria_24",  "genre": "News",    "country": "BG"},
    {"name": "Radio 1 Rock",         "url": "https://live.radio1.bg/radio1rock.mp3",   "genre": "Rock",    "country": "BG"},
    {"name": "NRJ Bulgaria",         "url": "https://stream.nrj.bg/nrj-128.mp3",      "genre": "Pop",     "country": "BG"},
    {"name": "Z-Rock Bulgaria",      "url": "https://stream.zrock.bg/zrock",           "genre": "Rock",    "country": "BG"},
    {"name": "BBC World Service",    "url": "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service", "genre": "News", "country": "UK"},
    {"name": "BBC Radio 6 Music",    "url": "https://stream.live.vc.bbcmedia.co.uk/bbc_6music",        "genre": "Music","country": "UK"},
    {"name": "KEXP 90.3 FM",         "url": "https://kexp-mp3-128.streamguys1.com/kexp128.mp3",         "genre": "Indie","country": "US"},
    {"name": "SomaFM Groove Salad",  "url": "https://ice1.somafm.com/groovesalad-128-mp3",              "genre": "Ambient","country": "US"},
    {"name": "SomaFM Drone Zone",    "url": "https://ice1.somafm.com/dronezone-128-mp3",                "genre": "Ambient","country": "US"},
    {"name": "Jazz24",               "url": "https://live.wostreaming.net/manifest/ppm-jazz24mp3-ibc1.m3u8", "genre": "Jazz","country": "US"},
    {"name": "1.FM Jazz & Blues",    "url": "https://strm112.1.fm/jazzandblues_mobile_mp3",             "genre": "Jazz","country": "US"},
]

# ── Подкасти ──────────────────────────────────────────────────────────────────
PODCAST_FEEDS = [
    {"name": "БНР Подкасти",       "url": "https://bnr.bg/radiobulgaria/podcast/category/44"},
    {"name": "Deutsche Welle BG",  "url": "https://rss.dw.com/rdf/podcast-bulgarisch-aktuell"},
    {"name": "BBC Global News",    "url": "https://podcasts.files.bbci.co.uk/p02nq0gn.rss"},
    {"name": "TED Talks Daily",    "url": "https://feeds.feedburner.com/TEDTalks_audio"},
    {"name": "Radiolab",           "url": "https://feeds.feedburner.com/radiolab"},
    {"name": "99% Invisible",      "url": "https://feeds.simplecast.com/BqbsxVfO"},
    {"name": "Freakonomics Radio", "url": "https://feeds.simplecast.com/Y8lFbOT4"},
]

# ── Новини ────────────────────────────────────────────────────────────────────
NEWS_FEEDS = [
    {"name": "БНР Новини",  "url": "https://bnr.bg/rss",                        "lang": "bg"},
    {"name": "Dnevnik.bg",  "url": "https://www.dnevnik.bg/rss/",               "lang": "bg"},
    {"name": "Reuters",     "url": "https://feeds.reuters.com/reuters/topNews", "lang": "en"},
    {"name": "BBC News",    "url": "https://feeds.bbci.co.uk/news/rss.xml",     "lang": "en"},
    {"name": "Al Jazeera",  "url": "https://www.aljazeera.com/xml/rss/all.xml", "lang": "en"},
]

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="SqueezeCloud", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═════════════════════════════════════════════════════════════════════════════
# AUTH / LOGIN
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/login")
@app.get("/user/login")
async def login(request: Request):
    mac = request.query_params.get("mac") or request.query_params.get("u") or "unknown"
    return {
        "status": "ok",
        "player": {"id": mac, "name": "Squeezebox Radio", "server": CONFIG["server_name"]},
        "token": f"{mac}:squeezecloud",
        "result": {
            "sn_version": CONFIG["version"],
            "playerid": mac,
            "userId": 1,
            "username": "squeezecloud",
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# SESSION — устройството проверява сесията при всяко зареждане
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/session")
@app.get("/sn/api/v1/session")
@app.get("/api/v1/account")
async def session(request: Request):
    mac = request.query_params.get("mac") or _device_mac
    return {
        "status": "ok",
        "result": {
            "userId": 1,
            "username": "squeezecloud",
            "sn_version": CONFIG["version"],
            "playerid": mac,
            "loggedIn": 1,
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# DEVICE REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/deviceRegistration")
@app.post("/api/v1/deviceRegistration")
async def device_registration(request: Request):
    mac = request.query_params.get("mac") or _device_mac
    return {
        "status": "ok",
        "result": {
            "registered": 1,
            "playerid": mac,
            "pin": False,
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# FIRMWARE — устройството проверява за обновления
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/firmware")
@app.get("/update/firmware")
@app.get("/firmware/squeezeos/baby/firmware.xml")
async def firmware_check(request: Request):
    return {
        "status": "ok",
        "result": {
            "firmwareVersion": "7.7.3",
            "upgradeUrl": "",
            "upgradeNeeded": 0,
            "reset": 0,
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# TIME
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/time")
async def get_time():
    ts = int(time.time())
    return {"status": "ok", "time": ts, "result": ts}

# ═════════════════════════════════════════════════════════════════════════════
# STATUS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/")
@app.get("/api/v1/status")
async def status():
    return {
        "status": "ok",
        "version": CONFIG["version"],
        "name": CONFIG["server_name"],
        "result": {
            "version": CONFIG["version"],
            "server_name": CONFIG["server_name"],
            "uuid": "squeezecloud-local-v1",
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# APPS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/apps")
@app.get("/apps")
async def get_apps():
    return {
        "status": "ok",
        "result": {
            "loop_loop": [
                {"id": "radio",    "name": "Internet Radio", "type": "app"},
                {"id": "podcasts", "name": "Podcasts",        "type": "app"},
                {"id": "weather",  "name": "Weather",         "type": "app"},
                {"id": "news",     "name": "News",            "type": "app"},
            ],
            "count": 4,
        }
    }

# ═════════════════════════════════════════════════════════════════════════════
# COMET / BAYEUX — /cometd
# Squeezebox използва Bayeux protocol за persistent connection към сървъра
# Имплементираме: handshake → connect → subscribe → slim/request
# ═════════════════════════════════════════════════════════════════════════════

import uuid as _uuid

# Активни Comet сесии: clientId → { mac, subscriptions }
_comet_sessions: dict = {}


def _new_client_id() -> str:
    return _uuid.uuid4().hex[:8]


@app.post("/cometd")
async def cometd(request: Request):
    try:
        messages = await request.json()
    except Exception:
        return JSONResponse([{"successful": False, "error": "Invalid JSON"}])

    if not isinstance(messages, list):
        messages = [messages]

    for msg in messages:
        ch = msg.get("channel", "?")
        data = msg.get("data", {})
        print(f"[Comet] ← {ch} | data={json.dumps(data)[:200]}")

    responses = []
    is_connect = any(m.get("channel") in ("/meta/connect", "/meta/reconnect") for m in messages)
    is_streaming = any(
        m.get("connectionType") == "streaming"
        for m in messages
    )

    for msg in messages:
        channel = msg.get("channel", "")
        resp = await _handle_comet_message(msg, channel)
        if resp:
            responses.append(resp)
            print(f"[Comet] → {channel} | resp={json.dumps(resp)[:200]}")

    if is_connect:
        client_id = next(
            (m.get("clientId") for m in messages if m.get("channel") in ("/meta/connect", "/meta/reconnect")),
            None
        )

        async def event_stream():
            # Първи chunk — всички текущи responses включително /meta/connect
            chunk = json.dumps(responses) + "\r\n"
            yield chunk.encode()

            # Keepalive — само /meta/connect на всеки 30 секунди
            while True:
                await asyncio.sleep(30)
                keepalive = json.dumps([{
                    "channel": "/meta/connect",
                    "clientId": client_id,
                    "successful": True,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                    "advice": {"timeout": 60000, "interval": 0, "reconnect": "retry"},
                }]) + "\r\n"
                yield keepalive.encode()

        from starlette.responses import StreamingResponse
        # НЕ задаваме Transfer-Encoding ръчно — uvicorn го прави правилно
        return StreamingResponse(
            event_stream(),
            media_type="application/json",
        )

    return JSONResponse(responses)


@app.get("/cometd")
async def cometd_get(request: Request):
    print(f"[Comet] GET /cometd от {request.client}")
    return JSONResponse([{"channel": "/meta/connect", "successful": True}])


async def _handle_comet_message(msg: dict, channel: str) -> dict:
    # ── /meta/handshake ──────────────────────────────────────────────────────
    if channel == "/meta/handshake":
        client_id = _new_client_id()
        _comet_sessions[client_id] = {"subscriptions": [], "mac": ""}
        # Извлечи MAC от ext ако е наличен
        ext = msg.get("ext", {})
        if ext.get("mac"):
            global _device_mac
            _device_mac = ext["mac"]
            print(f"[Comet] MAC от handshake: {_device_mac}")
        return {
            "channel": "/meta/handshake",
            "version": "1.0",
            "minimumVersion": "1.0",
            "supportedConnectionTypes": ["long-polling", "streaming"],
            "clientId": client_id,
            "successful": True,
            "advice": {
                "timeout": 60000,
                "interval": 0,
                "reconnect": "retry",
            },
        }

    # ── /meta/connect ────────────────────────────────────────────────────────
    elif channel == "/meta/connect":
        client_id = msg.get("clientId", "")
        return {
            "channel": "/meta/connect",
            "clientId": client_id,
            "successful": True,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "advice": {"timeout": 60000, "interval": 0, "reconnect": "retry"},
        }

    # ── /meta/reconnect ──────────────────────────────────────────────────────
    # Squeezebox изпраща reconnect когато вече има clientId (след disconnect)
    # _response() в Comet.lua: if event.channel == '/meta/reconnect' → _connected()
    elif channel == "/meta/reconnect":
        client_id = msg.get("clientId", "")
        return {
            "channel": "/meta/reconnect",
            "clientId": client_id,
            "successful": True,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "advice": {"timeout": 60000, "interval": 0, "reconnect": "retry"},
        }

    # ── /meta/subscribe ──────────────────────────────────────────────────────
    elif channel == "/meta/subscribe":
        client_id = msg.get("clientId", "")
        subscription = msg.get("subscription", "")
        if client_id in _comet_sessions:
            _comet_sessions[client_id]["subscriptions"].append(subscription)
        return {
            "channel": "/meta/subscribe",
            "clientId": client_id,
            "subscription": subscription,
            "successful": True,
        }

    # ── /meta/unsubscribe ────────────────────────────────────────────────────
    elif channel == "/meta/unsubscribe":
        client_id = msg.get("clientId", "")
        subscription = msg.get("subscription", "")
        return {
            "channel": "/meta/unsubscribe",
            "clientId": client_id,
            "subscription": subscription,
            "successful": True,
        }

    # ── /meta/disconnect ─────────────────────────────────────────────────────
    elif channel == "/meta/disconnect":
        client_id = msg.get("clientId", "")
        _comet_sessions.pop(client_id, None)
        return {
            "channel": "/meta/disconnect",
            "clientId": client_id,
            "successful": True,
        }

    # ── /slim/request — JSON-RPC през Comet ──────────────────────────────────
    elif channel == "/slim/request":
        client_id = msg.get("clientId", "")
        data = msg.get("data", {})
        response_channel = data.get("response", f"/slim/reply/{client_id}")

        # Извлечи params — същия формат като jsonrpc.js
        params = data.get("request", [])
        player_mac = params[0] if len(params) > 0 else ""
        cmd = params[1] if len(params) > 1 else []
        command = cmd[0] if cmd else ""

        result = await dispatch_rpc(command, cmd, player_mac)

        return {
            "channel": response_channel,
            "clientId": client_id,
            "successful": True,
            "data": result,
            "id": msg.get("id", ""),
        }

    # ── /slim/subscribe — subscription за player events ──────────────────────
    elif channel == "/slim/subscribe":
        client_id = msg.get("clientId", "")
        data = msg.get("data", {})
        response_channel = data.get("response", f"/slim/reply/{client_id}")

        params = data.get("request", [])
        player_mac = params[0] if len(params) > 0 else ""
        cmd = params[1] if len(params) > 1 else []
        command = cmd[0] if cmd else ""

        # serverstatus трябва да върне пълен отговор с players
        if command == "serverstatus":
            # Винаги използваме реалния MAC от HELO — никога не го hardcode-ваме
            mac = _device_mac
            print(f"[serverstatus] via subscribe, mac={mac}")
            result = {
                "version": CONFIG["version"],
                "server_name": CONFIG["server_name"],
                "uuid": "squeezecloud-local-v1",
                "player count": 1,
                "lastscan": int(time.time()),
                "rescan": 0,
                "pin": False,
                "info": "total_genres:0,total_artists:0,total_albums:0,total_songs:0",
                # Полета за разпознаване от Jive firmware като SqueezeNetwork
                "isSqueezenetwork": 1,
                "sn_version": CONFIG["version"],
                "players_loop": [{
                    "playerid": mac,
                    "name": "Squeezebox Radio",
                    "model": "baby",
                    "modelname": "Squeezebox Radio",
                    "connected": 1,
                    "isplaying": 0,
                    "power": 1,
                    "seq_no": 0,
                }],
            }
        elif command == "firmwareupgrade":
            result = {
                "firmwareVersion": "7.7.3",
                "upgradeUrl": "",
                "upgradeNeeded": 0,
                "reset": 0,
            }
        else:
            result = await dispatch_rpc(command, cmd, player_mac)

        return {
            "channel": response_channel,
            "clientId": client_id,
            "successful": True,
            "data": result,
        }

    # ── Всичко непознато — успех ──────────────────────────────────────────────
    else:
        return {
            "channel": channel,
            "clientId": msg.get("clientId", ""),
            "successful": True,
        }


# ═════════════════════════════════════════════════════════════════════════════
# JSON-RPC (главният endpoint — Squeezebox го ползва за всичко)
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/jsonrpc.js")
@app.get("/jsonrpc.js")
async def jsonrpc(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    params = body.get("params", [])
    rpc_id = body.get("id", 1)
    player_mac = params[0] if len(params) > 0 else ""
    cmd = params[1] if len(params) > 1 else []
    command = cmd[0] if cmd else ""

    print(f"[RPC] mac={player_mac} cmd={cmd}")

    result = await dispatch_rpc(command, cmd, player_mac)

    return {"id": rpc_id, "method": "slim.request", "result": result}


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def catch_all(request: Request, path: str):
    """Логва всички непознати endpoints"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    print(f"[???] {request.method} /{path} | body={json.dumps(body)[:300]}")
    return JSONResponse({"status": "ok", "result": []})


async def dispatch_rpc(command: str, cmd: list, player_mac: str) -> dict:
    if command == "serverstatus":
        mac = _device_mac
        print(f"[serverstatus] via dispatch, mac={mac}")
        return {
            "version": CONFIG["version"],
            "server_name": CONFIG["server_name"],
            "uuid": "squeezecloud-local-v1",
            "player count": 1,
            "lastscan": int(time.time()),
            "rescan": 0,
            "pin": False,
            # Полета за разпознаване от Jive firmware като SqueezeNetwork
            "isSqueezenetwork": 1,
            "sn_version": CONFIG["version"],
            "players_loop": [{
                "playerid": mac,
                "name": "Squeezebox Radio",
                "model": "baby",
                "modelname": "Squeezebox Radio",
                "connected": 1,
                "isplaying": 0,
                "power": 1,
                "seq_no": 0,
            }],
        }

    elif command == "players":
        return {
            "count": 1,
            "players_loop": [{
                "playerid": player_mac,
                "name": "Squeezebox Radio",
                "model": "squeezebox_radio",
                "isplaying": 0,
                "connected": 1,
            }]
        }

    elif command == "status":
        return {
            "playerid": player_mac,
            "name": "Squeezebox Radio",
            "mode": "stop",
            "mixer_volume": 50,
            "playlist_cur_index": 0,
            "playlist_loop": [],
        }

    elif command in ("radios", "radio"):
        start = int(cmd[1]) if len(cmd) > 1 else 0
        count = int(cmd[2]) if len(cmd) > 2 else 10
        stations = await get_radio_stations()
        slice_ = stations[start:start + count]
        return {
            "count": len(stations),
            "loop_loop": [
                {
                    "id": f"radio:{start+i}",
                    "text": s["name"],
                    "type": "audio",
                    "url": s["url"],
                    "isaudio": 1,
                    "hasitems": 0,
                    "icon": "",
                }
                for i, s in enumerate(slice_)
            ]
        }

    elif command == "podcasts":
        start = int(cmd[1]) if len(cmd) > 1 else 0
        item_id = next((c for c in cmd if isinstance(c, str) and "item_id:" in c), None)

        if not item_id:
            return {
                "count": len(PODCAST_FEEDS),
                "loop_loop": [
                    {"id": f"podcast:{i}", "name": f["name"], "type": "playlist",
                     "isaudio": 0, "hasitems": 1, "item_id": f"podcast:{i}"}
                    for i, f in enumerate(PODCAST_FEEDS)
                ]
            }
        else:
            feed_idx = int(item_id.replace("item_id:podcast:", ""))
            feed = PODCAST_FEEDS[feed_idx]
            episodes = await fetch_podcast_episodes(feed)
            return {
                "count": len(episodes),
                "loop_loop": [
                    {"id": f"ep:{feed_idx}:{i}", "name": ep["title"],
                     "type": "audio", "url": ep["url"], "isaudio": 1}
                    for i, ep in enumerate(episodes[start:start+10])
                ]
            }

    elif command == "menu":
        return {
            "count": 5,
            "item_loop": [
                {
                    "id": "favorites",
                    "node": "home",
                    "text": "Favorites",
                    "iconStyle": "hm_favorites",
                    "weight": 10,
                    "isANode": 0,
                    "actions": {
                        "go": {"cmd": ["favorites", 0, 100], "player": 0}
                    },
                    "window": {"windowId": "favorites"},
                },
                {
                    "id": "globalSearch",
                    "node": "home",
                    "text": "Internet Radio",
                    "iconStyle": "hm_radio",
                    "weight": 20,
                    "isANode": 0,
                    "actions": {
                        "go": {"cmd": ["radios", 0, 100], "player": 0}
                    },
                    "window": {"windowId": "globalSearch"},
                },
                {
                    "id": "myApps",
                    "node": "home",
                    "text": "My Apps",
                    "iconStyle": "hm_myApps",
                    "weight": 30,
                    "isANode": 1,
                    "window": {"windowId": "myApps"},
                },
                {
                    "id": "randomplay",
                    "node": "home",
                    "text": "Podcasts",
                    "iconStyle": "hm_randomplay",
                    "weight": 40,
                    "isANode": 0,
                    "actions": {
                        "go": {"cmd": ["podcasts", 0, 100], "player": 0}
                    },
                    "window": {"windowId": "randomplay"},
                },
                {
                    "id": "settingsAlarm",
                    "node": "home",
                    "text": "Weather & News",
                    "iconStyle": "hm_settingsAlarm",
                    "weight": 50,
                    "isANode": 0,
                    "actions": {
                        "go": {"cmd": ["weather"], "player": 0}
                    },
                    "window": {"windowId": "settingsAlarm"},
                },
            ],
        }

    elif command == "register":
        # squeezeNetworkRequest очаква item_loop response за _browseSink
        # pin: False сигнализира на Jive firmware че плейърът е вече свързан (не нужен PIN)
        # Това тригерира notify_serverLinked → step9 → завършва setup без signup screen
        return {
            "count": 0,
            "pin": False,
            "registered": 1,
            "connected": 1,
        }

    elif command == "playerRegister":
        mac = cmd[2] if len(cmd) > 2 else player_mac
        name = cmd[3] if len(cmd) > 3 else "Squeezebox Radio"
        return {
            "ok": 1,
            "id": mac,
            "name": name,
            "pin": False,
            "registered": 1,
            "connected": 1,
            "count": 0,
        }

    elif command == "firmwareupgrade":
        # Казваме на устройството че няма нужда от ъпдейт
        return {
            "firmwareVersion": "7.7.3",
            "upgradeUrl": "",
            "upgradeNeeded": 0,
            "reset": 0,
        }

    elif command in ("favorites", "browseLibrary"):
        # Любими станции — показваме статичните BG станции
        start = int(cmd[1]) if len(cmd) > 1 else 0
        count = int(cmd[2]) if len(cmd) > 2 else 10
        slice_ = STATIC_STATIONS[start:start + count]
        return {
            "count": len(STATIC_STATIONS),
            "loop_loop": [
                {
                    "id": f"fav:{start + i}",
                    "text": s["name"],
                    "url": s["url"],
                    "type": "audio",
                    "isaudio": 1,
                    "hasitems": 0,
                }
                for i, s in enumerate(slice_)
            ]
        }

    elif command == "weather":
        w = await fetch_weather()
        return {
            "count": 1,
            "loop_loop": [{"id": "weather:current", "text": w["summary"], "type": "text", "isaudio": 0}]
        }

    elif command == "news":
        items = await fetch_news_items(NEWS_FEEDS[0])
        return {
            "count": len(items),
            "loop_loop": [
                {"id": f"news:{i}", "text": item["title"], "type": "text", "isaudio": 0}
                for i, item in enumerate(items[:10])
            ]
        }

    elif command == "apps":
        return {
            "count": 4,
            "loop_loop": [
                {"id": "radio",    "text": "Internet Radio", "cmd": "radios",   "type": "link"},
                {"id": "podcasts", "text": "Podcasts",        "cmd": "podcasts", "type": "link"},
                {"id": "weather",  "text": "Weather",         "cmd": "weather",  "type": "link"},
                {"id": "news",     "text": "News",            "cmd": "news",     "type": "link"},
            ]
        }

    else:
        return {"ok": 1, "count": 0}

# ═════════════════════════════════════════════════════════════════════════════
# RADIO
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/radios")
@app.get("/radio")
async def radio_browse(
    genre: Optional[str] = None,
    country: Optional[str] = None,
    search: Optional[str] = None,
):
    stations = await get_radio_stations()

    if genre:
        stations = [s for s in stations if genre.lower() in (s.get("genre") or "").lower()]
    if country:
        stations = [s for s in stations if s.get("country", "").lower() == country.lower()]
    if search:
        stations = [s for s in stations if search.lower() in s["name"].lower()]

    genres = sorted(set(s.get("genre", "Other") for s in stations))

    return {
        "status": "ok",
        "count": len(stations),
        "genres": genres,
        "stations": stations[:100],
    }


async def get_radio_stations() -> list:
    cached = cache_get("stations:all", ttl=3600)
    if cached:
        return cached

    live = []
    apis = [
        "https://de1.api.radio-browser.info",
        "https://nl1.api.radio-browser.info",
        "https://at1.api.radio-browser.info",
    ]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{apis[0]}/json/stations/search",
                params={"limit": 300, "hidebroken": "true", "order": "votes",
                        "reverse": "true", "is_https": "true"},
                headers={"User-Agent": "SqueezeCloud/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                live = [
                    {
                        "name": s["name"].strip(),
                        "url": s["url_resolved"],
                        "genre": (s.get("tags") or "Music").split(",")[0].strip().title(),
                        "country": s.get("countrycode", ""),
                        "bitrate": s.get("bitrate", 128),
                    }
                    for s in data
                    if s.get("url_resolved") and s.get("name")
                ]
    except Exception as e:
        print(f"Radio Browser API error: {e}")

    all_stations = STATIC_STATIONS + live
    cache_set("stations:all", all_stations)
    return all_stations

# ═════════════════════════════════════════════════════════════════════════════
# WEATHER  (Open-Meteo — безплатен, без API key)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/weather")
@app.get("/weather")
async def weather_endpoint(
    lat: float = CONFIG["lat"],
    lon: float = CONFIG["lon"],
    city: str = CONFIG["city"],
):
    return await fetch_weather(lat, lon, city)


async def fetch_weather(
    lat: float = CONFIG["lat"],
    lon: float = CONFIG["lon"],
    city: str = CONFIG["city"],
) -> dict:
    cached = cache_get(f"weather:{lat}:{lon}", ttl=600)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weathercode,windspeed_10m,relativehumidity_2m",
                    "timezone": "auto",
                },
            )
            data = resp.json()
            curr = data["current"]
            temp = curr["temperature_2m"]
            humidity = curr["relativehumidity_2m"]
            wind = curr["windspeed_10m"]
            code = curr["weathercode"]
            condition = _weather_code(code)
            icon = _weather_icon(code)
            result = {
                "status": "ok",
                "city": city,
                "temperature": temp,
                "unit": "°C",
                "condition": condition,
                "humidity": humidity,
                "wind_kmh": wind,
                "icon": icon,
                "summary": f"{city}: {icon} {condition}, {temp}°C, влажност {humidity}%, вятър {wind} км/ч",
            }
            cache_set(f"weather:{lat}:{lon}", result)
            return result
    except Exception as e:
        return {"status": "error", "city": city, "summary": f"{city}: няма данни", "error": str(e)}


def _weather_code(code: int) -> str:
    codes = {
        0: "Ясно", 1: "Предимно ясно", 2: "Частично облачно", 3: "Облачно",
        45: "Мъгла", 48: "Скреж",
        51: "Ситен дъжд", 53: "Дъжд", 55: "Силен дъжд",
        61: "Дъжд", 63: "Умерен дъжд", 65: "Силен дъжд",
        71: "Сняг", 73: "Умерен сняг", 75: "Силен сняг",
        80: "Валежи", 81: "Умерени валежи", 82: "Силни валежи",
        95: "Гръмотевична буря", 99: "Силна буря",
    }
    return codes.get(code, "Непознат")


def _weather_icon(code: int) -> str:
    if code == 0: return "☀️"
    if code <= 2: return "🌤️"
    if code <= 3: return "☁️"
    if code <= 48: return "🌫️"
    if code <= 67: return "🌧️"
    if code <= 77: return "❄️"
    if code <= 82: return "🌦️"
    return "⛈️"

# ═════════════════════════════════════════════════════════════════════════════
# NEWS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/news")
@app.get("/news")
async def news_endpoint(lang: str = "bg"):
    feed = next((f for f in NEWS_FEEDS if f["lang"] == lang), NEWS_FEEDS[0])
    items = await fetch_news_items(feed)
    return {"status": "ok", "source": feed["name"], "count": len(items), "items": items[:20]}


async def fetch_news_items(feed: dict) -> list:
    cached = cache_get(f"news:{feed['url']}", ttl=300)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(feed["url"], headers={"User-Agent": "SqueezeCloud/1.0"})
            items = _parse_rss(resp.text)
            cache_set(f"news:{feed['url']}", items)
            return items
    except Exception as e:
        print(f"News fetch error: {e}")
        return [{"title": "Неуспешно зареждане", "url": "", "description": ""}]

# ═════════════════════════════════════════════════════════════════════════════
# PODCASTS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/podcasts")
@app.get("/podcasts")
async def podcasts_endpoint(feed: int = 0):
    f = PODCAST_FEEDS[feed] if feed < len(PODCAST_FEEDS) else PODCAST_FEEDS[0]
    episodes = await fetch_podcast_episodes(f)
    return {"status": "ok", "feed": f["name"], "count": len(episodes), "episodes": episodes[:20]}


async def fetch_podcast_episodes(feed: dict) -> list:
    cached = cache_get(f"podcast:{feed['url']}", ttl=900)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(feed["url"], headers={"User-Agent": "SqueezeCloud/1.0"})
            episodes = _parse_rss_audio(resp.text)
            cache_set(f"podcast:{feed['url']}", episodes)
            return episodes
    except Exception as e:
        print(f"Podcast fetch error: {e}")
        return []

# ═════════════════════════════════════════════════════════════════════════════
# RSS ПАРСЕР
# ═════════════════════════════════════════════════════════════════════════════

def _parse_rss(xml: str) -> list:
    items = []
    try:
        root = ET.fromstring(xml)
        ns = {"media": "http://search.yahoo.com/mrss/"}
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            desc = item.findtext("description") or ""
            if title.strip():
                items.append({
                    "title": _clean(title),
                    "url": link.strip(),
                    "description": _clean(desc)[:200],
                })
    except Exception:
        # Fallback regex parser за счупен XML
        for m in re.finditer(r"<item[^>]*>(.*?)</item>", xml, re.DOTALL):
            title = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", m.group(1), re.DOTALL)
            if title:
                items.append({"title": _clean(title.group(1)), "url": "", "description": ""})
    return items


def _parse_rss_audio(xml: str) -> list:
    items = []
    try:
        root = ET.fromstring(xml)
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            enclosure = item.find("enclosure")
            url = enclosure.get("url", "") if enclosure is not None else ""
            if title.strip() and url:
                items.append({"title": _clean(title), "url": url})
    except Exception:
        for m in re.finditer(r"<item[^>]*>(.*?)</item>", xml, re.DOTALL):
            title = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", m.group(1), re.DOTALL)
            enc = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', m.group(1))
            if title and enc:
                items.append({"title": _clean(title.group(1)), "url": enc.group(1)})
    return items


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " ")
    return text.strip()

# ═════════════════════════════════════════════════════════════════════════════
# SLIM PROTOCOL — TCP 3483
# Squeezebox го изисква за да се счита сървърът за "свързан"
# Имплементираме минимален handshake — достатъчен за discovery
# ═════════════════════════════════════════════════════════════════════════════

async def slim_handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"[Slim] Свързан: {addr}")
    keepalive_task = None

    async def send_keepalive():
        try:
            while True:
                await asyncio.sleep(10)
                _slim_send(writer, b"strm", b"t" + b"\x00" * 24)
                await writer.drain()
        except Exception:
            pass

    try:
        while True:
            header = await asyncio.wait_for(reader.readexactly(8), timeout=60)
            op = header[:4].decode("ascii", errors="ignore").strip()
            length = int.from_bytes(header[4:8], "big")

            body = b""
            if length > 0:
                body = await asyncio.wait_for(reader.readexactly(length), timeout=10)

            print(f"[Slim] OP={op!r} len={length}")

            if op == "HELO":
                if len(body) >= 8:
                    mac = ":".join(f"{b:02x}" for b in body[2:8])
                    print(f"[Slim] HELO от MAC={mac}")
                    # Запази реалния MAC глобално
                    global _device_mac
                    _device_mac = mac

                # Само vers — БЕЗ serv!
                # serv кара устройството да disconnect/reconnect
                # Реалният LMS изпраща serv само при redirect към друг сървър
                version = CONFIG["version"].encode("utf-8")
                _slim_send(writer, b"vers", version)
                await writer.drain()
                print(f"[Slim] ✓ HELO → само vers (без serv)")

                # Стартирай keepalive
                if keepalive_task is None:
                    keepalive_task = asyncio.create_task(send_keepalive())

            elif op == "STAT":
                event = body[0:4].decode("ascii", errors="ignore") if len(body) >= 4 else "????"
                print(f"[Slim] STAT event={event!r}")
                # Не отговаряме — просто държим връзката жива

            elif op == "BYE!":
                print(f"[Slim] BYE от {addr}")
                break

            elif op in ("IR  ", "RESP", "BODY", "META", "BUTN"):
                pass

            else:
                print(f"[Slim] Непознат OP={op!r}")

    except asyncio.IncompleteReadError:
        pass
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        print(f"[Slim] Грешка: {e}")
    finally:
        if keepalive_task:
            keepalive_task.cancel()
        try:
            writer.close()
        except Exception:
            pass
        print(f"[Slim] Разкачен: {addr}")


def _slim_send(writer: asyncio.StreamWriter, cmd: bytes, data: bytes):
    """
    Изпраща пакет към Squeezebox.
    ПРАВИЛЕН формат: 2 байта length + 4 байта cmd + payload
    (Squeezebox чете 2 байта length, после len байта където първите 4 са opcode)
    """
    body = cmd + data
    packet = len(body).to_bytes(2, "big") + body
    writer.write(packet)


async def start_slim_server():
    server = await asyncio.start_server(slim_handle_client, "0.0.0.0", 3483)
    print("[Slim] TCP 3483 слуша...")
    async with server:
        await server.serve_forever()


# ═════════════════════════════════════════════════════════════════════════════
# UDP DISCOVERY — 255.255.255.255:3483
# Squeezebox вика в ефира "има ли LMS сървър?"
# Ние отговаряме и той се свързва директно — без hosts пач!
# ═════════════════════════════════════════════════════════════════════════════

async def slim_udp_discovery(local_ip: str):
    """
    Squeezebox изпраща UDP broadcast към 255.255.255.255:3483
    с payload 'eIPAD\x00NAME\x00' търсейки LMS сървър.
    Отговаряме с 'E' + server_name + '\x00' + ip + '\x00' + port + '\x00'
    """
    loop = asyncio.get_event_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 3483))
    sock.setblocking(False)

    print(f"[Discovery] UDP 3483 слуша за broadcasts...")

    name = CONFIG["server_name"].encode("utf-8")
    ip   = local_ip.encode("utf-8")
    port = b"9000"
    # Стандартен LMS discovery отговор
    response = b"E" + name + b"\x00" + ip + b"\x00" + port + b"\x00"

    while True:
        try:
            data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
            print(f"[Discovery] Broadcast от {addr[0]}: {data[:30]}")

            # Squeezebox изпраща 'e' за discovery request
            if data and data[0:1] in (b"e", b"E", b"d"):
                sock.sendto(response, addr)
                print(f"[Discovery] ✓ Отговорено на {addr[0]} → {CONFIG['server_name']} @ {local_ip}:9000")

        except BlockingIOError:
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"[Discovery] Грешка: {e}")
            await asyncio.sleep(1)


# ═════════════════════════════════════════════════════════════════════════════
# СТАРТ — стартира всичките три услуги едновременно
# ═════════════════════════════════════════════════════════════════════════════

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def main():
    import uvicorn

    local_ip = get_local_ip()

    print("=" * 60)
    print("  SqueezeCloud сървър")
    print("=" * 60)
    print(f"  Локално IP:       {local_ip}")
    print(f"  HTTP порт:        9000  (LMS API)")
    print(f"  TCP порт:         3483  (Slim Protocol)")
    print(f"  UDP порт:         3483  (Autodiscovery broadcast)")
    print()
    print("  Squeezebox ще се открие АВТОМАТИЧНО чрез broadcast!")
    print()
    print("  Ако не се открие — добави в SSH на Squeezebox:")
    print(f"  cat > /mnt/storage/etc/hosts << 'EOF'")
    print(f"  127.0.0.1 localhost")
    print(f"  {local_ip} mysqueezebox.com")
    print(f"  {local_ip} www.mysqueezebox.com")
    print(f"  {local_ip} update.squeezenetwork.com")
    print(f"  {local_ip} config.logitechmusic.com")
    print(f"  EOF")
    print()
    print("  После: reboot")
    print("=" * 60)

    # Стартираме всичките три услуги паралелно
    udp_task  = asyncio.create_task(slim_udp_discovery(local_ip))
    slim_task = asyncio.create_task(start_slim_server())

    config = uvicorn.Config("main:app", host="0.0.0.0", port=9000, reload=False, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(udp_task, slim_task, server.serve())


if __name__ == "__main__":
    asyncio.run(main())
