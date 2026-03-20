"""
SqueezeCloud — локален сървър имитиращ mysqueezebox.com
Стартирай: python main.py
Порт: 9000 (HTTP) + 3483 (Slim Protocol TCP)
"""

import asyncio
import json
import logging
import os
import shutil
import socket
import struct
import sys
import threading
import time
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("squeezecloud")

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

# ── Now-playing state (обновява се от playlist play/stop команди) ─────────────
# { "url": "...", "name": "..." }  или  {}  ако нищо не се пуска
_now_playing: dict = {}

# ── TCP Slim Protocol writer — set when device connects, cleared on disconnect ─
_slim_writer: Optional[asyncio.StreamWriter] = None
# ── Local software player subprocess (ffplay/mpv/vlc/powershell) ──────────────
_local_player_proc: Optional[asyncio.subprocess.Process] = None
# ── Pure-Python audio player (sounddevice + miniaudio) ───────────────────────
_python_audio_stop: Optional[threading.Event] = None
_python_audio_thread: Optional[threading.Thread] = None
# ── Local LAN IP — set once in main() ────────────────────────────────────────
_local_ip: str = "127.0.0.1"
# ── Comet status subscription channel — set when device subscribes for status ─
_status_channel: str = ""
# ── Player volume (0-100) — synced to Squeezebox when connected ───────────────
_player_volume: int = 80
# ── Timestamp of last CometD message from device (used to detect connection) ──
_comet_last_seen: float = 0.0

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
    {"name": "Jazz24",               "url": "https://streams.jazz24.org/jazz24_mp3",                          "genre": "Jazz","country": "US"},
    {"name": "1.FM Jazz & Blues",    "url": "https://strm112.1.fm/jazzandblues_mobile_mp3",             "genre": "Jazz","country": "US"},
]

# ── Потребителски станции от stations.json (без зависимост от Radio Browser) ──
# Създай squeezecloud/stations.json за да добавиш свои станции.
# Формат: [{"name": "...", "url": "...", "genre": "...", "country": "..."}, ...]
import os as _os, pathlib as _pathlib

def _load_custom_stations() -> list:
    _path = _pathlib.Path(_os.path.dirname(_os.path.abspath(__file__))) / "stations.json"
    if not _path.exists():
        return []
    try:
        with open(_path, "r", encoding="utf-8") as _f:
            data = json.load(_f)
        if isinstance(data, list):
            log.info("Заредени %d потребителски станции от stations.json", len(data))
            return data
        log.warning("stations.json не е масив (list) — пропуснат")
    except json.JSONDecodeError as e:
        log.warning("stations.json е невалиден JSON: %s", e)
    except Exception as e:
        log.warning("Грешка при четене на stations.json: %s", e)
    return []

CUSTOM_STATIONS: list = _load_custom_stations()

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


@app.middleware("http")
async def detect_local_ip_middleware(request: Request, call_next):
    """
    Auto-detect the server's LAN IP from the Host header of every incoming
    request. When the Squeezebox connects via http://192.168.1.X:9000/... the
    Host header contains exactly that IP — far more reliable than trying to
    guess the LAN interface at startup.
    """
    global _local_ip
    host_header = request.headers.get("host", "").split(":")[0]
    # Only accept dotted-decimal IPs, skip hostnames like 'mysqueezebox.com'
    if host_header and host_header not in ("127.0.0.1", "localhost", ""):
        parts = host_header.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            if _local_ip != host_header:
                log.info("[config] Local IP detected from Host header: %s", host_header)
                _local_ip = host_header
    return await call_next(request)

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
# WEB CONTROL — прост HTML статус / контролен панел
# ═════════════════════════════════════════════════════════════════════════════

