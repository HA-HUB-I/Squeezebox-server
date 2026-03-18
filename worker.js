/**
 * SqueezeCloud Worker
 * Impersonates mysqueezebox.com for Squeezebox Radio devices
 * Deploy to Cloudflare Workers
 * 
 * Endpoints implemented:
 *   GET  /api/v1/login
 *   GET  /api/v1/time
 *   POST /jsonrpc.js
 *   GET  /radio
 *   GET  /api/v1/radios
 *   GET  /api/v1/apps
 *   GET  /api/v1/weather
 *   GET  /api/v1/news
 *   GET  /api/v1/podcasts
 *   GET  /webcontrol
 */

// ─── CONFIG ──────────────────────────────────────────────────────────────────

const CONFIG = {
  serverName: "SqueezeCloud",
  serverVersion: "8.5.0",
  // Default location for weather (Sofia, BG) — override per device via KV
  defaultLat: 42.6977,
  defaultLon: 23.3219,
  defaultCity: "Sofia",
  // How long to keep "now playing" state in KV (seconds)
  nowPlayingTtl: 3600,
};

// ─── STATIC RADIO STATIONS ───────────────────────────────────────────────────
// Fallback list — Worker also fetches live from Radio Browser API

const STATIC_STATIONS = [
  { name: "БНР Хоризонт",       url: "https://stream.bnr.bg/horizont_24",      genre: "News",    country: "BG" },
  { name: "БНР Христо Ботев",   url: "https://stream.bnr.bg/hristobotev_24",   genre: "Culture", country: "BG" },
  { name: "БНР Радио България", url: "https://stream.bnr.bg/radiobulgaria_24", genre: "News",    country: "BG" },
  { name: "Radio 1 Rock",        url: "https://live.radio1.bg/radio1rock.mp3",  genre: "Rock",    country: "BG" },
  { name: "Z-Rock Bulgaria",     url: "https://stream.zrock.bg/zrock",          genre: "Rock",    country: "BG" },
  { name: "BBC World Service",   url: "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service", genre: "News", country: "UK" },
  { name: "BBC Radio 6 Music",   url: "https://stream.live.vc.bbcmedia.co.uk/bbc_6music",        genre: "Music", country: "UK" },
  { name: "KEXP 90.3 FM",        url: "https://kexp-mp3-128.streamguys1.com/kexp128.mp3",         genre: "Indie", country: "US" },
  { name: "SomaFM Groove Salad", url: "https://ice1.somafm.com/groovesalad-128-mp3",              genre: "Ambient", country: "US" },
  { name: "SomaFM Drone Zone",   url: "https://ice1.somafm.com/dronezone-128-mp3",                genre: "Ambient", country: "US" },
  { name: "SomaFM Indie Pop",    url: "https://ice1.somafm.com/indiepop-128-mp3",                 genre: "Indie", country: "US" },
  { name: "Jazz24",              url: "https://streams.jazz24.org/jazz24_mp3",                        genre: "Jazz", country: "US" },
  { name: "1.FM Jazz & Blues",   url: "https://strm112.1.fm/jazzandblues_mobile_mp3",             genre: "Jazz", country: "US" },
  { name: "NRJ Bulgaria",        url: "https://stream.nrj.bg/nrj-128.mp3",     genre: "Pop",     country: "BG" },
  { name: "Radio Energy BG",     url: "https://stream.rne.bg/energy128.mp3",   genre: "Dance",   country: "BG" },
];

// ─── PODCAST RSS FEEDS ───────────────────────────────────────────────────────

const PODCAST_FEEDS = [
  { name: "БНР Подкасти",        url: "https://bnr.bg/radiobulgaria/podcast/category/44", lang: "bg" },
  { name: "Deutche Welle BG",    url: "https://rss.dw.com/rdf/podcast-bulgarisch-aktuell", lang: "bg" },
  { name: "BBC Global News",     url: "https://podcasts.files.bbci.co.uk/p02nq0gn.rss",   lang: "en" },
  { name: "Radiolab",            url: "https://feeds.feedburner.com/radiolab",             lang: "en" },
  { name: "99% Invisible",       url: "https://feeds.simplecast.com/BqbsxVfO",             lang: "en" },
  { name: "TED Talks Daily",     url: "https://feeds.feedburner.com/TEDTalks_audio",       lang: "en" },
  { name: "Freakonomics Radio",  url: "https://feeds.simplecast.com/Y8lFbOT4",             lang: "en" },
];

