---
name: squeezecloud-lms
description: >
  Expert guide for developing squeezecloud/main.py — a Python FastAPI server that
  simulates Lyrion Media Server (LMS) for Squeezebox Radio hardware. Use this skill
  when working on any of: SlimProto TCP protocol, CometD/Bayeux, stream proxy, web UI,
  JSON-RPC browse, UDP discovery, audio playback, or Squeezebox device compatibility.
---

# Squeezecloud LMS Server — Development Skill

## Project Context

**File:** `squeezecloud/main.py` (FastAPI + asyncio, Python 3.10+)
**Device:** UE Squeezebox Radio ("baby"), MAC `00:04:20:2c:90:b1`, Firmware 7.7.3
**Server:** Windows, `192.168.1.43:9000`
**Full reference:** `squeezecloud/FIRMWARE_ANALYSIS.md` — read this first for any protocol question.

---

## Architecture: 3 Independent Channels

```
Device ──UDP 3483──▶ Discovery (server responds with TLV)
Device ──TCP 3483──▶ SlimProto binary (audio commands: strm/HELO/STAT/vers)
Device ──HTTP 9000─▶ CometD + JSON-RPC (menus, status, subscriptions)
              │
              └────▶ /stream?url=... (audio proxy → device pulls audio)
```

---

## Step 1: Before any change, check FIRMWARE_ANALYSIS.md

Use `@squeezecloud/FIRMWARE_ANALYSIS.md` for:
- Protocol packet formats → §2 (SlimProto), §3 (CometD)
- Quick keyword lookup → `🔍 KEYWORD INDEX` section
- Common errors & fixes → `🐛 ЧЕСТИТЕ ГРЕШКИ` section
- Development workflows → `⚙️ DEVELOPMENT WORKFLOWS` section

---

## Step 2: Critical Rules (never violate)

### TCP / SlimProto

1. **NO `asyncio.wait_for` timeout on `readexactly()`** — device goes silent for 60-90s between STAT packets; timeout kills the writer and breaks playback.
2. **`SO_KEEPALIVE` must be set** on the TCP socket after accept.
3. **Packet direction is asymmetric:**
   - Device→Server: `4-byte opcode + 4-byte length (big-endian) + payload`
   - Server→Device: `2-byte length (big-endian) + 4-byte opcode + payload`
4. **MAC address in HELO is at `body[2:8]`** (not `body[0:6]`).
5. **NEVER send `serv` command** — causes device to disconnect and connect to a different server.
6. **Always send `strm 'q'` before `strm 's'`** to stop current playback first.
7. **Keepalive:** send `strm 't'` every 25 seconds (device READ_TIMEOUT = 35s).

### CometD / JSON

8. **`"player count"` with a space** (not underscore) — SlimServer.lua line 240 reads `data["player count"]`.
9. **`advice.timeout` must be `0`** (not 30000) — device needs immediate reconnect (polling mode).
10. **CometD responses MUST be a JSON array** `[...]`, even with one element.
11. **`item_loop`** (not `loop_loop`) — every browse response needs `item_loop`, `count`, `offset`.
12. **Never use `"hasitems": 0` or `"isaudio": 0`** — Lua `0` is truthy! Omit the key entirely.
13. **`id: "radio"`** (not `"radios"`) — SlimMenusApplet filters out `"radios"` silently.
14. **Use `"text"` not `"name"`** for display labels in item_loop.

### Audio Format

15. Auto-detect format from URL:
    - `_opus`, `.opus`, `walmradio` → `'o'` (OGG/Opus)
    - `_aac`, `.aac`, `.m3u8` → `'a'` (AAC)
    - everything else → `'m'` (MP3)

---

## Step 3: `strm-s` Packet Layout (24 bytes + HTTP GET)