_WEBCONTROL_HTML = """<!DOCTYPE html>
<html lang="bg">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SqueezeCloud — контрол</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: sans-serif; margin: 0; background: #1a1a2e; color: #eee; }
    header { background: #16213e; padding: .8rem 1.4rem; display: flex; align-items: center; gap: .7rem; flex-wrap: wrap; }
    header h1 { margin: 0; font-size: 1.25rem; color: #e94560; white-space: nowrap; }
    .badge { font-size: .7rem; background: #0f3460; padding: .15rem .55rem; border-radius: 999px; white-space: nowrap; }
    .badge.green  { background: #1a6b3a; color: #7dffaa; }
    .badge.orange { background: #5c3d00; color: #ffc261; }
    .badge.red    { background: #5c0000; color: #ff9090; }
    main { padding: .9rem 1.4rem; max-width: 900px; margin: 0 auto; }
    .card { background: #16213e; border-radius: 8px; padding: .9rem 1.1rem; margin-bottom: .9rem; }
    .card h2 { margin: 0 0 .6rem; font-size: .8rem; color: #e94560; text-transform: uppercase; letter-spacing: .07em; }

    /* ── Now-Playing ──────────────────────────────────────── */
    #np-card { display: none; }
    #np-card.active { display: block; }
    .np-inner { display: flex; align-items: center; gap: .9rem; flex-wrap: wrap; }
    .np-icon { font-size: 1.8rem; flex-shrink: 0; animation: spin 3s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .np-icon.paused { animation-play-state: paused; }
    .np-text { flex: 1 1 140px; }
    .np-name { font-size: 1rem; font-weight: bold; color: #fff; word-break: break-word; }
    .np-sub  { font-size: .72rem; color: #aaa; margin-top: .15rem; word-break: break-all; }
    .np-source { font-size: .72rem; margin-top: .2rem; }
    .np-source.device { color: #7dffaa; }
    .np-source.browser { color: #ffc261; }
    .np-source.local   { color: #90c0ff; }
    .np-controls { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; }
    .ctrl-btn { background: #0f3460; border: none; color: #eee; width: 2rem; height: 2rem;
                border-radius: 50%; cursor: pointer; font-size: .95rem; display: flex;
                align-items: center; justify-content: center; transition: background .15s; }
    .ctrl-btn:hover { background: #e94560; }
    .ctrl-btn.stop-btn { background: #6b1a1a; }
    .ctrl-btn.stop-btn:hover { background: #e94560; }
    .vol-row { display: flex; align-items: center; gap: .35rem; font-size: .72rem; color: #aaa; width: 100%; }
    #vol-slider { flex: 1; accent-color: #e94560; cursor: pointer; }
    #vol-label { min-width: 2.2rem; text-align: right; }

    /* ── Status strip ────────────────────────────────────── */
    .status-strip { display: flex; gap: .8rem; flex-wrap: wrap; font-size: .75rem; color: #aaa; }
    .status-item { display: flex; align-items: center; gap: .3rem; }
    .dot { width: .55rem; height: .55rem; border-radius: 50%; background: #444; display: inline-block; }
    .dot.green { background: #5dbb80; }
    .dot.orange { background: #cc8800; }
    .dot.red    { background: #bb5555; }

    /* ── Genre filter ────────────────────────────────────── */
    .genre-list { display: flex; flex-wrap: wrap; gap: .35rem; }
    .genre-btn { background: #0f3460; border: none; color: #eee; padding: .25rem .7rem;
                 border-radius: 999px; cursor: pointer; font-size: .78rem; transition: background .15s; }
    .genre-btn:hover, .genre-btn.active { background: #e94560; }

    /* ── Search ──────────────────────────────────────────── */
    #search-box { background: #0f3460; border: 1px solid #1e4080; border-radius: 6px;
                  color: #eee; padding: .38rem .75rem; font-size: .82rem; width: 100%;
                  margin: .6rem 0 .4rem; outline: none; }
    #search-box::placeholder { color: #667; }

    /* ── Station list ───────────────────────────────────── */
    #stations { }
    #stations-info { font-size: .75rem; color: #667; margin-bottom: .4rem; }
    .station-row { display: flex; align-items: center; gap: .4rem;
                   padding: .38rem 0; border-bottom: 1px solid #0f3460; }
    .station-row:last-child { border-bottom: none; }
    .station-row.playing { background: linear-gradient(90deg,rgba(93,187,128,.08),transparent);
                            border-radius: 4px; padding-left: .35rem; }
    .station-info { flex: 1; min-width: 0; }
    .station-name { font-size: .88rem; font-weight: 500; white-space: nowrap;
                    overflow: hidden; text-overflow: ellipsis; }
    .station-meta { font-size: .7rem; color: #aaa; }
    .play-btn { background: #e94560; border: none; color: #fff; padding: .25rem .65rem;
                border-radius: 4px; cursor: pointer; font-size: .78rem; white-space: nowrap;
                flex-shrink: 0; transition: background .15s; min-width: 3.5rem; text-align: center; }
    .play-btn:hover { background: #c73652; }
    .play-btn.active { background: #1a6b3a; }
    .play-btn.active:hover { background: #e94560; }
    #load-more { display: block; width: 100%; margin-top: .7rem; background: #0f3460; border: none;
                 color: #eee; padding: .45rem; border-radius: 6px; cursor: pointer; font-size: .82rem; }
    #load-more:hover { background: #1e4080; }

    /* ── Toast ───────────────────────────────────────────── */
    #toast { position: fixed; bottom: 1rem; right: 1rem; background: #e94560; color: #fff;
             padding: .5rem .9rem; border-radius: 6px; display: none; font-size: .82rem;
             max-width: 280px; word-break: break-word; z-index: 999; }
  </style>
</head>
<body>
<header>
  <h1>&#127925; SqueezeCloud</h1>
  <span class="badge" id="srv-version">v…</span>
  <span class="badge" id="device-badge">устройство: …</span>
  <span class="badge green" id="np-badge" style="display:none">&#9654; Играе</span>
</header>

<main>

  <!-- Connection status -->
  <div class="card">
    <h2>Статус</h2>
    <div class="status-strip">
      <div class="status-item"><span class="dot" id="dot-srv"></span><span>Сървър</span></div>
      <div class="status-item"><span class="dot" id="dot-comet"></span><span id="comet-label">Squeezebox CometD</span></div>
      <div class="status-item"><span class="dot" id="dot-tcp"></span><span id="tcp-label">Squeezebox TCP</span></div>
      <div class="status-item"><span style="color:#aaa">MAC:</span>&nbsp;<span id="dev-mac">—</span></div>
    </div>
  </div>

  <!-- Now Playing -->
  <div class="card" id="np-card">
    <h2>&#9654; Сега играе</h2>
    <div class="np-inner">
      <div class="np-icon" id="np-icon">&#127925;</div>
      <div class="np-text">
        <div class="np-name" id="np-name">—</div>
        <div class="np-sub"  id="np-url">—</div>
        <div class="np-source" id="np-source"></div>
      </div>
      <div class="np-controls">
        <button class="ctrl-btn" id="btn-pause" title="Пауза / Продължи" onclick="togglePause()">&#9646;&#9646;</button>
        <button class="ctrl-btn stop-btn" title="Стоп" onclick="stopPlayback()">&#9632;</button>
        <div class="vol-row">
          <span>&#128266;</span>
          <input type="range" id="vol-slider" min="0" max="100" value="80"
                 oninput="onVolumeInput(this.value)" onchange="onVolumeCommit(this.value)">
          <span id="vol-label">80%</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Radio stations -->
  <div class="card">
    <h2>Радио станции</h2>
    <div class="genre-list" id="genre-list"><span style="color:#667">Зареждане…</span></div>
    <input type="search" id="search-box" placeholder="&#128269; Търси станция…" oninput="onSearch(this.value)">
    <div id="stations-info"></div>
    <div id="stations"><p style="color:#667">Зареждане на станции…</p></div>
    <button id="load-more" onclick="loadMore()" style="display:none">Покажи още</button>
  </div>

</main>
<div id="toast"></div>
<audio id="audio-player" preload="none"></audio>

<script>
  const audio = document.getElementById('audio-player');

  let allStations      = [];
  let filteredStations = [];
  let shownCount       = 40;

  // Playback state
  let currentUrl   = '';   // URL the HTML5 audio is playing
  let currentName  = '';
  let serverUrl    = '';   // URL the server reports as playing
  let serverName   = '';
  let deviceOnline = false; // CometD active (device sending commands)
  let tcpOnline    = false; // TCP Slim Protocol connected

  // ── Init ──────────────────────────────────────────────────────────────────
  async function init() {
    // Server version
    try {
      const r = await fetch('/api/v1/status');
      const d = await r.json();
      document.getElementById('srv-version').textContent = 'v' + d.result.version;
      document.getElementById('dot-srv').classList.add('green');
    } catch(e) { document.getElementById('dot-srv').classList.add('red'); }

    // 1. Load static stations immediately (fast=true)
    await loadStationsFast();

    // 2. Poll status every 3 seconds
    pollStatus();
    setInterval(pollStatus, 3000);

    // 3. Load full Radio Browser list in background
    setTimeout(loadStationsFull, 500);
  }

  // ── Stations ──────────────────────────────────────────────────────────────
  async function loadStationsFast() {
    try {
      const r = await fetch('/api/v1/radios?fast=true');
      const d = await r.json();
      allStations      = d.stations || [];
      filteredStations = allStations;
      renderGenres(d.genres || []);
      updateStationsInfo(allStations.length, false);
      renderStations(filteredStations.slice(0, shownCount));
      document.getElementById('load-more').style.display =
        filteredStations.length > shownCount ? 'block' : 'none';
    } catch(e) {
      document.getElementById('stations').innerHTML =
        '<p style="color:#c66">Грешка при зареждане на станции.</p>';
    }
  }

  async function loadStationsFull() {
    try {
      const r = await fetch('/api/v1/radios');
      const d = await r.json();
      if ((d.stations || []).length > allStations.length) {
        allStations      = d.stations || [];
        filteredStations = allStations;
        renderGenres(d.genres || []);
        updateStationsInfo(allStations.length, true);
        renderStations(filteredStations.slice(0, shownCount));
        document.getElementById('load-more').style.display =
          filteredStations.length > shownCount ? 'block' : 'none';
      }
    } catch(e) {}
  }

  function updateStationsInfo(count, full) {
    document.getElementById('stations-info').textContent =
      count + ' станции' + (full ? '' : ' (бързо зареждане — пълният списък се зарежда…)');
  }

  // ── Status polling ─────────────────────────────────────────────────────────
  async function pollStatus() {
    try {
      const r = await fetch('/api/v1/now_playing');
      const d = await r.json();

      deviceOnline = !!d.comet_active;
      tcpOnline    = !!d.device_connected;

      // Update status dots
      document.getElementById('dot-comet').className = 'dot ' + (deviceOnline ? 'green' : 'red');
      document.getElementById('dot-tcp').className   = 'dot ' + (tcpOnline    ? 'green' : 'orange');
      document.getElementById('comet-label').textContent =
        'Squeezebox' + (deviceOnline && d.device_mac ? ' ' + d.device_mac : ' (не свързан)');
      document.getElementById('tcp-label').textContent =
        'TCP аудио ' + (tcpOnline ? '(свързан)' : '(не свързан — локален плейър)');
      document.getElementById('dev-mac').textContent = d.device_mac || '—';
      document.getElementById('device-badge').textContent =
        'MAC: ' + (d.device_mac || '—');

      // Volume
      if (d.volume !== undefined) {
        const v = d.volume;
        document.getElementById('vol-slider').value = v;
        document.getElementById('vol-label').textContent = v + '%';
        audio.volume = v / 100;
      }

      // Now-playing from server
      const newServerUrl  = (d.mode === 'play') ? (d.url  || '') : '';
      const newServerName = (d.mode === 'play') ? (d.name || '') : '';

      if (newServerUrl !== serverUrl) {
        serverUrl  = newServerUrl;
        serverName = newServerName;

        if (serverUrl) {
          // Server started playing something (could be from Squeezebox hardware)
          showNowPlaying(serverName, serverUrl, tcpOnline ? 'device' : 'local');
          refreshRows();
          document.getElementById('np-badge').style.display = 'inline';
        } else {
          // Server stopped
          if (!currentUrl) {  // only collapse card if browser isn't also playing
            hideNowPlaying();
          }
          refreshRows();
        }
      }
    } catch(_) {}
  }

  // ── Now-Playing display ────────────────────────────────────────────────────
  function showNowPlaying(name, url, source) {
    const card = document.getElementById('np-card');
    card.classList.add('active');
    document.getElementById('np-name').textContent = name || '—';
    document.getElementById('np-url').textContent  = url;
    document.getElementById('np-icon').classList.remove('paused');
    document.getElementById('btn-pause').textContent = '⏸';
    document.getElementById('np-badge').style.display = 'inline';

    const srcEl = document.getElementById('np-source');
    if (source === 'device') {
      srcEl.className = 'np-source device';
      srcEl.textContent = '▶ Squeezebox устройство';
    } else if (source === 'browser') {
      srcEl.className = 'np-source browser';
      srcEl.textContent = '▶ Браузър (HTML5)';
    } else {
      srcEl.className = 'np-source local';
      srcEl.textContent = '▶ Локален плейър (сървър)';
    }
  }

  function hideNowPlaying() {
    document.getElementById('np-card').classList.remove('active');
    document.getElementById('np-badge').style.display = 'none';
  }

  // ── Genre filter ───────────────────────────────────────────────────────────
  function renderGenres(genres) {
    const el = document.getElementById('genre-list');
    el.innerHTML = '';
    const all = document.createElement('button');
    all.className = 'genre-btn active';
    all.textContent = 'Всички';
    all.addEventListener('click', () => {
      setActive(all);
      filteredStations = allStations;
      shownCount = 40;
      renderStations(filteredStations.slice(0, shownCount));
      document.getElementById('load-more').style.display =
        filteredStations.length > shownCount ? 'block' : 'none';
    });
    el.appendChild(all);
    genres.forEach(g => {
      const btn = document.createElement('button');
      btn.className = 'genre-btn';
      btn.textContent = g;
      btn.addEventListener('click', () => {
        setActive(btn);
        filteredStations = allStations.filter(s => (s.genre||'').toLowerCase() === g.toLowerCase());
        shownCount = 40;
        renderStations(filteredStations.slice(0, shownCount));
        document.getElementById('load-more').style.display =
          filteredStations.length > shownCount ? 'block' : 'none';
      });
      el.appendChild(btn);
    });
  }

  function setActive(btn) {
    document.querySelectorAll('.genre-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }

  function onSearch(q) {
    const lq = q.toLowerCase();
    filteredStations = lq ? allStations.filter(s => s.name.toLowerCase().includes(lq)) : allStations;
    shownCount = 40;
    renderStations(filteredStations.slice(0, shownCount));
    document.getElementById('load-more').style.display =
      filteredStations.length > shownCount ? 'block' : 'none';
  }

  function loadMore() {
    shownCount += 40;
    renderStations(filteredStations.slice(0, shownCount));
    document.getElementById('load-more').style.display =
      filteredStations.length > shownCount ? 'block' : 'none';
  }

  // ── Station list render ────────────────────────────────────────────────────
  function renderStations(stations) {
    const el = document.getElementById('stations');
    el.innerHTML = '';
    const playingUrl = serverUrl || currentUrl;
    if (!stations.length) {
      const p = document.createElement('p');
      p.style.color = '#667';
      p.textContent = 'Няма станции.';
      el.appendChild(p);
      return;
    }
    stations.forEach(s => {
      const isPlaying = s.url === playingUrl;
      const row = document.createElement('div');
      row.className = 'station-row' + (isPlaying ? ' playing' : '');
      row.dataset.url = s.url;

      const info = document.createElement('div');
      info.className = 'station-info';
      const nameEl = document.createElement('div');
      nameEl.className = 'station-name';
      nameEl.textContent = s.name || '';
      const metaEl = document.createElement('div');
      metaEl.className = 'station-meta';
      metaEl.textContent = [s.genre, s.country, s.bitrate ? s.bitrate + ' kbps' : '']
        .filter(Boolean).join(' • ');
      info.appendChild(nameEl);
      info.appendChild(metaEl);

      const btn = document.createElement('button');
      btn.className = 'play-btn' + (isPlaying ? ' active' : '');
      btn.textContent = isPlaying ? '■ Стоп' : '▶ Пусни';
      btn.addEventListener('click', () => {
        if (isPlaying) stopPlayback();
        else playStation(s.url, s.name);
      });

      row.appendChild(info);
      row.appendChild(btn);
      el.appendChild(row);
    });
  }

  function refreshRows() {
    const playingUrl = serverUrl || currentUrl;
    document.querySelectorAll('.station-row').forEach(row => {
      const url = row.dataset.url;
      const btn = row.querySelector('.play-btn');
      if (!btn) return;
      const active = url === playingUrl;
      row.classList.toggle('playing', active);
      btn.classList.toggle('active', active);
      btn.textContent = active ? '■ Стоп' : '▶ Пусни';
    });
  }

  // ── Playback ───────────────────────────────────────────────────────────────
  async function playStation(url, name) {
    // POST to server — triggers Squeezebox strm OR local player
    try {
      await fetch('/api/v1/play', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url, name}),
      });
    } catch(_) {}

    // Also start HTML5 audio in the browser
    currentUrl  = url;
    currentName = name || url;
    const proxyUrl = '/stream?url=' + encodeURIComponent(url);
    audio.src = proxyUrl;
    audio.volume = document.getElementById('vol-slider').value / 100;
    audio.play().catch(() => showToast('Натиснете ▶ Пусни отново (autoplay блокиран)'));

    serverUrl  = url;
    serverName = name;
    showNowPlaying(name || url, url, 'browser');
    refreshRows();
    showToast('▶ ' + (name || url));
  }

  function togglePause() {
    if (audio.paused) {
      audio.play();
      document.getElementById('np-icon').classList.remove('paused');
      document.getElementById('btn-pause').textContent = '⏸';
    } else {
      audio.pause();
      document.getElementById('np-icon').classList.add('paused');
      document.getElementById('btn-pause').textContent = '▶';
    }
  }

  async function stopPlayback() {
    audio.pause();
    audio.src = '';
    currentUrl = '';
    currentName = '';
    serverUrl  = '';
    serverName = '';
    hideNowPlaying();
    refreshRows();
    try { await fetch('/api/v1/stop', {method: 'POST'}); } catch(_) {}
    showToast('■ Стоп');
  }

  // ── Volume ─────────────────────────────────────────────────────────────────
  let _volTimer = null;
  function onVolumeInput(v) {
    audio.volume = v / 100;
    document.getElementById('vol-label').textContent = v + '%';
  }
  function onVolumeCommit(v) {
    // Debounce: only send to server after slider settles
    clearTimeout(_volTimer);
    _volTimer = setTimeout(() => {
      fetch('/api/v1/volume', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({level: parseInt(v)}),
      }).catch(() => {});
    }, 300);
  }

  // ── Toast ──────────────────────────────────────────────────────────────────
  function showToast(text) {
    const el = document.getElementById('toast');
    el.textContent = text;
    el.style.display = 'block';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.display = 'none'; }, 3000);
  }

  audio.addEventListener('error', () => showToast('⚠ Грешка при зареждане на потока'));

  init();
</script>
</body>
</html>
"""