// ─── NEWS RSS FEEDS ──────────────────────────────────────────────────────────

const NEWS_FEEDS = [
  { name: "БНР Новини",    url: "https://bnr.bg/rss",                           lang: "bg" },
  { name: "Dnevnik.bg",    url: "https://www.dnevnik.bg/rss/",                  lang: "bg" },
  { name: "Reuters",       url: "https://feeds.reuters.com/reuters/topNews",     lang: "en" },
  { name: "BBC News",      url: "https://feeds.bbci.co.uk/news/rss.xml",        lang: "en" },
  { name: "Al Jazeera",    url: "https://www.aljazeera.com/xml/rss/all.xml",    lang: "en" },
];

// ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers for all responses
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      let response;

      // Route requests
      if (path === "/api/v1/login" || path === "/user/login") {
        response = handleLogin(url, request);
      } else if (path === "/api/v1/time") {
        response = handleTime();
      } else if (path === "/jsonrpc.js") {
        response = await handleJsonRpc(request, env);
      } else if (path === "/api/v1/radios" || path === "/radio" || path.startsWith("/api/v1/radio")) {
        response = await handleRadio(url, env);
      } else if (path === "/api/v1/apps" || path === "/apps") {
        response = handleApps();
      } else if (path === "/api/v1/weather" || path.startsWith("/weather")) {
        response = await handleWeather(url, env);
      } else if (path === "/api/v1/news" || path.startsWith("/news")) {
        response = await handleNews(url, env);
      } else if (path === "/api/v1/podcasts" || path.startsWith("/podcasts")) {
        response = await handlePodcasts(url, env);
      } else if (path === "/" || path === "/api/v1/status") {
        response = handleStatus();
      } else if (path === "/webcontrol") {
        response = handleWebControl();
      } else {
        // Unknown endpoint — return empty success so device doesn't error
        response = jsonResponse({ status: "ok", result: [] });
      }

      // Add CORS to all responses
      const headers = new Headers(response.headers);
      Object.entries(corsHeaders).forEach(([k, v]) => headers.set(k, v));
      return new Response(response.body, { status: response.status, headers });

    } catch (err) {
      console.error("Worker error:", err);
      return jsonResponse({ error: err.message }, 500);
    }
  }
};

// ─── AUTH / LOGIN ─────────────────────────────────────────────────────────────

function handleLogin(url, request) {
  // Squeezebox sends MAC address as device ID
  // Accept any device — no real auth needed
  const mac = url.searchParams.get("mac") || 
               url.searchParams.get("u") || 
               "unknown";

  return jsonResponse({
    status: "ok",
    player: {
      id: mac,
      name: "Squeezebox Radio",
      server: CONFIG.serverName,
    },
    // Session token — static is fine since we trust all devices
    token: Buffer.from(mac + ":squeezecloud").toString("base64"),
    result: {
      sn_version: CONFIG.serverVersion,
      playerid: mac,
    }
  });
}

// ─── TIME ────────────────────────────────────────────────────────────────────

function handleTime() {
  return jsonResponse({
    status: "ok",
    time: Math.floor(Date.now() / 1000),
    result: Math.floor(Date.now() / 1000),
  });
}

// ─── SERVER STATUS ────────────────────────────────────────────────────────────

function handleStatus() {
  return jsonResponse({
    status: "ok",
    version: CONFIG.serverVersion,
    name: CONFIG.serverName,
    result: {
      version: CONFIG.serverVersion,
      server_name: CONFIG.serverName,
      uuid: "squeezecloud-worker-v1",
    }
  });
}

// ─── APPS LIST ───────────────────────────────────────────────────────────────

function handleApps() {
  const apps = [
    { id: "radio",    text: "Internet Radio", icon: "radio",    type: "app" },
    { id: "podcasts", text: "Podcasts",        icon: "podcast",  type: "app" },
    { id: "weather",  text: "Weather",         icon: "weather",  type: "app" },
    { id: "news",     text: "News",            icon: "news",     type: "app" },
  ];

  return jsonResponse({
    status: "ok",
    result: {
      item_loop: apps,
      count: apps.length,
      offset: 0,
    }
  });
}