```python
data = (
    b's'          # [0] command: start
    b'1'          # [1] autostart
    fmt_byte      # [2] 'm'=MP3, 'o'=OGG, 'a'=AAC, 'f'=FLAC
    b'?'          # [3] pcm_sample_size: auto
    b'?'          # [4] pcm_sample_rate: auto
    b'?'          # [5] pcm_channels: auto
    b'?'          # [6] pcm_endian: auto
    b'\xff'       # [7] threshold: 255
    b'\x00'       # [8] spdif: off
    b'\x00'       # [9] trans_period
    b'0'          # [10] trans_type: none
    b'\x00'       # [11] flags
    b'\x00'       # [12] out_threshold
    b'\x00'       # [13] slaves
    (0).to_bytes(4, 'big')   # [14-17] replay_gain
    (9000).to_bytes(2, 'big') # [18-19] serverPort
    server_ip_bytes           # [20-23] serverIp (4 octets)
    http_get_request          # [24+] "GET /stream?url=... HTTP/1.0\r\n..."
)
```

---

## Step 4: Logging Standards

```python
log.info("[Slim] ✓ _slim_writer SET для %s", addr)
log.info("[Slim] HELO від MAC=%s", mac)
log.info("[Slim] _slim_writer CLEARED (disconnect) %s", addr)
log.info("[strm] ✓ strm start (fmt=%s) → %s", chr(fmt), name)
log.info("[Discovery] ✓ TLV відповідь → %s", addr)
log.info("[Comet] handshake clientId=%s", clientId)
log.warning("[strm] Няма TCP connection — strm пропуснат")
log.error("[stream-proxy] Stream error: %s", e)
```

---

## Step 5: Debugging Workflow

### Device plays nothing after selecting station
```
1. Check log: "[Slim] ✓ _slim_writer SET" → if missing: TCP not connected
   - Check Windows Firewall: inbound TCP 3483 must be allowed
   - Check device /etc/hosts: www.squeezenetwork.com → 192.168.1.43

2. Check log: "[strm] ✓ strm start" → if missing: _send_strm_play() not called
   - Check: is CometD receiving the playlist play command?

3. Check log: "[stream-proxy] →" → if missing: device not connecting to /stream
   - Check: strm-s serverIp/serverPort correct?
   - Test: curl http://192.168.1.43:9000/stream?url=<encoded-url>

4. Check STAT events: STMc=connected, STMn=can't connect, STMd=buffer drained

5. Try a simple MP3: http://icecast.radiofrance.fr/fip-midfi.mp3
```

### Menu shows 0 stations
```
1. Check /jsonrpc.js response: has item_loop? offset? count?
2. Check: id="radio" not "radios"
3. Check: hasitems/isaudio omitted (not set to 0)
4. Check: text not name for labels
5. Check: actions.go.cmd present for every hasitems:1 item
```

---

## Step 6: Pre-commit Checklist

```bash
# Syntax check
python -c "import ast; ast.parse(open('squeezecloud/main.py').read()); print('OK')"

# Scan for known mistakes
grep -n "loop_loop\|player_count\|\"radios\"\|wait_for.*readexactly\|b\"serv\"" squeezecloud/main.py
# Expected: no matches

# Manual checklist:
# [ ] item_loop (not loop_loop)
# [ ] "player count" with space
# [ ] id="radio" not "radios"
# [ ] advice.timeout=0
# [ ] strm 'q' before strm 's'
# [ ] No wait_for on readexactly
# [ ] SO_KEEPALIVE set on TCP socket
```

---

## Key Files

| File | Purpose |
|---|---|
| `squeezecloud/main.py` | Server: FastAPI + CometD + SlimProto + stream proxy |
| `squeezecloud/stations.json` | User stations: `[{"name","url","genre","country"}]` |
| `squeezecloud/FIRMWARE_ANALYSIS.md` | Full protocol reference (read first!) |
| `squeezecloud/dump/jive_dump/.../SlimProto.lua` | Authoritative TCP format source |
| `squeezecloud/dump/net_dump/.../SlimBrowserApplet.lua` | Browse item format source |