@app.get("/webcontrol")
async def webcontrol():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=_WEBCONTROL_HTML)
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/apps")
@app.get("/apps")
async def get_apps():
    return {
        "status": "ok",
        "result": {
            "item_loop": [
                {"id": "radio",    "text": "Internet Radio", "type": "app"},
                {"id": "podcasts", "text": "Podcasts",        "type": "app"},
                {"id": "weather",  "text": "Weather",         "type": "app"},
                {"id": "news",     "text": "News",            "type": "app"},
            ],
            "count": 4,
            "offset": 0,
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
        log.debug("[Comet] ← %s | data=%s", ch, json.dumps(data)[:200])

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
            if isinstance(resp, list):
                responses.extend(resp)
            else:
                responses.append(resp)
            log.debug("[Comet] → %s", channel)

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
                    "advice": {"timeout": 0, "interval": 0, "reconnect": "retry"},
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
    log.debug("[Comet] GET /cometd от %s", request.client)
    return JSONResponse([{"channel": "/meta/connect", "successful": True}])


async def _handle_comet_message(msg: dict, channel: str) -> dict:
    global _device_mac, _status_channel, _comet_last_seen
    _comet_last_seen = time.time()
    # ── /meta/handshake ──────────────────────────────────────────────────────
    if channel == "/meta/handshake":
        client_id = _new_client_id()
        _comet_sessions[client_id] = {"subscriptions": [], "mac": ""}
        # Извлечи MAC от ext ако е наличен
        ext = msg.get("ext", {})
        if ext.get("mac"):
            _device_mac = ext["mac"]
            log.info("[Comet] MAC от handshake: %s", _device_mac)
        return {
            "channel": "/meta/handshake",
            "version": "1.0",
            "minimumVersion": "1.0",
            "supportedConnectionTypes": ["long-polling", "streaming"],
            "clientId": client_id,
            "successful": True,
            "advice": {
                "timeout": 0,        # polling mode: respond immediately (SKILL rule #9)
                "interval": 5000,    # 5s between reconnect attempts (FIRMWARE_ANALYSIS §3.1)
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
            "advice": {"timeout": 0, "interval": 0, "reconnect": "retry"},
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
            "advice": {"timeout": 0, "interval": 0, "reconnect": "retry"},
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

        msgs = [{
            "channel": response_channel,
            "clientId": client_id,
            "successful": True,
            "data": result,
            "id": msg.get("id", ""),
        }]

        # After playlist play/stop push updated status to the NowPlaying subscription
        if command == "playlist" and len(cmd) > 1 and str(cmd[1]).lower() in ("play", "stop", "clear", "pause"):
            # Look up by session first, then fall back to the global status channel.
            # The global fallback handles the case where clientId is empty in the play request.
            status_channel = (
                _comet_sessions.get(client_id, {}).get("status_channel")
                or _status_channel
            )
            if status_channel:
                mac = player_mac or _device_mac
                status_data = await dispatch_rpc("status", ["status"], mac)
                msgs.append({
                    "channel": status_channel,
                    "id": f"push-{msg.get('id', '')}",
                    "data": status_data,
                })
                log.info("[Comet] → status push → %s (mode=%s)", status_channel, status_data.get("mode"))
            else:
                log.warning("[Comet] status_channel not known yet — NowPlaying will not update")

        return msgs

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
            log.debug("[serverstatus] via subscribe, mac=%s", mac)
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

        # Store the status subscription channel so we can push updates after play commands
        if command == "status":
            _status_channel = response_channel
            if client_id in _comet_sessions:
                _comet_sessions[client_id]["status_channel"] = response_channel
            log.info("[Comet] status_channel saved: %s", response_channel)

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

    log.debug("[RPC] mac=%s cmd=%s", player_mac, cmd)

    result = await dispatch_rpc(command, cmd, player_mac)

    return {"id": rpc_id, "method": "slim.request", "result": result}


# ═════════════════════════════════════════════════════════════════════════════
# PLAY FROM PHONE / REMOTE PLAY
# Телефонът (или всяко устройство в мрежата) може да изпрати POST заявка
# към /api/v1/play с {"url": "...", "name": "..."} и Squeezebox ще пусне URL-а.
# Следващата поллинг заявка за "status" ще върне mode:"play" с новото URL.
#
# SPOTIFY:
# Spotify Connect изисква регистрирана Spotify Premium сметка и librespot.
# За "play from phone" без Premium — изпрати директен stream URL (напр. от
# youtube-dl/yt-dlp) към /api/v1/play.
# Пример от телефон:
#   curl -X POST http://<server-ip>:9000/api/v1/play \
#        -H "Content-Type: application/json" \
#        -d '{"url":"https://stream.nrj.bg/nrj-128.mp3","name":"NRJ Bulgaria"}'
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/api/v1/play")
async def play_from_phone(request: Request):
    """
    Пусни URL на Squeezebox/локален плейър от уеб UI или телефон.
    Body: {"url": "stream-url", "name": "Station Name"}
    """
    global _now_playing
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "Invalid JSON"}, status_code=400)

    url  = (body.get("url") or "").strip()
    name = (body.get("name") or "").strip()
    if not url:
        return JSONResponse({"status": "error", "error": "url is required"}, status_code=400)

    _now_playing = {"url": url, "name": name, "started_at": time.time()}
    log.info("[play-from-web] %s  (%s)", name, url)
    # Send to Squeezebox (TCP strm) or local player
    asyncio.create_task(_send_strm_play(url, name))
    return {"status": "ok", "playing": {"url": url, "name": name}}


@app.get("/api/v1/now_playing")
async def now_playing_status():
    """Върни текущо пусканото + статус на свързване."""
    device_connected = _slim_writer is not None
    comet_active = (time.time() - _comet_last_seen) < 30 if _comet_last_seen else False
    if _now_playing:
        return {
            "status": "ok",
            "mode": "play",
            "url":  _now_playing.get("url"),
            "name": _now_playing.get("name"),
            "started_at": _now_playing.get("started_at"),
            "volume": _player_volume,
            "device_connected": device_connected,
            "comet_active": comet_active,
            "device_mac": _device_mac,
        }
    return {
        "status": "ok",
        "mode": "stop",
        "volume": _player_volume,
        "device_connected": device_connected,
        "comet_active": comet_active,
        "device_mac": _device_mac,
    }


@app.post("/api/v1/stop")
async def stop_playback():
    """Спри пускането — изпраща strm q до Squeezebox и/или спира локалния плейър."""
    global _now_playing
    _now_playing = {}
    log.info("[stop] Пускането спряно от уеб UI")
    asyncio.create_task(_send_strm_stop())
    return {"status": "ok", "mode": "stop"}


@app.get("/api/v1/volume")
async def get_volume():
    """Върни текущата сила на звука (0-100)."""
    return {"status": "ok", "volume": _player_volume}


@app.post("/api/v1/volume")
async def set_volume(request: Request):
    """Задай сила на звука. Body: {"level": 0-100}"""
    global _player_volume
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "Invalid JSON"}, status_code=400)
    level = max(0, min(100, int(body.get("level", _player_volume))))
    _player_volume = level
    asyncio.create_task(_send_audg(level))
    log.info("[volume] Зададен: %d%%", level)
    return {"status": "ok", "volume": level}