// ─── JSON-RPC HANDLER ─────────────────────────────────────────────────────────

async function handleJsonRpc(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON" }, 400);
  }

  const method = body.method || "";
  const params = body.params || [];
  const id = body.id || 1;

  // params[0] = player MAC, params[1] = command array
  const playerMac = params[0] || "";
  const cmd = Array.isArray(params[1]) ? params[1] : [];
  const command = cmd[0] || "";

  let result = {};

  switch (command) {
    case "serverstatus":
      result = await rpcServerStatus(env);
      break;

    case "players":
      result = {
        count: 1,
        players_loop: [{
          playerid: playerMac,
          name: "Squeezebox Radio",
          model: "squeezebox_radio",
          isplaying: 0,
          connected: 1,
        }]
      };
      break;

    case "status":
      result = await rpcPlayerStatus(playerMac, env);
      break;

    case "play":
      result = { ok: 1 };
      break;

    case "pause":
      await rpcPause(playerMac, env);
      result = { ok: 1 };
      break;

    case "playlist":
      result = await rpcPlaylist(cmd, playerMac, env);
      break;

    case "mixer":
      result = { ok: 1 };
      break;

    case "radios":
      result = await rpcRadios(cmd, env);
      break;

    case "podcasts":
      result = await rpcPodcasts(cmd, env);
      break;

    case "apps":
      result = {
        count: 4,
        offset: 0,
        item_loop: [
          { id: "squeezecloudRadio",    text: "Internet Radio", actions: { go: { cmd: ["radios", 0, 100], player: 0 } } },
          { id: "squeezecloudPodcasts", text: "Podcasts",        actions: { go: { cmd: ["podcasts", 0, 100], player: 0 } } },
          { id: "squeezecloud_weather", text: "Weather",         actions: { go: { cmd: ["weather"], player: 0 } } },
          { id: "squeezecloud_news",    text: "News",            actions: { go: { cmd: ["news"], player: 0 } } },
        ]
      };
      break;

    case "favorites":
      result = await rpcFavorites(cmd, env);
      break;

    case "weather":
      result = await rpcWeather(env);
      break;

    case "news":
      result = await rpcNews(env);
      break;

    default:
      result = { ok: 1, count: 0 };
  }

  return jsonResponse({
    id,
    method: "slim.request",
    result,
  });
}

// ─── RPC: SERVER STATUS ───────────────────────────────────────────────────────

async function rpcServerStatus(env) {
  return {
    version: CONFIG.serverVersion,
    server_name: CONFIG.serverName,
    uuid: "squeezecloud-v1",
    player_count: 1,
    info: "total_duration:0,total_genres:5,total_artists:0,total_albums:0,total_songs:0",
  };
}

// ─── RPC: PLAYER STATUS ───────────────────────────────────────────────────────

async function rpcPlayerStatus(mac, env) {
  let mode = "stop";
  let playlistLoop = [];
  let remoteMeta = {};

  if (env && env.RADIO_KV && mac) {
    try {
      const nowPlaying = await env.RADIO_KV.get(`player:${mac}:now_playing`, { type: "json" });
      if (nowPlaying && nowPlaying.url) {
        mode = "play";
        playlistLoop = [{
          id: "current",
          title: nowPlaying.name || "",
          url: nowPlaying.url,
          duration: 0,
          remote: 1,
        }];
        remoteMeta = { title: nowPlaying.name || "", url: nowPlaying.url };
      }
    } catch (_) {
      // KV unavailable — fall through to stopped state
    }
  }

  return {
    playerid: mac,
    name: "Squeezebox Radio",
    mode,
    mixer_volume: 50,
    playlist_cur_index: 0,
    playlist_timestamp: Date.now() / 1000,
    playlist_loop: playlistLoop,
    remoteMeta,
  };
}

// ─── RPC: PLAYLIST ────────────────────────────────────────────────────────────