async def _send_audg(level: int):
    """Send audg (audio gain) command to Squeezebox for volume control."""
    global _slim_writer
    if not _slim_writer:
        return
    # gain is a 32-bit fixed-point: 65536 = 100% (0 dB)
    gain = max(0, min(65536, int(level * 65536 / 100)))
    try:
        data = struct.pack("!IIBB", gain, gain, 1, 255) + b"\x00"
        _slim_send(_slim_writer, b"audg", data)
        await _slim_writer.drain()
        log.debug("[audg] volume → %d%%  (gain=%d)", level, gain)
    except Exception as e:
        log.warning("[audg] Грешка: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# STATIONS — управление на потребителски станции (stations.json)
# GET  /api/v1/stations          → списък на потребителски станции
# POST /api/v1/stations          → добави станция
# DELETE /api/v1/stations/{idx}  → изтрий станция по индекс
# ═════════════════════════════════════════════════════════════════════════════

def _save_custom_stations():
    path = _pathlib.Path(_os.path.dirname(_os.path.abspath(__file__))) / "stations.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(CUSTOM_STATIONS, f, ensure_ascii=False, indent=2)


@app.get("/api/v1/stations")
async def list_custom_stations():
    return {"status": "ok", "count": len(CUSTOM_STATIONS), "stations": CUSTOM_STATIONS}


@app.post("/api/v1/stations")
async def add_custom_station(request: Request):
    """Добави станция към stations.json. Body: {"name":"...","url":"...","genre":"...","country":"..."}"""
    global CUSTOM_STATIONS
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "Invalid JSON"}, status_code=400)

    url  = (body.get("url") or "").strip()
    name = (body.get("name") or "").strip()
    if not url or not name:
        return JSONResponse({"status": "error", "error": "name and url are required"}, status_code=400)

    station = {
        "name": name,
        "url": url,
        "genre": (body.get("genre") or "Music").strip(),
        "country": (body.get("country") or "").strip(),
    }
    CUSTOM_STATIONS.append(station)
    _save_custom_stations()
    # Invalidate station cache so new station appears immediately
    _cache.pop("stations:all", None)
    log.info("[stations] Добавена: %s (%s)", name, url)
    return {"status": "ok", "station": station, "total": len(CUSTOM_STATIONS)}


@app.delete("/api/v1/stations/{idx}")
async def delete_custom_station(idx: int):
    global CUSTOM_STATIONS
    if idx < 0 or idx >= len(CUSTOM_STATIONS):
        return JSONResponse({"status": "error", "error": "Index out of range"}, status_code=404)
    removed = CUSTOM_STATIONS.pop(idx)
    _save_custom_stations()
    _cache.pop("stations:all", None)
    log.info("[stations] Изтрита: %s", removed.get("name"))
    return {"status": "ok", "removed": removed, "total": len(CUSTOM_STATIONS)}