async function rpcPlaylist(cmd, mac, env) {
  // cmd[1] = sub-command ("play", "stop", "pause", ...)
  // cmd[2] = url (for "play")
  const subCmd = (cmd[1] || "").toLowerCase();

  if (subCmd === "play" && cmd[2]) {
    const url = cmd[2];
    const name = cmd[3] || "";
    if (env && env.RADIO_KV && mac) {
      try {
        await env.RADIO_KV.put(`player:${mac}:now_playing`, JSON.stringify({ url, name }), { expirationTtl: CONFIG.nowPlayingTtl });
      } catch (_) { /* KV unavailable */ }
    }
    return { ok: 1, mode: "play", url };
  }

  if (subCmd === "stop" || subCmd === "clear") {
    if (env && env.RADIO_KV && mac) {
      try {
        await env.RADIO_KV.delete(`player:${mac}:now_playing`);
      } catch (_) { /* KV unavailable */ }
    }
    return { ok: 1, mode: "stop" };
  }

  return { ok: 1 };
}

// ─── RPC: PAUSE ──────────────────────────────────────────────────────────────

async function rpcPause(mac, env) {
  if (env && env.RADIO_KV && mac) {
    try {
      await env.RADIO_KV.delete(`player:${mac}:now_playing`);
    } catch (_) { /* KV unavailable */ }
  }
}

// ─── RPC: RADIOS ─────────────────────────────────────────────────────────────

async function rpcRadios(cmd, env) {
  // cmd = ["radios", "0", "10", ...optional "item_id:genre:Rock"]
  const start = parseInt(cmd[1]) || 0;
  const count = parseInt(cmd[2]) || 10;

  // Detect genre drill-down: SlimBrowser appends "item_id:genre:Rock" when user
  // selects a genre node, matching the same pattern used by the podcasts handler.
  const itemIdParam = cmd.find(c => typeof c === "string" && c.startsWith("item_id:"));

  const stations = await getRadioStations(env);

  if (itemIdParam && itemIdParam.startsWith("item_id:genre:")) {
    // Drill into a specific genre (or "All" for the full list)
    const genre = itemIdParam.slice("item_id:genre:".length);
    const filtered = genre === "All"
      ? stations
      : stations.filter(s => (s.genre || "Music").toLowerCase() === genre.toLowerCase());
    const slice = filtered.slice(start, start + count);
    return {
      count: filtered.length,
      offset: start,
      item_loop: slice.map((s, i) => ({
        id: `radio:${start + i}`,
        text: s.name,
        type: "audio",
        url: s.url,
        isaudio: 1,
        actions: {
          go: {
            player: 0,
            cmd: ["playlist", "play", s.url, s.name],
            nextWindow: "nowPlaying",
          },
        },
      })),
    };
  }

  // Top level — show genre nodes so the user can browse by genre.
  const genres = [...new Set(stations.map(s => s.genre || "Music"))].sort();
  const allItem = {
    id: "genre:All",
    text: "All Stations",
    hasitems: 1,
    actions: { go: { player: 0, cmd: ["radios", 0, 100, "item_id:genre:All"] } },
  };
  const genreItems = genres.map(g => ({
    id: `genre:${g}`,
    text: g,
    hasitems: 1,
    actions: { go: { player: 0, cmd: ["radios", 0, 100, `item_id:genre:${g}`] } },
  }));
  const allItems = [allItem, ...genreItems];
  const total = allItems.length;
  return {
    count: total,
    offset: start,
    item_loop: allItems.slice(start, start + count),
  };
}

// ─── RPC: FAVORITES ───────────────────────────────────────────────────────────

async function rpcFavorites(cmd, env) {
  const stations = STATIC_STATIONS.slice(0, 10);
  return {
    count: stations.length,
    offset: 0,
    item_loop: stations.map((s, i) => ({
      id: `fav:${i}`,
      text: s.name,
      url: s.url,
      type: "audio",
      isaudio: 1,
      actions: {
        go: {
          player: 0,
          cmd: ["playlist", "play", s.url, s.name],
          nextWindow: "nowPlaying",
        },
      },
    }))
  };
}

// ─── RPC: WEATHER ─────────────────────────────────────────────────────────────

async function rpcWeather(env) {
  const weather = await fetchWeather(CONFIG.defaultLat, CONFIG.defaultLon, CONFIG.defaultCity);
  return {
    count: 1,
    offset: 0,
    item_loop: [{
      id: "weather:current",
      text: weather.summary,
      type: "text",
    }]
  };
}