@app.get("/stream")
async def stream_proxy(url: str):
    """
    HTTP audio stream proxy for Squeezebox playback.
    The strm-start command points the device here so we can handle HTTPS,
    redirects and other protocol details on the server side.
    The device connects via plain HTTP; we fetch the real stream behind it.
    """
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https"):
        return JSONResponse({"error": "only http/https streams allowed"}, status_code=400)

    log.info("[stream-proxy] → %s", url[:100])

    async def generate():
        upstream_content_type = "audio/mpeg"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15.0, read=None, write=None, pool=None),
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url, headers={
                    "User-Agent": "Squeezebox/7.7.3",
                    "Accept": "*/*",
                    # Do NOT request Icy-MetaData — keeps the byte stream clean
                    # (the Squeezebox expects icy-metaint header if metadata is embedded)
                }) as resp:
                    upstream_content_type = resp.headers.get("content-type", "audio/mpeg").split(";")[0].strip()
                    async for chunk in resp.aiter_bytes(8192):
                        yield chunk
        except Exception as e:
            log.warning("[stream-proxy] Stream error: %s", e)

    # Determine media type before streaming (best effort — use URL heuristic first)
    url_lower = url.lower()
    if any(x in url_lower for x in ("_opus", ".opus", "ogg")):
        media_type = "audio/ogg"
    elif any(x in url_lower for x in ("_aac", ".aac", ".m3u8")):
        media_type = "audio/aac"
    else:
        media_type = "audio/mpeg"

    from starlette.responses import StreamingResponse as _SR
    return _SR(
        generate(),
        media_type=media_type,
        headers={
            "Cache-Control": "no-cache",
            "Accept-Ranges": "none",
            "icy-name": "Radio",
            "icy-br": "128",
        },
    )


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def catch_all(request: Request, path: str):
    """Логва всички непознати endpoints"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    log.debug("[???] %s /%s | body=%s", request.method, path, json.dumps(body)[:300])
    return JSONResponse({"status": "ok", "result": []})


def _home_menu_items() -> list:
    """
    Returns the list of home-menu items sent via the 'menu' command and pushed
    via the 'menustatus' Comet channel.

    IMPORTANT – Lua treats the number 0 as truthy, so we must NEVER include
    "isANode": 0 in non-node items.  Only node items carry "isANode": 1.
    All items that should appear as clickable entries must omit the isANode key.

    The id "radios" is explicitly ignored by SlimMenusApplet ("shown locally"),
    so we use "radio" (without the trailing 's') for our Internet Radio entry.

    Items with isApp:1 are automatically placed under the "My Apps" node AND
    copied to the home menu by the firmware (addAppToHome logic).  This gives
    us both a "My Apps" section and direct shortcuts on the home screen.
    """
    return [
        {
            "id": "favorites",
            "node": "home",
            "text": "Favorites",
            "iconStyle": "hm_favorites",
            "weight": 10,
            "actions": {
                "go": {"cmd": ["favorites", 0, 100], "player": 0}
            },
            "window": {"windowId": "favorites"},
        },
        {
            # isApp:1 → item is placed under "My Apps" node AND copied to home
            "id": "squeezecloudRadio",
            "node": "home",
            "text": "Internet Radio",
            "iconStyle": "hm_radio",
            "isApp": 1,
            "weight": 20,
            "actions": {
                "go": {"cmd": ["radios", 0, 100], "player": 0}
            },
            "window": {"windowId": "squeezecloudRadio"},
        },
        {
            "id": "squeezecloudPodcasts",
            "node": "home",
            "text": "Podcasts",
            "iconStyle": "hm_podcast",
            "isApp": 1,
            "weight": 40,
            "actions": {
                "go": {"cmd": ["podcasts", 0, 100], "player": 0}
            },
            "window": {"windowId": "squeezecloudPodcasts"},
        },
        {
            "id": "squeezecloud_weather",
            "node": "home",
            "text": "Weather & News",
            "iconStyle": "hm_weather",
            "weight": 50,
            "actions": {
                "go": {"cmd": ["weather"], "player": 0}
            },
            "window": {"windowId": "squeezecloud_weather"},
        },
    ]


async def dispatch_rpc(command: str, cmd: list, player_mac: str):
    global _now_playing
    if command == "serverstatus":
        mac = _device_mac
        log.debug("[serverstatus] via dispatch, mac=%s", mac)
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
        mac = player_mac or _device_mac
        return {
            "count": 1,
            "players_loop": [{
                "playerid": mac,
                "name": "Squeezebox Radio",
                "model": "squeezebox_radio",
                "isplaying": 1 if _now_playing else 0,
                "connected": 1,
            }]
        }

    elif command == "status":
        mac = player_mac or _device_mac
        if _now_playing:
            mode = "play"
            track_name = _now_playing.get("name", "")
            track_url  = _now_playing.get("url", "")
            playlist_loop = [{
                "id": "current",
                "title": track_name,
                # current_title is the field Jive reads for remote/radio streams
                "current_title": track_name,
                "url": track_url,
                "duration": 0,
                "remote": 1,
                "remote_title": track_name,
            }]
            remote_meta = {"title": track_name, "url": track_url}
        else:
            mode = "stop"
            playlist_loop = []
            remote_meta = {}
        return {
            "playerid": mac,
            "name": "Squeezebox Radio",
            "mode": mode,
            "mixer_volume": 50,
            "playlist_cur_index": 0,
            "playlist_tracks": len(playlist_loop),
            "playlist_timestamp": _now_playing.get("started_at", time.time()),
            "playlist_loop": playlist_loop,
            "remoteMeta": remote_meta,
        }

    elif command == "playlist":
        # cmd = ["playlist", "play"|"stop"|"clear", url, name]
        sub = str(cmd[1]).lower() if len(cmd) > 1 else ""
        if sub == "play" and len(cmd) > 2:
            original_url = cmd[2]
            name = cmd[3] if len(cmd) > 3 else ""
            # Proxy HTTPS streams through our local HTTP server.
            # The Squeezebox Radio firmware (7.7.x) does not support TLS natively,
            # so HTTPS URLs must be transparently proxied as plain HTTP.
            _now_playing = {"url": original_url, "name": name, "started_at": time.time()}
            log.info("[playlist] Играе: %s  (%s)", name, original_url)
            # _send_strm_play always routes via our /stream proxy so the device
            # only ever connects to our server for audio (handles HTTPS + redirects)
            asyncio.create_task(_send_strm_play(original_url, name))
        elif sub in ("stop", "clear", "pause"):
            _now_playing = {}
            log.info("[playlist] Спряно")
            asyncio.create_task(_send_strm_stop())
        return {"ok": 1, "mode": "play" if _now_playing else "stop"}

    elif command in ("radios", "radio"):
        start = int(cmd[1]) if len(cmd) > 1 else 0
        count = int(cmd[2]) if len(cmd) > 2 else 10

        # Detect genre drill-down: SlimBrowser appends "item_id:genre:Rock" when user
        # selects a genre node, matching the same pattern used by the podcasts handler.
        item_id = next((c for c in cmd if isinstance(c, str) and c.startswith("item_id:")), None)

        stations = await get_radio_stations()

        if item_id and item_id.startswith("item_id:genre:"):
            # Drill into a specific genre (or "All" for the full list)
            genre = item_id[len("item_id:genre:"):]
            filtered = stations if genre == "All" else [
                s for s in stations
                if (s.get("genre") or "Music").lower() == genre.lower()
            ]
            slice_ = filtered[start:start + count]

            return {
                "count": len(filtered),
                "offset": start,
                "item_loop": [
                    {
                        "id": f"radio:{start + i}",
                        "text": s["name"],
                        "type": "audio",
                        "url": s["url"],
                        "isaudio": 1,
                        "actions": {
                            "go": {
                                "player": 0,
                                "cmd": ["playlist", "play", s["url"], s["name"]],
                                "nextWindow": "nowPlaying",
                            }
                        },
                    }
                    for i, s in enumerate(slice_)
                ],
            }
        else:
            # Top level — show genre nodes so the user can browse by genre.
            # Build a de-duplicated, sorted genre list from all available stations.
            genres = sorted(set((s.get("genre") or "Music") for s in stations))
            genre_items = [
                {
                    "id": f"genre:{g}",
                    "text": g,
                    "item_id": f"genre:{g}",
                    "hasitems": 1,
                    "type": "playlist",
                    "actions": {
                        "go": {
                            "player": 0,
                            "cmd": ["radios", 0, 100, f"item_id:genre:{g}"],
                        }
                    },
                }
                for g in genres
            ]
            # Prepend an "All Stations" shortcut
            all_item = {
                "id": "genre:All",
                "text": "All Stations",
                "item_id": "genre:All",
                "hasitems": 1,
                "type": "playlist",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["radios", 0, 100, "item_id:genre:All"],
                    }
                },
            }
            items = ([all_item] + genre_items)[start:start + count]
            total = len(genres) + 1  # genres + "All Stations"
            return {
                "count": total,
                "offset": start,
                "item_loop": items,
            }

    elif command == "podcasts":
        start = int(cmd[1]) if len(cmd) > 1 else 0
        count = int(cmd[2]) if len(cmd) > 2 else 10
        item_id = next((c for c in cmd if isinstance(c, str) and "item_id:" in c), None)

        if not item_id:
            return {
                "count": len(PODCAST_FEEDS),
                "offset": 0,
                "item_loop": [
                    {
                        "id": f"podcast:{i}",
                        "text": f["name"],
                        "type": "playlist",
                        "hasitems": 1,
                        "item_id": f"podcast:{i}",
                        "actions": {
                            "go": {
                                "player": 0,
                                "cmd": ["podcasts", 0, 100, f"item_id:podcast:{i}"],
                            }
                        },
                    }
                    for i, f in enumerate(PODCAST_FEEDS)
                ]
            }
        else:
            feed_idx = int(item_id.replace("item_id:podcast:", ""))
            feed = PODCAST_FEEDS[feed_idx]
            episodes = await fetch_podcast_episodes(feed)
            slice_ = episodes[start:start + count]
            return {
                "count": len(episodes),
                "offset": start,
                "item_loop": [
                    {
                        "id": f"ep:{feed_idx}:{i}",
                        "text": ep["title"],
                        "type": "audio",
                        "url": ep["url"],
                        "isaudio": 1,
                        "actions": {
                            "go": {
                                "player": 0,
                                "cmd": ["playlist", "play", ep["url"], ep["title"]],
                                "nextWindow": "nowPlaying",
                            }
                        },
                    }
                    for i, ep in enumerate(slice_)
                ]
            }

    elif command == "menu":
        items = _home_menu_items()
        return {
            "count": len(items),
            "offset": 0,
            "item_loop": items,
        }

    elif command == "menustatus":
        # Push initial menu items via the menustatus Comet channel.
        # Lua reads chunk.data[2]=items, chunk.data[3]=directive, chunk.data[4]=playerId
        # (Lua arrays are 1-indexed, so JSON index 0 → Lua index 1, etc.)
        mac = player_mac or _device_mac
        return [mac, _home_menu_items(), "add", mac]

    elif command == "register":
        # Отговаряме с goNow="home" за да накараме Jive браузъра да се прехвърли
        # директно към началния екран без потребителско взаимодействие.
        # item_loop с nextWindow="home" и serverLinked=1 осигурява резервен вариант:
        # ако goNow не работи, потребителят вижда бутон "Connected" и може да го натисне.
        # pin: False сигнализира на SlimServer.lua че плейърът е вече свързан (не е нужен PIN низ).
        return {
            "count": 1,
            "offset": 0,
            "item_loop": [
                {
                    "text": "Connected to SqueezeCloud",
                    "nextWindow": "home",
                    "serverLinked": 1,
                    "style": "item",
                }
            ],
            "goNow": "home",
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
            "offset": start,
            "item_loop": [
                {
                    "id": f"fav:{start + i}",
                    "text": s["name"],
                    "url": s["url"],
                    "type": "audio",
                    "isaudio": 1,
                    "actions": {
                        "go": {
                            "player": 0,
                            "cmd": ["playlist", "play", s["url"], s["name"]],
                            "nextWindow": "nowPlaying",
                        }
                    },
                }
                for i, s in enumerate(slice_)
            ]
        }

    elif command == "weather":
        w = await fetch_weather()
        return {
            "count": 1,
            "offset": 0,
            "item_loop": [{"id": "weather:current", "text": w["summary"], "type": "text"}]
        }

    elif command == "news":
        items = await fetch_news_items(NEWS_FEEDS[0])
        return {
            "count": len(items),
            "offset": 0,
            "item_loop": [
                {"id": f"news:{i}", "text": item["title"], "type": "text"}
                for i, item in enumerate(items[:10])
            ]
        }

    elif command == "apps":
        app_items = _home_menu_items()
        return {
            "count": len(app_items),
            "offset": 0,
            "item_loop": app_items,
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
    fast: bool = False,
):
    if fast:
        # Return built-in stations immediately, trigger background Radio Browser fetch
        stations = list(CUSTOM_STATIONS) + list(STATIC_STATIONS)
        asyncio.create_task(_bg_prefetch_stations())
    else:
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
        "stations": stations[:300],
    }


_bg_prefetching = False


async def _bg_prefetch_stations():
    """Populate Radio Browser cache in the background."""
    global _bg_prefetching
    if _bg_prefetching or cache_get("stations:all"):
        return
    _bg_prefetching = True
    try:
        await get_radio_stations()
    finally:
        _bg_prefetching = False


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
                        "reverse": "true"},
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
                    # Exclude HLS (.m3u8) streams — Squeezebox Radio firmware does not
                    # support HTTP Live Streaming; only direct MP3/AAC/OGG streams work.
                    and not s["url_resolved"].lower().endswith(".m3u8")
                ]
    except Exception as e:
        log.warning("Radio Browser API error: %s", e)

    all_stations = CUSTOM_STATIONS + STATIC_STATIONS + live
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
        log.warning("News fetch error: %s", e)
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
        log.warning("Podcast fetch error: %s", e)
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


# ── Slim Protocol strm helpers ────────────────────────────────────────────────

async def _send_strm_play(url: str, name: str):
    """Send strm start to connected Squeezebox, or start local software player."""
    global _slim_writer, _local_ip

    # If TCP isn't connected yet but CometD is active, wait for it.
    # The device connects TCP (3483) and CometD (9000) nearly simultaneously;
    # give it up to 10 seconds to establish the binary SlimProto connection.
    if not _slim_writer and (time.time() - _comet_last_seen) < 30:
        for _ in range(20):          # up to 10 seconds
            await asyncio.sleep(0.5)
            if _slim_writer:
                log.info("[strm] TCP з'явився після очікування — продовжуємо")
                break
        if not _slim_writer:
            log.warning(
                "[strm] ⚠️  TCP 3483 не свързан след 10s.\n"
                "  Станцията '%s' е ЗАПАЗЕНА — ще се пусне АВТОМАТИЧНО при TCP reconnect.\n"
                "  Ако след 60s не свири, провери:\n"
                "  • Windows Firewall → Inbound Rules → TCP 3483 (LISTENING)\n"
                "  • /mnt/storage/etc/hosts на устройството → www.squeezenetwork.com = %s\n"
                "  • Рестартирай устройството за да принудиш TCP reconnect",
                name, _local_ip
            )

    if _slim_writer:
        # ── Physical Squeezebox connected — send strm TCP command ─────────────
        server_ip = _local_ip if _local_ip not in ("127.0.0.1", "") else get_local_ip()
        proxy_path = f"/stream?url={urllib.parse.quote(url, safe='')}"
        http_req = (
            f"GET {proxy_path} HTTP/1.0\r\n"
            f"Host: {server_ip}:9000\r\n"
            f"Accept: */*\r\n"
            f"User-Agent: Squeezebox/7.7.3\r\n"
            f"\r\n"
        ).encode("utf-8")
        ip_bytes = bytes(int(p) for p in server_ip.split("."))
        port_bytes = struct.pack("!H", 9000)
        # Detect format: 'm'=mp3 works for most stations; use 'a' for AAC, 'o' for ogg/opus
        url_lower = url.lower()
        if any(x in url_lower for x in ("_aac", ".aac", "aac-", "/aac", "format=aac")):
            fmt = ord('a')
        elif any(x in url_lower for x in ("_opus", ".opus", "opus-", "/opus", "ogg", "icecast.walmradio")):
            fmt = ord('o')   # ogg container (opus/vorbis)
        else:
            fmt = ord('m')   # default: mp3
        try:
            _slim_send(_slim_writer, b"strm", b"q" + b"\x00" * 24)
            await _slim_writer.drain()
            await asyncio.sleep(0.05)
            strm_start = bytes([
                ord('s'), ord('1'), fmt,
                ord('?'), ord('?'), ord('?'), ord('?'),
                255, 0, 0, ord('0'), 0, 0, 0,
                0, 0, 0, 0,
            ]) + port_bytes + ip_bytes + http_req
            _slim_send(_slim_writer, b"strm", strm_start)
            await _slim_writer.drain()
            log.info("[strm] ✓ strm start (fmt=%s) → %s  (%s)", chr(fmt), name, url[:80])
        except Exception as e:
            log.error("[strm] Грешка при изпращане на strm start: %s", e)
            _slim_writer = None   # connection is broken; clear so next play retries
    else:
        # ── No physical device — CometD tells us if the device is visible ─────
        if (time.time() - _comet_last_seen) < 30:
            # Device is reachable via CometD but TCP 3483 is not open yet.
            # DO NOT start local player — user explicitly wants Squeezebox playback.
            # _now_playing is still set → HELO auto-replay will fire when TCP reconnects.
            log.warning(
                "[strm] Станцията '%s' е ЗАПАЗЕНА. Изчакване на TCP reconnect...\n"
                "  Устройството свири АВТОМАТИЧНО щом свърже TCP 3483.",
                name
            )
        else:
            log.info("[player] Няма Squeezebox — пускам локално: %s", name)
            await _start_local_player(url, name)


def _find_executable(name: str) -> Optional[str]:
    """Find an executable by name in PATH and common Windows install locations."""
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        import glob as _glob
        # Common fixed install locations
        common = [
            rf"C:\ffmpeg\bin\{name}.exe",
            rf"C:\Program Files\ffmpeg\bin\{name}.exe",
            rf"C:\Program Files (x86)\ffmpeg\bin\{name}.exe",
            rf"C:\tools\ffmpeg\bin\{name}.exe",
            rf"{os.environ.get('LOCALAPPDATA','')}\Programs\ffmpeg\bin\{name}.exe",
            rf"{os.environ.get('APPDATA','')}\ffmpeg\bin\{name}.exe",
        ]
        for p in common:
            if p and os.path.isfile(p):
                return p
        # WinGet installs to a hashed path — search with glob
        winget_base = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft", "WinGet", "Packages",
        )
        if os.path.isdir(winget_base):
            # e.g. Gyan.FFmpeg_Microsoft.Winget.Source_*/ffmpeg-*/bin/ffplay.exe
            patterns = [
                os.path.join(winget_base, "*ffmpeg*", "*", "bin", f"{name}.exe"),
                os.path.join(winget_base, "*ffmpeg*", "bin", f"{name}.exe"),
            ]
            for pat in patterns:
                matches = _glob.glob(pat, recursive=False)
                if matches:
                    return matches[0]
    return None


async def _start_local_player(url: str, name: str):
    """Start a local player: tries ffplay/mpv/vlc, then Windows PowerShell WMP,
    then pure-Python Windows MCI, in that order."""
    global _local_player_proc, _local_ip
    await _stop_local_player()

    server_ip = _local_ip if _local_ip not in ("127.0.0.1", "") else get_local_ip()
    proxy_url = f"http://{server_ip}:9000/stream?url={urllib.parse.quote(url, safe='')}"

    # ── 1. Subprocess candidates ──────────────────────────────────────────────
    # Resolve full paths first so we get a useful log message if not found
    candidates = []
    for prog, args in [
        ("ffplay", ["-nodisp", "-loglevel", "quiet", proxy_url]),
        ("mpv",    ["--no-video", "--really-quiet", proxy_url]),
        ("vlc",    ["--intf", "dummy", "--quiet", proxy_url]),
    ]:
        path = _find_executable(prog)
        if path:
            candidates.append([path] + args)
        else:
            log.debug("[player] %s не е намерен в PATH", prog)

    # Windows-native: PowerShell + Windows Media Player COM (no install needed)
    if sys.platform == "win32":
        # WMPlayer.OCX.7 = Windows Media Player ActiveX — plays HTTP streams headlessly
        ps_wmp = (
            "$wmp = New-Object -ComObject 'WMPlayer.OCX.7'; "
            "$wmp.settings.volume = 80; "
            f"$wmp.URL = '{proxy_url}'; "
            "$wmp.controls.play(); "
            "Start-Sleep -Seconds 86400"
        )
        ps_path = _find_executable("powershell") or "powershell"
        candidates.append([
            ps_path, "-WindowStyle", "Hidden", "-NonInteractive",
            "-Command", ps_wmp,
        ])

    for cmd in candidates:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Short wait to detect immediate crash
            await asyncio.sleep(0.5)
            if proc.returncode is None:  # still running → success
                _local_player_proc = proc
                log.info("[player] ✓ %s стартиран: %s", cmd[0], name)
                return
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("[player] %s грешка: %s", cmd[0], e)

    # ── 2. Pure-Python fallback: sounddevice + miniaudio ─────────────────────
    if await _start_python_audio(proxy_url, name):
        return

    # ── 3. Nothing worked ─────────────────────────────────────────────────────
    ffplay_path = _find_executable("ffplay")
    if ffplay_path:
        log.warning("[player] ffplay намерен в %s но не успя да стартира!", ffplay_path)
    else:
        log.warning(
            "[player] ffplay не е намерен. "
            "Ако ffmpeg е инсталиран, добавете го в PATH: "
            "Старт → 'Редактирай системни ENV' → Path → Добави C:\\ffmpeg\\bin  "
            "ИЛИ инсталирайте: winget install Gyan.FFmpeg  "
            "Можете да слушате в браузъра: http://%s:9000/webcontrol",
            server_ip,
        )


async def _start_python_audio(url: str, name: str) -> bool:
    """Windows-native audio via ctypes winmm MCI (always available on Windows).
    Returns True if playback started successfully."""
    global _python_audio_stop, _python_audio_thread

    if sys.platform != "win32":
        return False

    stop_event = threading.Event()

    def _mci_play():
        try:
            import ctypes
            winmm = ctypes.windll.winmm

            # Close any leftover session from a previous play
            winmm.mciSendStringW("close radio", None, 0, None)

            # Open the HTTP stream via the Windows Media Player MCI device
            open_cmd = f'open "{url}" type mpegvideo alias radio'
            rc = winmm.mciSendStringW(open_cmd, None, 0, None)
            if rc != 0:
                log.warning("[player] MCI open failed (rc=%d) — перепробвайте с ffmpeg или web UI", rc)
                return

            winmm.mciSendStringW("play radio", None, 0, None)
            log.info("[player] ✓ Windows MCI стартира: %s", name)

            while not stop_event.is_set():
                time.sleep(1)

            winmm.mciSendStringW("stop radio",  None, 0, None)
            winmm.mciSendStringW("close radio", None, 0, None)
        except Exception as e:
            log.warning("[player] MCI грешка: %s", e)

    t = threading.Thread(target=_mci_play, daemon=True, name="mci-audio")
    t.start()

    # Wait briefly to see if MCI initialized successfully
    await asyncio.sleep(1.2)
    if t.is_alive():
        _python_audio_stop  = stop_event
        _python_audio_thread = t
        return True

    return False


async def _stop_local_player():
    """Terminate any running local player (subprocess or Python thread)."""
    global _local_player_proc, _python_audio_stop, _python_audio_thread

    if _local_player_proc:
        try:
            _local_player_proc.terminate()
            await asyncio.wait_for(_local_player_proc.wait(), timeout=3.0)
        except Exception:
            try:
                _local_player_proc.kill()
            except Exception:
                pass
        _local_player_proc = None
        log.info("[player] Subprocess плейър спрян")

    if _python_audio_stop:
        _python_audio_stop.set()
        _python_audio_stop = None
        if _python_audio_thread and _python_audio_thread.is_alive():
            _python_audio_thread.join(timeout=2.0)
        _python_audio_thread = None
        log.info("[player] Python audio плейър спрян")


async def _send_strm_stop():
    """Stop playback — Squeezebox strm q or local player subprocess."""
    global _slim_writer
    if _slim_writer:
        try:
            _slim_send(_slim_writer, b"strm", b"q" + b"\x00" * 24)
            await _slim_writer.drain()
            log.info("[strm] ✓ strm stop")
        except Exception as e:
            log.error("[strm] Грешка при изпращане на strm stop: %s", e)
    await _stop_local_player()


async def slim_handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    log.info("[Slim] Свързан: %s", addr)
    keepalive_task = None

    # Enable OS-level TCP keepalive so dead connections are detected without timeouts
    try:
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass

    global _slim_writer, _device_mac
    _slim_writer = writer
    log.info("[Slim] ✓ _slim_writer SET для %s", addr)

    async def send_keepalive():
        try:
            while True:
                await asyncio.sleep(25)       # device READ_TIMEOUT = 35s
                _slim_send(writer, b"strm", b"t" + b"\x00" * 24)
                await writer.drain()
        except Exception:
            pass

    try:
        while True:
            # No timeout — wait indefinitely; OS TCP keepalive detects dead connection
            header = await reader.readexactly(8)
            op = header[:4].decode("ascii", errors="ignore").strip()
            length = int.from_bytes(header[4:8], "big")

            body = b""
            if length > 0:
                body = await reader.readexactly(length)

            log.debug("[Slim] OP=%r len=%d", op, length)

            if op == "HELO":
                if len(body) >= 8:
                    mac = ":".join(f"{b:02x}" for b in body[2:8])
                    log.info("[Slim] HELO від MAC=%s", mac)
                    global _device_mac
                    _device_mac = mac

                version = CONFIG["version"].encode("utf-8")
                _slim_send(writer, b"vers", version)
                await writer.drain()
                log.info("[Slim] ✓ HELO → vers відправлено")

                # Start keepalive
                if keepalive_task is None:
                    keepalive_task = asyncio.create_task(send_keepalive())

                # If there's something to play, re-send strm start (reconnect case)
                if _now_playing:
                    log.info("[Slim] ✓ TCP reconnect — автоматично пускам: %s",
                             _now_playing.get("name", ""))
                    asyncio.create_task(_send_strm_play(
                        _now_playing["url"], _now_playing.get("name", "")
                    ))

            elif op == "STAT":
                event = body[0:4].decode("ascii", errors="ignore") if len(body) >= 4 else "????"
                if event in ("STMc", "STMs", "STMt", "STMd", "STMn", "STMp", "STMu", "STMl"):
                    level = log.info if event in ("STMc", "STMs", "STMn") else log.debug
                    level("[Slim] STAT %s від MAC=%s", event, _device_mac)
                    if event == "STMn":
                        log.warning("[Slim] ⚠️  Устройството НЕ МОЖЕ да се свърже към stream URL! "
                                    "Провери /stream proxy — устройството получи strm-s но не може "
                                    "да отвори HTTP връзка към %s:9000", _local_ip)
                    elif event == "STMc":
                        log.info("[Slim] ✓ Устройството се свърза към stream (TCP audio OK)")
                    elif event == "STMs":
                        log.info("[Slim] ✓ Устройството започна да свири 🎵")

            elif op == "BYE!":
                log.info("[Slim] BYE от %s", addr)
                break

            elif op in ("IR  ", "RESP", "BODY", "META", "BUTN"):
                pass

            else:
                log.debug("[Slim] Непознат OP=%r", op)

    except asyncio.IncompleteReadError:
        pass
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        log.error("[Slim] Грешка: %s", e)
    finally:
        if keepalive_task:
            keepalive_task.cancel()
        if _slim_writer is writer:
            _slim_writer = None
            log.info("[Slim] _slim_writer CLEARED (disconnect) %s", addr)
        try:
            writer.close()
        except Exception:
            pass
        log.info("[Slim] Разкачен: %s", addr)


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
    try:
        server = await asyncio.start_server(slim_handle_client, "0.0.0.0", 3483)
    except OSError as e:
        log.critical("[Slim] НЕ МОЖЕ да стартира TCP 3483: %s  "
                     "(Проверете дали портът не е зает с: netstat -an | grep 3483)", e)
        return
    log.info("[Slim] TCP 3483 слуша...")
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
    с payload 'eIPAD\x00NAME\x00JSON\x00VERS\x00UUID\x00JVID\x06...'
    търсейки LMS сървър.

    Отговаряме с 'E' + TLV пакети:
    всеки TLV = 4-байтов таг + 1-байт дължина + стойност
    Таговете: NAME, IPAD, JSON (HTTP порт), VERS, UUID
    """
    def _tlv(tag: str, val: bytes) -> bytes:
        return tag.encode("ascii") + bytes([len(val)]) + val

    loop = asyncio.get_event_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 3483))
    sock.setblocking(False)

    log.info("[Discovery] UDP 3483 слуша за broadcasts...")

    while True:
        try:
            data, addr = await loop.run_in_executor(None, sock.recvfrom, 1024)
            log.debug("[Discovery] Broadcast от %s: %s", addr[0], data[:30])

            # Squeezebox изпраща 'e' за discovery request
            if data and data[0:1] in (b"e", b"E", b"d"):
                # Използваме актуалното _local_ip (обновено от HTTP middleware)
                reply_ip = _local_ip if _local_ip not in ("127.0.0.1", "") else local_ip
                response = (
                    b"E"
                    + _tlv("NAME", CONFIG["server_name"].encode("utf-8"))
                    + _tlv("IPAD", reply_ip.encode("ascii"))
                    + _tlv("JSON", b"9000")
                    + _tlv("VERS", CONFIG["version"].encode("utf-8"))
                    + _tlv("UUID", CONFIG["server_name"].encode("utf-8"))
                )
                sock.sendto(response, addr)
                log.debug("[Discovery] ✓ TLV отговор → %s: NAME=%s IPAD=%s JSON=9000",
                          addr[0], CONFIG["server_name"], reply_ip)

        except BlockingIOError:
            await asyncio.sleep(0.05)
        except Exception as e:
            log.error("[Discovery] Грешка: %s", e)
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