// ─── RPC: NEWS ────────────────────────────────────────────────────────────────

async function rpcNews(env) {
  const items = await fetchNewsItems(NEWS_FEEDS[0]);
  return {
    count: items.length,
    offset: 0,
    item_loop: items.slice(0, 10).map((item, i) => ({
      id: `news:${i}`,
      text: item.title,
      type: "text",
    }))
  };
}

// ─── RPC: PODCASTS ────────────────────────────────────────────────────────────

async function rpcPodcasts(cmd, env) {
  const start = parseInt(cmd[1]) || 0;

  // Top level — show podcast list
  const menuId = cmd.find(c => typeof c === "string" && c.startsWith("item_id:"));
  
  if (!menuId) {
    // Return podcast channels
    return {
      count: PODCAST_FEEDS.length,
      offset: 0,
      item_loop: PODCAST_FEEDS.map((feed, i) => ({
        id: `podcast:${i}`,
        text: feed.name,
        hasitems: 1,
        actions: { go: { player: 0, cmd: ["podcasts", 0, 100, `item_id:podcast:${i}`] } },
      }))
    };
  }

  // Drill into a podcast feed
  const feedIdx = parseInt(menuId.replace("item_id:podcast:", "")) || 0;
  const feed = PODCAST_FEEDS[feedIdx];
  if (!feed) return { count: 0, offset: 0, item_loop: [] };

  const episodes = await fetchPodcastEpisodes(feed);
  return {
    count: episodes.length,
    offset: start,
    item_loop: episodes.slice(start, start + 10).map((ep, i) => ({
      id: `episode:${feedIdx}:${i}`,
      text: ep.title,
      type: "audio",
      url: ep.url,
      isaudio: 1,
      actions: {
        go: {
          player: 0,
          cmd: ["playlist", "play", ep.url, ep.title],
          nextWindow: "nowPlaying",
        },
      },
    }))
  };
}

// ─── HTTP ROUTE: RADIO ────────────────────────────────────────────────────────

async function handleRadio(url, env) {
  const genre = url.searchParams.get("genre") || "";
  const country = url.searchParams.get("country") || "";
  const search = url.searchParams.get("search") || "";

  let stations = await getRadioStations(env);

  if (genre) stations = stations.filter(s => s.genre?.toLowerCase().includes(genre.toLowerCase()));
  if (country) stations = stations.filter(s => s.country?.toLowerCase() === country.toLowerCase());
  if (search) stations = stations.filter(s => s.name?.toLowerCase().includes(search.toLowerCase()));

  // Group by genre for browse menu
  const genres = [...new Set(stations.map(s => s.genre || "Other"))].sort();

  return jsonResponse({
    status: "ok",
    count: stations.length,
    genres,
    stations: stations.slice(0, 50).map(s => ({
      name: s.name,
      url: s.url,
      genre: s.genre,
      country: s.country,
      bitrate: s.bitrate || 128,
    }))
  });
}

// ─── HTTP ROUTE: WEATHER ──────────────────────────────────────────────────────

async function handleWeather(url, env) {
  const lat = parseFloat(url.searchParams.get("lat") || CONFIG.defaultLat);
  const lon = parseFloat(url.searchParams.get("lon") || CONFIG.defaultLon);
  const city = url.searchParams.get("city") || CONFIG.defaultCity;

  const weather = await fetchWeather(lat, lon, city);
  return jsonResponse({ status: "ok", ...weather });
}

// ─── HTTP ROUTE: NEWS ─────────────────────────────────────────────────────────

async function handleNews(url, env) {
  const lang = url.searchParams.get("lang") || "bg";
  const feed = NEWS_FEEDS.find(f => f.lang === lang) || NEWS_FEEDS[0];
  const items = await fetchNewsItems(feed);

  return jsonResponse({
    status: "ok",
    source: feed.name,
    count: items.length,
    items: items.slice(0, 20),
  });
}

// ─── HTTP ROUTE: PODCASTS ─────────────────────────────────────────────────────

async function handlePodcasts(url, env) {
  const feedIdx = parseInt(url.searchParams.get("feed") || "0");
  const feed = PODCAST_FEEDS[feedIdx] || PODCAST_FEEDS[0];
  const episodes = await fetchPodcastEpisodes(feed);

  return jsonResponse({
    status: "ok",
    feed: feed.name,
    count: episodes.length,
    episodes: episodes.slice(0, 20),
  });
}

// ─── RADIO BROWSER API ────────────────────────────────────────────────────────

async function getRadioStations(env) {
  // Try KV cache first
  if (env?.RADIO_KV) {
    try {
      const cached = await env.RADIO_KV.get("stations:all", { type: "json" });
      if (cached && cached.timestamp > Date.now() - 3600000) {
        return [...STATIC_STATIONS, ...cached.stations];
      }
    } catch {}
  }

  // Fetch from Radio Browser API (community-maintained, free)
  try {
    const apis = [
      "https://de1.api.radio-browser.info",
      "https://nl1.api.radio-browser.info",
      "https://at1.api.radio-browser.info",
    ];
    const base = apis[Math.floor(Math.random() * apis.length)];

    const resp = await fetch(
      `${base}/json/stations/search?limit=200&hidebroken=true&order=votes&reverse=true&is_https=true`,
      { headers: { "User-Agent": "SqueezeCloud/1.0" } }
    );

    if (resp.ok) {
      const data = await resp.json();
      const stations = data
        .filter(s => s.url_resolved && s.name
          // Exclude HLS (.m3u8) streams — Squeezebox Radio firmware does not
          // support HTTP Live Streaming; only direct MP3/AAC/OGG streams work.
          && !s.url_resolved.toLowerCase().endsWith(".m3u8"))
        .map(s => ({
          name: s.name.trim(),
          url: s.url_resolved,
          genre: s.tags?.split(",")[0] || "Music",
          country: s.countrycode || "",
          bitrate: s.bitrate || 128,
          votes: s.votes || 0,
        }));

      // Cache to KV for 1 hour
      if (env?.RADIO_KV) {
        try {
          await env.RADIO_KV.put("stations:all", JSON.stringify({
            timestamp: Date.now(),
            stations,
          }), { expirationTtl: 3600 });
        } catch {}
      }

      return [...STATIC_STATIONS, ...stations];
    }
  } catch (err) {
    console.error("Radio Browser API error:", err);
  }

  // Fallback to static list
  return STATIC_STATIONS;
}

// ─── WEATHER (Open-Meteo — free, no key needed) ────────────────────────────────

async function fetchWeather(lat, lon, city) {
  try {
    const resp = await fetch(
      `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weathercode,windspeed_10m,relativehumidity_2m&timezone=auto`
    );

    if (!resp.ok) throw new Error("Weather API failed");
    const data = await resp.json();

    const curr = data.current;
    const temp = curr.temperature_2m;
    const humidity = curr.relativehumidity_2m;
    const wind = curr.windspeed_10m;
    const code = curr.weathercode;
    const condition = weatherCodeToText(code);

    return {
      city,
      temperature: temp,
      unit: "°C",
      condition,
      humidity,
      wind_kmh: wind,
      summary: `${city}: ${condition}, ${temp}°C, влажност ${humidity}%, вятър ${wind} км/ч`,
      icon: weatherCodeToIcon(code),
    };
  } catch (err) {
    return {
      city,
      summary: `${city}: Няма данни за времето`,
      error: err.message,
    };
  }
}

function weatherCodeToText(code) {
  const codes = {
    0: "Ясно", 1: "Предимно ясно", 2: "Частично облачно", 3: "Облачно",
    45: "Мъгла", 48: "Скреж",
    51: "Ситен дъжд", 53: "Дъжд", 55: "Силен дъжд",
    61: "Дъжд", 63: "Умерен дъжд", 65: "Силен дъжд",
    71: "Сняг", 73: "Умерен сняг", 75: "Силен сняг",
    80: "Валежи", 81: "Умерени валежи", 82: "Силни валежи",
    95: "Гръмотевична буря", 96: "Буря с градушка", 99: "Силна буря",
  };
  return codes[code] || "Непознат";
}