async def _strm_watchdog():
    """
    Background task: every 15 seconds, if the device has reconnected TCP (_slim_writer set)
    and there is a pending _now_playing that wasn't delivered yet, re-send strm-s.
    This is a safety net for the case where TCP reconnects after _send_strm_play gave up.
    The HELO handler also does this, but the watchdog covers any edge cases.
    """
    _last_sent_url: str = ""
    while True:
        await asyncio.sleep(15)
        if _slim_writer and _now_playing:
            url = _now_playing.get("url", "")
            if url and url != _last_sent_url:
                started_at = _now_playing.get("started_at", 0)
                # Only retry if the station was selected recently (within 10 minutes)
                if (time.time() - started_at) < 600:
                    log.info("[watchdog] TCP свързан + _now_playing запазен — изпращам strm")
                    _last_sent_url = url
                    asyncio.create_task(_send_strm_play(url, _now_playing.get("name", "")))
        elif not _now_playing:
            _last_sent_url = ""   # reset when stopped


async def main():
    import uvicorn

    local_ip = get_local_ip()

    # Make local IP available to strm helpers
    global _local_ip
    _local_ip = local_ip

    # ── Detect available media players ────────────────────────────────────────
    players_found = []
    for p in ("ffplay", "mpv", "vlc"):
        path = _find_executable(p)
        if path:
            players_found.append(f"{p} ({path})")
    if sys.platform == "win32":
        players_found.append("PowerShell WMP (вграден)")

    print("=" * 60)
    print("  SqueezeCloud сървър")
    print("=" * 60)
    print(f"  Локално IP:       {local_ip}")
    print(f"  HTTP порт:        9000  (LMS API + Web UI)")
    print(f"  TCP порт:         3483  (Slim Protocol — аудио към Squeezebox)")
    print(f"  UDP порт:         3483  (Autodiscovery broadcast)")
    print(f"  Станции:          {len(CUSTOM_STATIONS)} потребителски + {len(STATIC_STATIONS)} вградени")
    print(f"  Медия плейъри:    {', '.join(players_found) if players_found else 'не са намерени — само Web UI'}")
    print()
    print(f"  Уеб контрол:      http://{local_ip}:9000/webcontrol")
    print()
    print("  Squeezebox ще се открие АВТОМАТИЧНО чрез broadcast!")
    print()
    print("  Задължително добави в SSH на Squeezebox:")
    print(f"  cat > /mnt/storage/etc/hosts << 'EOF'")
    print(f"  127.0.0.1 localhost")
    print(f"  {local_ip} mysqueezebox.com")
    print(f"  {local_ip} www.mysqueezebox.com")
    print(f"  {local_ip} www.squeezenetwork.com")
    print(f"  {local_ip} update.squeezenetwork.com")
    print(f"  {local_ip} config.logitechmusic.com")
    print(f"  EOF")
    print()
    print("  След промяна на hosts: reboot")
    print()
    print("  ВАЖНО: www.squeezenetwork.com → TCP Slim Protocol (порт 3483)")
    print("  Без него устройството не изпраща аудио команди към сървъра.")
    print("=" * 60)

    # Стартираме всичките три услуги паралелно
    udp_task      = asyncio.create_task(slim_udp_discovery(local_ip))
    slim_task     = asyncio.create_task(start_slim_server())
    watchdog_task = asyncio.create_task(_strm_watchdog())

    config = uvicorn.Config("main:app", host="0.0.0.0", port=9000, reload=False, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(udp_task, slim_task, watchdog_task, server.serve())


if __name__ == "__main__":
    asyncio.run(main())