function weatherCodeToIcon(code) {
  if (code === 0) return "☀️";
  if (code <= 2) return "🌤️";
  if (code <= 3) return "☁️";
  if (code <= 48) return "🌫️";
  if (code <= 67) return "🌧️";
  if (code <= 77) return "❄️";
  if (code <= 82) return "🌦️";
  return "⛈️";
}

// ─── RSS PARSER ───────────────────────────────────────────────────────────────

async function fetchNewsItems(feed) {
  try {
    const resp = await fetch(feed.url, {
      headers: { "User-Agent": "SqueezeCloud/1.0" },
      cf: { cacheTtl: 300 },
    });
    if (!resp.ok) throw new Error("RSS fetch failed");
    const text = await resp.text();
    return parseRSS(text).slice(0, 20);
  } catch (err) {
    console.error("News fetch error:", err);
    return [{ title: "Неуспешно зареждане на новини", url: "", description: "" }];
  }
}

async function fetchPodcastEpisodes(feed) {
  try {
    const resp = await fetch(feed.url, {
      headers: { "User-Agent": "SqueezeCloud/1.0" },
      cf: { cacheTtl: 900 },
    });
    if (!resp.ok) throw new Error("Podcast RSS fetch failed");
    const text = await resp.text();
    return parseRSSWithAudio(text).slice(0, 20);
  } catch (err) {
    console.error("Podcast fetch error:", err);
    return [];
  }
}

function parseRSS(xml) {
  const items = [];
  const itemRegex = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const item = match[1];
    const title = extractTag(item, "title");
    const link = extractTag(item, "link");
    const description = extractTag(item, "description");
    if (title) items.push({ title: cleanText(title), url: link, description: cleanText(description) });
  }
  return items;
}

function parseRSSWithAudio(xml) {
  const items = [];
  const itemRegex = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let match;
  while ((match = itemRegex.exec(xml)) !== null) {
    const item = match[1];
    const title = extractTag(item, "title");
    
    // Look for enclosure (audio file)
    const enclosureMatch = item.match(/<enclosure[^>]+url=["']([^"']+)["'][^>]*>/i);
    const url = enclosureMatch?.[1] || "";
    
    if (title && url) {
      items.push({ title: cleanText(title), url });
    }
  }
  return items;
}

function extractTag(xml, tag) {
  const match = xml.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, "i")) ||
                xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return match?.[1]?.trim() || "";
}

function cleanText(text) {
  return text.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;/g, "'").replace(/&quot;/g, '"').trim();
}

// ─── HELPERS ─────────────────────────────────────────────────────────────────

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ─── WEB CONTROL PAGE ────────────────────────────────────────────────────────

function handleWebControl() {
  const html = `<!DOCTYPE html>
<html lang="bg">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SqueezeCloud — контрол</title>
  <style>
    body { font-family: sans-serif; margin: 0; background: #1a1a2e; color: #eee; }
    header { background: #16213e; padding: 1rem 2rem; display: flex; align-items: center; gap: 1rem; }
    header h1 { margin: 0; font-size: 1.4rem; color: #e94560; }
    .badge { font-size: .75rem; background: #0f3460; padding: .2rem .6rem; border-radius: 999px; }
    main { padding: 1.5rem 2rem; }
    .card { background: #16213e; border-radius: 8px; padding: 1rem 1.5rem; margin-bottom: 1.2rem; }
    .card h2 { margin: 0 0 .6rem; font-size: 1rem; color: #e94560; text-transform: uppercase; letter-spacing: .05em; }
    .info-row { display: flex; gap: 2rem; flex-wrap: wrap; }
    .info-item label { display: block; font-size: .75rem; color: #aaa; }
    .info-item span { font-size: 1rem; font-weight: bold; }
    .genre-list { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .5rem; }
    .genre-btn { background: #0f3460; border: none; color: #eee; padding: .35rem .9rem;
                 border-radius: 999px; cursor: pointer; font-size: .85rem; }
    .genre-btn:hover, .genre-btn.active { background: #e94560; }
    #stations { margin-top: 1rem; }
    .station-row { display: flex; justify-content: space-between; align-items: center;
                   padding: .5rem 0; border-bottom: 1px solid #0f3460; }
    .station-row:last-child { border-bottom: none; }
    .station-name { font-size: .95rem; }
    .station-meta { font-size: .75rem; color: #aaa; }
    .copy-btn { background: #e94560; border: none; color: #fff; padding: .3rem .8rem;
                border-radius: 4px; cursor: pointer; font-size: .8rem; }
    .copy-btn:hover { background: #c73652; }
    #msg { position: fixed; bottom: 1rem; right: 1rem; background: #e94560; color: #fff;
           padding: .6rem 1.2rem; border-radius: 6px; display: none; font-size: .9rem; }
  </style>
</head>
<body>
<header>
  <h1>&#127925; SqueezeCloud</h1>
  <span class="badge" id="srv-version">v\u2026</span>
</header>
<main>
  <div class="card">
    <h2>Статус на сървъра</h2>
    <div class="info-row">
      <div class="info-item"><label>Сървър</label><span id="srv-name">\u2026</span></div>
      <div class="info-item"><label>Версия</label><span id="srv-ver">\u2026</span></div>
    </div>
  </div>
  <div class="card">
    <h2>Радио по жанр</h2>
    <div class="genre-list" id="genre-list">Зареждане\u2026</div>
    <div id="stations"></div>
  </div>
</main>
<div id="msg"></div>
<script>
  let allStations = [];

  async function init() {
    try {
      const r = await fetch('/api/v1/status');
      const d = await r.json();
      document.getElementById('srv-name').textContent = d.result.server_name;
      document.getElementById('srv-ver').textContent = d.result.version;
      document.getElementById('srv-version').textContent = 'v' + d.result.version;
    } catch (e) { console.error('status error', e); }

    try {
      const r = await fetch('/api/v1/radios');
      const d = await r.json();
      allStations = d.stations || [];
      renderGenres(d.genres || []);
      renderStations(allStations.slice(0, 20));
    } catch (e) { console.error('radios error', e); }
  }

  function renderGenres(genres) {
    const el = document.getElementById('genre-list');
    el.innerHTML = '';
    const all = document.createElement('button');
    all.className = 'genre-btn active';
    all.textContent = 'Всички';
    all.addEventListener('click', () => { setActive(all); renderStations(allStations.slice(0, 50)); });
    el.appendChild(all);
    genres.forEach(g => {
      const btn = document.createElement('button');
      btn.className = 'genre-btn';
      btn.textContent = g;
      btn.addEventListener('click', () => {
        setActive(btn);
        renderStations(allStations.filter(s => (s.genre || '').toLowerCase() === g.toLowerCase()));
      });
      el.appendChild(btn);
    });
  }

  function setActive(btn) {
    document.querySelectorAll('.genre-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }

  // Render station rows using DOM creation — no innerHTML with user data, no XSS.
  function renderStations(stations) {
    const el = document.getElementById('stations');
    el.innerHTML = '';
    if (!stations.length) {
      const p = document.createElement('p');
      p.style.color = '#aaa';
      p.textContent = '\u041d\u044f\u043c\u0430 \u0441\u0442\u0430\u043d\u0446\u0438\u0438.';
      el.appendChild(p);
      return;
    }
    stations.forEach(s => {
      const row = document.createElement('div');
      row.className = 'station-row';

      const info = document.createElement('div');
      const nameEl = document.createElement('div');
      nameEl.className = 'station-name';
      nameEl.textContent = s.name || '';
      const metaEl = document.createElement('div');
      metaEl.className = 'station-meta';
      metaEl.textContent = (s.genre || '') + ' \u2022 ' + (s.country || '') + ' \u2022 ' + (s.bitrate || '?') + ' kbps';
      info.appendChild(nameEl);
      info.appendChild(metaEl);

      const btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.textContent = '\u25b6 URL';
      btn.addEventListener('click', () => copyUrl(s.url || '', s.name || ''));

      row.appendChild(info);
      row.appendChild(btn);
      el.appendChild(row);
    });
  }

  function copyUrl(url, name) {
    navigator.clipboard.writeText(url).then(() => showMsg('\u041a\u043e\u043f\u0438\u0440\u0430\u043d\u043e: ' + name)).catch(() => showMsg(url));
  }

  function showMsg(text) {
    const el = document.getElementById('msg');
    el.textContent = text;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 3000);
  }

  init();
<\/script>
</body>
</html>`;
  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
