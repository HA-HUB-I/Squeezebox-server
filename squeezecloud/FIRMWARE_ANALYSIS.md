# Анализ на Firmware Файловете — Squeezebox Radio / Baby

**Устройство:** UE Squeezebox Radio (код: "baby"), Firmware 7.7.3-r16676  
**Платформа:** MIPS Linux (OpenEmbedded), Lua 5.1, Jive UI Framework  
**Dump директории:** `dump/jive_dump/` (системен) и `dump/net_dump/` (мрежов/актуален)  
**Сървър файл:** `squeezecloud/main.py` (FastAPI + asyncio)

---

## ⚡ QUICK REFERENCE — Бързо търсене по задача

> Намери задачата, виж директно секцията. Без четене на целия документ.

### 🔌 Свързване / Connection

| Задача | Секция | Ключово |
|--------|--------|---------|
| UDP discovery не работи | [§5.1–5.2](#51-discovery-пакет-устройство--broadcast) | `'e'` request → `'E'` response с TLV |
| HELO не се получава | [§2.2](#22-helo-пакет-устройство--сървър-при-свързване) | TCP 3483, прочети 8 байта header първо |
| `vers` отговор след HELO | [§2.5](#25-останали-serverdevice-команди) | Задължителен, веднага след HELO |
| CometD handshake | [§3.1](#31-свързване--handshake) | POST /cometd, върни `clientId` |
| `advice.timeout=0` | [§3.1](#31-свързване--handshake) | Polling mode — задължително |
| `_slim_writer` = None | [§19.1](#191-slimproto-tcp) | Виж SO_KEEPALIVE, без read timeout |

### 🎵 Аудио / Playback

| Задача | Секция | Ключово |
|--------|--------|---------|
| Устройството не пуска | [§2.4](#24-strm-команди-сървър--устройство) | `strm 'q'` после `strm 's'`, правилен format байт |
| Грешен аудио format | [§7.4](#74-codec-формати) | `'m'`=MP3, `'o'`=OGG/Opus, `'a'`=AAC |
| `strm-s` байтова структура | [§2.4](#24-strm-команди-сървър--устройство) | Пълна таблица offset/size |
| Keepalive / disconnect | [§2.6](#26-таймаути-и-keepalive) | `strm 't'` на 25s, READ_TIMEOUT=35s |
| Reconnect при HELO | [§2.7](#27-reconnect-логика) | wlanFlags=0x4000, повторно strm-s |
| Volume gain | [§7.3](#73-volume-mapping-таблица) | 100-level таблица, 65536=max |

### 📋 Меню / Browse

| Задача | Секция | Ключово |
|--------|--------|---------|
| Home menu items | [§12.3](#123-формат-на-home-menu-item) | `node:"home"`, `id:"radio"` (НЕ "radios") |
| menustatus push | [§12.2](#122-menustatus-subscription) | `data[2]=items, data[3]="add"` |
| Browse item задълж. полета | [§10.1](#101-задължителни-полета-в-item_loop) | `text,id,node,hasitems/isaudio,actions.go.cmd` |
| `0` е truthy в Lua! | [§10.1](#101-задължителни-полета-в-item_loop) | НИКОГА `hasitems:0` — пропусни ключа |
| item_loop формат | [§11.1](#111-задължителен-формат-на-chunk-отговор) | `count`, `offset`, `item_loop` (не loop_loop) |
| nextWindow стойности | [§10.3](#103-nextwindow-стойности) | `"nowPlaying"`, `"home"`, `"parent"` ... |
| Pagination | [§10.4](#104-pagination) | BLOCK_SIZE=200, offset=0-based |

### 📡 CometD / RPC

| Задача | Секция | Ключово |
|--------|--------|---------|
| serverstatus полета | [§4.2](#42-serverstatus-отговор--задължителни-полета) | `"player count"` (с интервал!) |
| playerstatus subscription | [§8.3](#83-cometd-subscription-за-playerstatus) | `/slim/playerstatus/{mac}` |
| playerstatus полета | [§8.4](#84-playerstatus-отговор--прочитани-полета) | mode, time, item_loop, remote |
| menu command при connect | [§12.1](#121-команда-за-зареждане-на-менюто) | `["menu", 0, 100, "direct:1"]` |
| RPC команди от плейъра | [§8.2](#82-rpc-команди-изпращани-от-плейъра) | status, mode, pause, volume ... |
| Batch заявки | [§3.6](#36-batch-заявки) | `startBatch()` / `endBatch()` |

---

## 📌 КОНСТАНТИ CHEAT SHEET

```python
# ══ TCP Ports ═══════════════════════════════════
SLIM_TCP_PORT    = 3483   # SlimProto binary (audio commands)
SLIM_UDP_PORT    = 3483   # UDP Discovery
HTTP_PORT        = 9000   # CometD + JSON-RPC + Audio stream

# ══ Packet Header Size ══════════════════════════
DEVICE_TO_SERVER = 8      # 4 opcode + 4 length
SERVER_TO_DEVICE = 2      # 2 length (then 4 opcode inside body)

# ══ HELO Offsets ════════════════════════════════
HELO_DEVICE_ID   = 0      # 1 byte (7 = baby/radio)
HELO_RESERVED    = 1      # 1 byte (0)
HELO_MAC         = 2      # 6 bytes → body[2:8]
HELO_UUID        = 8      # 16 bytes
HELO_WLAN_FLAGS  = 24     # 2 bytes (0x4000 = reconnect)
HELO_BYTES_H     = 26     # 4 bytes
HELO_BYTES_L     = 30     # 4 bytes

# ══ strm-s Offsets ══════════════════════════════
STRM_COMMAND     = 0      # 's','q','p','u','t','f','a'
STRM_AUTOSTART   = 1      # '0'=manual, '1'=auto
STRM_FORMAT      = 2      # 'm'=MP3,'o'=OGG,'a'=AAC,'f'=FLAC,'p'=PCM
STRM_PCM_SIZE    = 3      # '?'=auto
STRM_PCM_RATE    = 4      # '?'=auto
STRM_PCM_CHAN    = 5      # '?'=auto
STRM_PCM_END     = 6      # '?'=auto
STRM_THRESHOLD   = 7      # 255
STRM_SPDIF       = 8      # 0
STRM_TRANS_PER   = 9      # 0
STRM_TRANS_TYPE  = 10     # '0'=none
STRM_FLAGS       = 11     # 0
STRM_OUT_THRESH  = 12     # 0
STRM_SLAVES      = 13     # 0
STRM_REPLAY_GAIN = 14     # 4 bytes = 0
STRM_SERVER_PORT = 18     # 2 bytes big-endian (9000)
STRM_SERVER_IP   = 20     # 4 bytes big-endian
STRM_HTTP_HDR    = 24     # variable: HTTP GET request

# ══ Audio Formats ════════════════════════════════
FMT_MP3  = ord('m')   # 109
FMT_FLAC = ord('f')   # 102
FMT_OGG  = ord('o')   # 111   (Opus, Vorbis)
FMT_AAC  = ord('a')   # 97
FMT_WMA  = ord('w')   # 119
FMT_PCM  = ord('p')   # 112

# ══ STAT Events (device → server) ════════════════
STAT_CONNECTED  = b"STMc"   # TCP stream connected
STAT_STARTED    = b"STMs"   # Playback started
STAT_TIMER      = b"STMt"   # Timer/keepalive response
STAT_DRAIN      = b"STMd"   # Buffer drained
STAT_NO_CONN    = b"STMn"   # Cannot connect to stream
STAT_PAUSED     = b"STMp"   # Paused
STAT_UNPAUSED   = b"STMu"   # Unpaused
STAT_BUFFER_OK  = b"STMl"   # Buffer full (start play)
STAT_OVERFLOW   = b"STMo"   # Buffer overflow
STAT_ACK        = b"STMa"   # Generic ack

# ══ Timeouts ════════════════════════════════════
DEVICE_READ_TIMEOUT  = 35   # s — устройството очаква пакет
KEEPALIVE_INTERVAL   = 25   # s — изпращай strm 't'
DISCOVERY_INTERVAL   = 60   # s — UDP broadcast (connected)
SEARCHING_INTERVAL   = 10   # s — UDP broadcast (searching)
SERVER_CLEANUP       = 120  # s — сървър без отговор → изтрий

# ══ CometD ══════════════════════════════════════
COMET_PATH       = "/cometd"
COMET_VERSION    = "1.0"
COMET_CONN_TYPE  = "streaming"

# ══ serverstatus ════════════════════════════════
PLAYER_COUNT_KEY = "player count"   # НЕ "player_count"!
PLAYERS_ARRAY    = "players_loop"

# ══ Menu / Browse ════════════════════════════════
BLOCK_SIZE       = 200   # items per browse chunk
HOME_NODE        = "home"
RADIO_ID         = "radio"   # НЕ "radios"!
```

---

## 🔧 СТАНДАРТИ ЗА РАЗРАБОТКА

### S1 — Python код конвенции

```python
# ✅ Константи в горната част на файла (CAPS)
SLIM_TCP_PORT = 3483
HTTP_PORT = 9000

# ✅ Всяка функция с docstring за протокол
async def _send_strm_play(url: str, name: str):
    """Send strm-s to Squeezebox. Format: §2.4 FIRMWARE_ANALYSIS.md"""

# ✅ Лог съобщения с [модул] prefix
log.info("[Slim] ✓ HELO від MAC=%s", mac)
log.warning("[strm] Няма TCP връзка")
log.error("[Comet] Handshake failed")

# ✅ Async функции за всичко мрежово
# ✅ Global variables само за state (_slim_writer, _device_mac, _now_playing)
# ✅ Type hints на всички функции
```

### S2 — JSON отговори към устройството

```python
# ✅ item_loop (НЕ loop_loop)
# ✅ Числа като числа, не strings: "count": 13 (не "13")
# ✅ Пропускай False полета (НЕ поставяй 0)
# ✅ Задължителни: offset, count при всеки browse response
# ✅ text (не name) за display

ПРАВИЛНО:
{
    "count": 13,
    "offset": 0,
    "item_loop": [
        {"text": "Jazz FM", "id": "jazz", "isaudio": 1,
         "actions": {"go": {"cmd": ["playlist","play"],
                            "params": {"url":"...", "title":"..."},
                            "nextWindow": "nowPlaying"}}}
    ]
}

ГРЕШНО:
{"count": "13", "loop_loop": [...], "items": [{"name":"Jazz FM","isaudio":0}]}
```

### S3 — TCP Binary протокол

```python
# ✅ Четене: readexactly(8) за header, после readexactly(length)
# ✅ БЕЗ asyncio.wait_for timeout на четенето
# ✅ SO_KEEPALIVE на socket при свързване
# ✅ _slim_send = 2-байт length + 4-байт opcode + payload
# ✅ strm 'q' преди strm 's'
# ✅ Keepalive: strm 't' на 25s

def _slim_send(writer, cmd: bytes, data: bytes):
    body = cmd + data                          # cmd = b"strm", b"vers" etc.
    writer.write(len(body).to_bytes(2,"big") + body)
```

### S4 — CometD отговори

```python
# ✅ Винаги масив (list), дори с един елемент
# ✅ clientId в ВСЕКИ отговор
# ✅ Subscription response: bundle в /meta/connect отговора
# ✅ advice.timeout = 0 (не 30000!)
# ✅ Channel prefix: /clientId/slim/... (НЕ /slim/...)

ПРАВИЛНО:
[
    {"channel": "/meta/connect", "successful": True, "clientId": "abc",
     "advice": {"reconnect": "retry", "timeout": 0, "interval": 0}},
    {"channel": "/abc/slim/serverstatus", "data": {...}, "id": 1}
]
```

### S5 — Логване и диагностика

```python
# Нива на логване:
log.debug(...)    # Детайли (packets, raw data) — само при DEBUG mode
log.info(...)     # Нормален поток: connected, playing, stopped
log.warning(...)  # Нещо не е наред, но работи: fallback, retry
log.error(...)    # Грешка: exception, failed command

# Задължителни INFO лог точки:
"[Slim] ✓ _slim_writer SET за {addr}"
"[Slim] HELO від MAC={mac}"
"[Slim] _slim_writer CLEARED (disconnect) {addr}"
"[strm] ✓ strm start (fmt={chr(fmt)}) → {name}"
"[Discovery] ✓ TLV отговор → {addr}"
"[Comet] handshake clientId={clientId}"
```

### S6 — Тестване / Верификация

```python
# При всяка промяна:
# 1. python -c "import ast; ast.parse(open('main.py').read())" — синтаксис
# 2. Рестартирай сървъра
# 3. Провери в лога: [Slim] ✓ _slim_writer SET
# 4. Избери станция от устройството
# 5. Провери: [strm] ✓ strm start

# Диагностична последователност:
# UDP discovery → TCP HELO → CometD handshake → menu → menustatus → play → strm-s
```

---

## 🐛 ЧЕСТИТЕ ГРЕШКИ → РЕШЕНИЯ

| Симптом | Причина | Решение | Секция |
|---------|---------|---------|--------|
| `_slim_writer` = None | TCP timeout изгонил handler | Виж: без read timeout, SO_KEEPALIVE | §2.6, §19.1 |
| Устройство не свири | Wrong format byte | Провери: `'m'`/`'o'`/`'a'` по URL | §7.4 |
| 0 станции в UI | `loop_loop` вместо `item_loop` | Смени на `item_loop` | §11 |
| hasitems игнориран | `hasitems: 0` (truthy в Lua!) | Пропусни ключа изцяло | §10.1 |
| "radios" не се показва | ID "radios" е filtrирано | Смени на ID "radio" | §12.4 |
| player count = 0 | `"player_count"` вместо `"player count"` | Ползвай интервал | §4.2 |
| Меню не се зарежда | Няма `offset` в отговора | Добави `"offset": 0` | §11.1 |
| NowPlaying не се показва | Няма `nextWindow:"nowPlaying"` | Добави в actions.go | §10.3 |
| CometD reconnect loop | advice.timeout != 0 | Върни `"timeout": 0` | §3.1 |
| strm не стига до device | serv команда изпратена | НИКОГА не изпращай serv | §19.1 |
| Аудио спира след 35s | Нямало keepalive | strm 't' на 25s | §2.6 |
| DNS грешка в stream-proxy | Сървърът не може DNS | Провери DNS на Windows | §7.2 |

---

## 📊 ПОТОК НА ДАННИТЕ — Connection Flow Diagram

```
DEVICE POWER ON
      │
      ▼
[UDP Broadcast] → 255.255.255.255:3483
      │  "eIPAD\0NAME\0JSON\0VERS\0UUID\0JVID\x06{MAC}"
      │
      ▼ сървърът отговаря
[UDP Response] ← server
      │  "E" + TLV(NAME,IPAD,JSON,VERS,UUID)
      │
      ▼
[TCP Connect] → server:3483
      │
      ▼
[HELO sent] → server
      │  opcode="HELO", MAC[6], UUID[16], caps="Model=baby,..."
      │
      ▼ сървърът отговаря
[vers received] ← server
      │  opcode="vers", version="7.7.3"
      │
      ▼ (паралелно)
[HTTP Connect] → server:9000/cometd
      │
      ▼
[CometD Handshake]
      │  POST /cometd  {"channel":"/meta/handshake","ext":{"mac":"..."}}
      │  ← {"successful":true,"clientId":"abc123"}
      │
      ▼
[CometD Connect + Subscribe /**]
      │  POST /cometd  [connect, subscribe("abc123/**")]
      │
      ▼
[Auto-Subscriptions (SlimServer.lua)]
      │  subscribe: serverstatus (0, 50, "subscribe:60")
      │  subscribe: firmwarestatus
      │
      ▼
[Player subscriptions (Player.lua)]
      │  subscribe: /slim/playerstatus/{MAC} (subscribe:600)
      │  subscribe: /slim/displaystatus/{MAC}
      │
      ▼
[Menu request (SlimMenus.lua)]
      │  request: ["menu", 0, 100, "direct:1"]
      │  ← {item_loop: [{id:"radio", node:"home", ...}]}
      │
      ▼
[menustatus subscription]
      │  subscribe: /slim/menustatus/{MAC}
      │
      ▼
[HOME MENU SHOWN — устройството е готово]
      │
      │ (потребителят избира станция)
      ▼
[RPC: playlist play]
      │  ["playlist","play","url","name"]  или
      │  actions.go.cmd → ["radios","items"] → browse → play
      │
      ▼
[_send_strm_play() в main.py]
      │  strm 'q' (stop current)
      │  strm 's' (start new: serverIp, serverPort, HTTP GET /stream?url=...)
      │
      ▼
[DEVICE connects to server:9000]
      │  GET /stream?url=https://...  HTTP/1.0
      │  ← audio stream (chunked bytes)
      │
      ▼
[DEVICE PLAYING 🎵]
      │  STAT "STMc" → connected to stream
      │  STAT "STMs" → started playing
      │  STAT "STMt" → keepalive responses
```

---

## 🔍 KEYWORD INDEX — Азбучен списък

> `Ctrl+F` → търси думата → виж секцията

| Ключова дума | Секция | Бележка |
|---|---|---|
| `advice.timeout` | §3.1 | Задължително = 0 |
| audio format byte | §7.4 | `'m'`/`'o'`/`'a'`/`'f'`/`'p'` |
| autostart byte | §2.4 | `'1'` за auto |
| browse chunk | §11.1 | item_loop + count + offset |
| CometD handshake | §3.1 | POST /cometd |
| clientId | §3.1 | UUID в отговора |
| `displaystatus` | §8.5 | `/slim/displaystatus/{mac}` |
| DNS error | §7.2 | Windows DNS, не код |
| ffplay / ffmpeg | §7.2 | Локален fallback плейър |
| format auto-detect | §7.4 | По URL pattern |
| `firmwarestatus` | §4.3 | `"upgradeUrl": null` |
| HELO | §2.2 | MAC на offset 2, UUID на 8 |
| `hasitems` | §10.1 | 1 = папка, пропусни ако 0 |
| HTTP stream proxy | §7.2 | `/stream?url=...` |
| `icy-name` | §7.2 | Header в stream proxy |
| `id:"radio"` | §12.4 | НЕ "radios" |
| `item_loop` | §11.1 | НЕ `loop_loop` |
| `isaudio` | §10.1 | 1 = пусни директно |
| JSON-RPC | §3.4 | POST /jsonrpc.js |
| keepalive | §2.6 | `strm 't'` на 25s |
| HELO wlanFlags | §2.2 | 0x4000 = reconnect |
| `menustatus` | §12.2 | `[playerid, items, "add", playerid]` |
| menu request | §12.1 | `["menu", 0, 100, "direct:1"]` |
| `nextWindow` | §10.3 | `"nowPlaying"`, `"home"` |
| `node:"home"` | §12.3 | За home menu items |
| `nowPlaying` | §13 | RPC + nextWindow |
| `offset` | §11.1 | Задължителен в browse response |
| `player count` | §4.2 | С интервал, не underscore |
| `players_loop` | §4.2 | Масив с плейъри |
| `playerstatus` | §8.3–8.4 | `/slim/playerstatus/{mac}` |
| pagination | §10.4 | BLOCK_SIZE=200 |
| reconnect | §2.7 | Auto-replay strm-s при HELO |
| `remote` | §8.4 | 1 = internet radio |
| `serv` command | §19.1 | НИКОГА не изпращай |
| `SO_KEEPALIVE` | §2.6, §19.1 | OS-level keepalive |
| `serverstatus` | §4.2 | Полета + update: subscribe |
| `strm-s` | §2.4 | 24 байта + HTTP GET |
| `strm 'q'` | §2.4 | Спри преди нов strm 's' |
| `strm 't'` | §2.6 | Keepalive ping |
| STAT events | §2.3 | STMc, STMs, STMt, STMd... |
| subscription | §3.3 | POST /cometd, bundled |
| TCP packet format | §2.1 | Header асиметричен! |
| TLV | §5.2 | UDP response encoding |
| UDP discovery | §5.1–5.2 | Port 3483, `'e'`/`'E'` |
| `vers` | §2.5 | Отговор след HELO |
| volume | §7.3 | 100-level table |
| `wait_for` timeout | §19.1 | НИКОГА — изтрива writer |
| wlanFlags | §2.2 | offset 24 в HELO body |

---

## ⚙️ DEVELOPMENT WORKFLOWS — Стъпки за чести задачи

### W1 — Добавяне на нова станция

```
1. stations.json → добави { "name": "...", "url": "...", "genre": "...", "country": "BG" }
2. Формат: opus URL → format='o', aac URL → format='a', всичко друго → 'm'
3. Рестартирай сървъра → НЕ се изисква смяна на код
4. Тест: избери от устройство → виж "[strm] ✓ strm start"
```

### W2 — Добавяне на нова RPC команда (device → server)

```
1. Намери в SlimBrowserApplet.lua или Player.lua какво изпраща устройството
2. В dispatch_rpc():
   - Добави elif cmd[0] == "ново_действие":
   - Обработи params
   - Върни CometD отговор (масив!)
3. Стандарт: item_loop + count + offset
4. Логвай: log.info("[Comet] ново_действие → %s", params)
5. S6 тест: syntax check → рестарт → тест от устройство
```

### W3 — Дебъгване на спрян аудио

```
1. Провери: [Slim] ✓ _slim_writer SET → ако не: TCP проблем (firewall/port)
2. Провери: [strm] ✓ strm start → ако не: _send_strm_play не се извикала
3. Провери: [stream-proxy] → ако 404/DNS: URL проблем (виж S5 логове)
4. Провери STAT в лога: STMc=свързано, STMn=не може да се свърже
5. Тест с прост MP3 URL: http://icecast.radiofrance.fr/fip-midfi.mp3
```

### W4 — Дебъгване на меню (0 станции / не се зарежда)

```
1. Провери response от /jsonrpc.js: има ли item_loop? offset? count?
2. Провери: id="radio" (не "radios")
3. Провери: hasitems/isaudio — пропуснати ли са когато са 0?
4. Провери: text (не name) в item_loop елементите
5. Провери: actions.go.cmd присъства ли за всеки hasitems:1 елемент
```

### W5 — Нов CometD subscription тип

```
1. SlimServer.lua §4 → разгледай какви subscription path-ове изпраща
2. В handle_subscribe(): добави elif channel.endswith("/slim/нов_канал"):
3. Пуш: изпрати в connected_clients[clientId] response
4. Формат: {"channel": "/clientId/slim/нов_канал", "data": {...}}
5. Тест: монитор CometD POST response в лога
```

### W6 — Pre-commit чеклист

```bash
# 1. Синтаксис
python -c "import ast; ast.parse(open('main.py').read()); print('OK')"

# 2. Ключови думи да НЕ са в кода:
grep -n "loop_loop\|player_count\|\"radios\"\|wait_for.*readexactly\|\"serv\"" main.py

# 3. Рестарт + наблюдение
python main.py 2>&1 | head -50

# 4. Чеклист:
# [ ] item_loop (не loop_loop)
# [ ] "player count" (с интервал)
# [ ] id="radio" (не "radios")
# [ ] advice.timeout=0
# [ ] strm 'q' преди strm 's'
# [ ] Без wait_for timeout на readexactly
```

---

## Съдържание

1. [Обща архитектура](#1-обща-архитектура)
2. [SlimProto.lua — TCP бинарен протокол (порт 3483)](#2-slimprotolua--tcp-бинарен-протокол-порт-3483)
3. [Comet.lua — Bayeux/CometD протокол](#3-cometlua--bayeuxcometd-протокол)
4. [SlimServer.lua — Управление на връзката към сървър](#4-slimserverlua--управление-на-връзката-към-сървър)
5. [SlimDiscovery.lua — UDP Autodiscovery](#5-slimdiscoverylua--udp-autodiscovery)
6. [SetupWelcome.lua — Първоначален Setup Wizard](#6-setupwelcomelua--първоначален-setup-wizard)
7. [Playback.lua — Аудио Engine](#7-playbacklua--аудио-engine)
8. [Player.lua — Управление на плейъра](#8-playerlua--управление-на-плейъра)
9. [SqueezeboxBaby — Хардуерен Applet](#9-squeezboxbaby--хардуерен-applet)
10. [SlimBrowserApplet.lua — Browse Engine](#10-slimbrowserappletlua--browse-engine)
11. [DB.lua — База данни за Browse Items](#11-dblua--база-данни-за-browse-items)
12. [SlimMenus.lua — Главно Меню](#12-slimmenulua--главно-меню)
13. [NowPlaying.lua — Екран „Сега Свири"](#13-nowplayinglua--екран-сега-свири)
14. [ChooseMusicSource.lua — Избор на Сървър](#14-choosemusicsourcelua--избор-на-сървър)
15. [SelectPlayer.lua — Избор на Плейър](#15-selectplayerlua--избор-на-плейър)
16. [JiveMain.lua — Начало на приложението](#16-jivemainlua--начало-на-приложението)
17. [Networking.lua — WiFi/Мрежов Мениджър](#17-networkinglua--wifimрежов-мениджър)
18. [Пълен списък на всички файлове](#18-пълен-списък-на-всички-файлове)
19. [Критични правила за сървъра](#19-критични-правила-за-сървъра)

---

## 1. Обща архитектура

Squeezebox Radio използва **три независими канала** за комуникация с LMS сървъра:

```
┌──────────────────────────────────────────────────────────────┐
│                  Squeezebox Radio Device                     │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │  Jive UI /   │   │  CometD      │   │  SlimProto       │ │
│  │  Applets     │──▶│  (HTTP)      │──▶│  (TCP binary)    │ │
│  │  (Lua)       │   │  Port 9000   │   │  Port 3483       │ │
│  └──────────────┘   └──────────────┘   └──────────────────┘ │
│         │                  │                    │            │
│   Menus/Browse     Commands/Status        Audio stream       │
└──────────────────────────────────────────────────────────────┘
         │                  │                    │
         ▼                  ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│                      LMS Server                              │
│   /jsonrpc.js       /cometd           Port 3483 TCP          │
│   JSON-RPC          Bayeux            strm commands          │
└──────────────────────────────────────────────────────────────┘
```

### Стек на протоколите

| Слой | Протокол | Файл | Порт |
|------|---------|------|------|
| UDP Discovery | TLV broadcast | `SlimDiscovery.lua` | UDP 3483 |
| HTTP/JSON-RPC | REST-like API | `SlimServer.lua` | TCP 9000 |
| CometD/Bayeux | HTTP long-poll pub/sub | `Comet.lua` | TCP 9000 |
| SlimProto | Binary TCP | `SlimProto.lua` | TCP 3483 |
| Audio stream | HTTP/1.0 GET | `Playback.lua` | TCP 9000 |

### Последователност при свързване

```
1. UDP broadcast → 255.255.255.255:3483  "eIPAD\0NAME\0JSON\0VERS\0UUID\0"
2. Сървърът отговаря с TLV пакет: "E" + NAME+IPAD+JSON+VERS+UUID
3. Устройството отваря CometD (HTTP) към 9000 → Bayeux handshake
4. Устройството прави RPC: serverstatus, menu, menustatus subscription
5. Устройството отваря TCP към 3483 → изпраща HELO пакет
6. Сървърът отговаря с vers пакет
7. При play: сървърът изпраща strm-s → устройството GET /stream HTTP/1.0
```

---

## 2. SlimProto.lua — TCP бинарен протокол (порт 3483)

**Файл:** `jive_dump/usr/share/jive/jive/net/SlimProto.lua`  
**Роля:** Имплементира бинарния TCP протокол за аудио контрол.

### 2.1 Формат на пакетите

#### Устройство → Сървър

```
[4 байта ASCII opcode][4 байта length (big-endian)][length байта payload]

Пример: "HELO" + \x00\x00\x00\x4A + <74 байта>
```

#### Сървър → Устройство

```
[2 байта length (big-endian)][4 байта ASCII opcode][payload]

Пример: \x00\x08 + "vers" + "7.7.3..."
```

> ⚠️ **КРИТИЧНО:** Посоките имат различен формат! Устройството изпраща с 4-байтов header, сървърът — с 2-байтов.

### 2.2 HELO пакет (Устройство → Сървър, при свързване)

```
Offset  Size  Поле               Описание
------  ----  -----              --------
0       1     deviceID           7 = Jive/Baby/Radio
1       1     reserved           0x00
2       6     MAC address        Бинарен MAC (6 байта)
8       16    UUID               Бинарен UUID (16 байта)
24      2     wlanFlags          0x4000 = reconnect bit
26      4     bytesReceivedH     За resume на стрийм
30      4     bytesReceivedL     За resume на стрийм
34      2     locale             "EN" или друг
36      var   capabilities       Comma-sep: "Model=baby,ModelName=Squeezebox Radio,Firmware=7.7.3-r16676"
```

**Важни полета в Python:**
```python
mac = ":".join(f"{b:02x}" for b in body[2:8])   # body е payload след 8-байтов header
```

### 2.3 STAT пакет (Устройство → Сървър, статус)

```
Offset  Size  Поле
------  ----  -----
0       4     event            ASCII ("STMs"=started, "STMc"=connected,
                                      "STMt"=timer, "STMd"=drain, "STMn"=no connection,
                                      "STMp"=pause, "STMu"=unpause, "STMl"=full,
                                      "STMo"=overflow, "STMa"=ack)
4       1     num_crlf         Unused
5       2     mas_initialized  Unused
7       4     decode_size      Байтове в decode буфера (текущо)
11      4     decode_full      Размер на decode буфера
15      4     bytesReceivedH   Получени байтове (high)
19      4     bytesReceivedL   Получени байтове (low)
23      2     signal_strength  WiFi сигнал (-1 = N/A)
25      4     elapsed_jiffies  Изминало време (jiffies)
29      4     output_size      Изходен буфер (текущо)
33      4     output_full      Изходен буфер (пълен размер)
37      4     elapsed_seconds  Изминали секунди (playback)
41      2     voltage          Напрежение (0 = N/A)
43      4     elapsed_ms       Изминали milliseconds
47      4     server_timestamp Timestamp от сървъра
```

### 2.4 strm команди (Сървър → Устройство)

Всички аудио команди са `strm` пакети с различен sub-command байт:

```
Байтова структура на strm payload:
Offset  Size  Поле             Стойности
------  ----  -----            ---------
0       1     command          's'=start, 'q'=stop, 'u'=unpause, 'p'=pause,
                               't'=timer/status, 'f'=flush, 'a'=skip
1       1     autostart        '0'=manual, '1'=auto на fill
2       1     format           'm'=MP3, 'f'=FLAC, 'o'=OGG/Opus, 'a'=AAC,
                               'w'=WMA, 'p'=PCM, '9'=Vorbis
3       1     pcmSampleSize    '?'=auto, '0'=8bit, '1'=16bit, '2'=24bit, '3'=32bit
4       1     pcmSampleRate    '?'=auto, '3'=44100Hz, '4'=48000Hz, '0'=11025Hz...
5       1     pcmChannels      '?'=auto, '1'=mono, '2'=stereo
6       1     pcmEndianness    '?'=auto, '0'=big, '1'=little
7       1     threshold        Буфер threshold (KB), обикновено 255
8       1     spdifEnable      0=off, 1=on
9       1     transitionPeriod Crossfade period (0=none)
10      1     transitionType   '0'=none, '1'=crossfade
11      1     flags            Misc flags
12      1     outputThreshold  Output буфер threshold
13      1     slaves           Slave count (sync groups)
14      4     replayGain       Replay gain (big-endian uint32)
18      2     serverPort       HTTP порт (big-endian), обикновено 9000
20      4     serverIp         IP адрес (big-endian uint32)
24      var   httpHeaders      HTTP заявка (string)
```

**Примерен strm-s пакет за MP3 стрийм:**
```python
ip_bytes   = bytes(int(p) for p in server_ip.split("."))   # big-endian
port_bytes = struct.pack("!H", 9000)

strm_payload = bytes([
    ord('s'),   # start
    ord('1'),   # autostart
    ord('m'),   # MP3 format
    ord('?'), ord('?'), ord('?'), ord('?'),  # pcm auto
    255,        # threshold (KB)
    0,          # spdif off
    0,          # no crossfade
    ord('0'),   # transition=none
    0,          # flags
    0,          # output threshold
    0,          # no slaves
    0, 0, 0, 0, # replay gain = 0
]) + port_bytes + ip_bytes + b"GET /stream?url=... HTTP/1.0\r\nHost: ...\r\n\r\n"
```

### 2.5 Останали server→device команди

| Opcode | Payload | Описание |
|--------|---------|---------|
| `vers` | ASCII version string | Отговор на HELO — задължителен |
| `serv` | 4 байта IP + 11 байта SyncGroupID | Redirect към друг сървър — **НЕ изпращай!** |
| `dsco` | 1 байт reason | Disconnect заповед |
| `aude` | 1 байт (pos 0) | Аудио enable/disable |
| `audg` | gainL(4) + gainR(4) + [flags(1)] + [preamp(1)] + [advGain(8)] + [seqNo(4)] | Volume gain |
| `cont` | metaInterval(4) + loopFlag(1) + guidLen(2) + guid(var) | Stream continuation |
| `grfe` | N/A | Graphics frame — игнорира се |
| `grfs` | N/A | Graphics frame size — игнорира се |
| `geek` | 1 байт flag | IR blaster geekmode |
| `blst` | var string | IR blaster команда |

### 2.6 Таймаути и keepalive

```
READ_TIMEOUT  = 35s   (устройството очаква данни в рамките на 35s)
WRITE_TIMEOUT = 10s   (записване)

Keepalive от сървъра: strm 't' пакет (timer request)
  → Устройството отговаря с STAT "STMt"
  
Препоръчителен интервал: всеки 25s (< READ_TIMEOUT от 35s)
```

### 2.7 Reconnect логика

```lua
-- Устройството автоматично reconnect-ва след:
-- 1. dsco от сървъра
-- 2. TCP грешка / timeout
-- 3. serv команда (redirect)

-- При reconnect, HELO съдържа:
wlanFlags = 0x4000   -- reconnect bit е вдигнат
bytesReceived = последните стойности от стрийма (за resume)
```

---

## 3. Comet.lua — Bayeux/CometD протокол

**Файл:** `jive_dump/usr/share/jive/jive/net/Comet.lua` (и `net_dump/`)  
**Роля:** Имплементира Bayeux protocol v1.0 за pub/sub комуникация.

### 3.1 Свързване — Handshake

**Стъпка 1: Handshake (POST /cometd)**
```json
[{
  "channel": "/meta/handshake",
  "version": "1.0",
  "supportedConnectionTypes": ["streaming"],
  "ext": {
    "rev": "7.7.3",
    "uuid": "device-uuid-string",
    "mac": "00:04:20:2c:90:b1"
  }
}]
```

**Отговор:**
```json
[{
  "channel": "/meta/handshake",
  "successful": true,
  "clientId": "f1c8ab45...",
  "advice": {
    "reconnect": "retry",
    "timeout": 0,
    "interval": 5000
  }
}]
```

**Стъпка 2: Connect + Subscribe (единствен POST)**
```json
[
  {
    "channel": "/meta/connect",
    "clientId": "f1c8ab45...",
    "connectionType": "streaming"
  },
  {
    "channel": "/meta/subscribe",
    "clientId": "f1c8ab45...",
    "subscription": "/f1c8ab45/**"
  }
]
```

### 3.2 Subscription (slim/subscribe)

```json
{
  "channel": "/slim/subscribe",
  "id": 1,
  "clientId": "f1c8ab45...",
  "data": {
    "request": ["playerid_or_empty", ["command", "arg1", "arg2"]],
    "response": "/f1c8ab45/slim/serverstatus",
    "priority": 0
  }
}
```

Отговорите идват на `response` канала, чрез poll-ване на `/meta/connect`.

### 3.3 Request (slim/request) — еднократна заявка

```json
{
  "channel": "/slim/request",
  "id": 5,
  "clientId": "f1c8ab45...",
  "data": {
    "request": ["playerid", ["menu", 0, 100, "direct:1"]],
    "response": "/f1c8ab45/slim/request",
    "priority": 0
  }
}
```

Отговорът идва на `/f1c8ab45/slim/request` с `id=5`.

### 3.4 Unsubscribe

```json
{
  "channel": "/slim/unsubscribe",
  "id": 2,
  "clientId": "f1c8ab45...",
  "data": {
    "unsubscribe": "/f1c8ab45/slim/serverstatus"
  }
}
```

### 3.5 Reconnect логика

| Сценарий | Действие |
|---------|---------|
| Загуба на TCP, clientId жив | `/meta/reconnect` с текущия clientId |
| advice.reconnect = "handshake" | Нов handshake (нов clientId) |
| advice.reconnect = "none" | Спиране — не се прави reconnect |
| HTTP грешка | Backoff: `retry_interval = advice.interval × failures` |

### 3.6 Batch заявки

Устройството може да групира множество операции:
```lua
comet:startBatch()
comet:subscribe(channel1, callback1, ...)
comet:subscribe(channel2, callback2, ...)
comet:request(cb, playerid, {...})
comet:endBatch()   -- всичко се изпраща в един HTTP POST
```

### 3.7 Формат на отговорите

Всеки отговор е масив от обекти:
```json
[
  {
    "channel": "/f1c8ab45/slim/serverstatus",
    "id": 1,
    "clientId": "f1c8ab45...",
    "data": { ... }
  },
  {
    "channel": "/meta/connect",
    "successful": true,
    "advice": { "timeout": 0 }
  }
]
```

---

## 4. SlimServer.lua — Управление на връзката към сървър

**Файл:** `jive_dump/usr/share/jive/jive/slim/SlimServer.lua`  
**Роля:** Управлява CometD връзката и парсира `serverstatus`.

### 4.1 Автоматични subscriptions при connect

При `server:connect()` устройството веднага прави 2 абонамента:

#### 1. serverstatus
```json
{
  "channel": "/slim/subscribe",
  "data": {
    "request": ["", ["serverstatus", 0, 50, "subscribe:60"]],
    "response": "/clientId/slim/serverstatus",
    "priority": 0
  }
}
```

#### 2. firmwarestatus (само за устройство, не squeezeplay)
```json
{
  "channel": "/slim/subscribe",
  "data": {
    "request": ["", ["firmwareupgrade", "firmwareVersion:7.7.3-r16676",
                     "inSetup:0", "machine:baby", "subscribe:0"]],
    "response": "/clientId/slim/firmwarestatus"
  }
}
```

### 4.2 serverstatus отговор — задължителни полета

```json
{
  "player count": "1",
  "players_loop": [
    {
      "playerid": "00:04:20:2c:90:b1",
      "connected": "1",
      "seq_no": 0,
      "pin": null
    }
  ],
  "rescan": "0"
}
```

> ⚠️ **КРИТИЧНО:** Полето е `"player count"` (с **интервал**, НЕ долна черта).  
> Lua код (ред 240): `if tonumber(data["player count"]) > 0 then`

### 4.3 Notifications изпращани от SlimServer

| Notification | Кога |
|-------------|------|
| `serverNew` | Сървърът е открит за пръв път |
| `serverDelete` | Сървърът е недостъпен |
| `serverConnected` | CometD връзката е установена |
| `serverDisconnected` | CometD връзката е изгубена |
| `serverLinked` | PIN регистрация в SqueezeNetwork |
| `serverRescanning` | Библиотека се rescanning |
| `serverRescanDone` | Rescan завършен |
| `firmwareAvailable` | Налично firmware обновление |

### 4.4 RPC заявки

```lua
-- Еднократна заявка с callback
server:userRequest(callback, playerId, {"menu", 0, 100, "direct:1"})

-- Background заявка
server:request(callback, playerId, {"status", "-", 10, "subscribe:600"})
```

---

## 5. SlimDiscovery.lua — UDP Autodiscovery

**Файл:** `jive_dump/usr/share/jive/applets/SlimDiscovery/SlimDiscoveryApplet.lua`  
**Роля:** Открива LMS сървъри по UDP broadcast.

### 5.1 Discovery пакет (Устройство → Broadcast)

```
Порт:    UDP 3483
Адрес:   255.255.255.255

Структура:
  Байт 0:   'e' (0x65) — discovery request
  TLV трипли (Tag=4 байта, Len=1 байт, Value=len байта):
    "IPAD" + \x00   (заявява IP адрес)
    "NAME" + \x00   (заявява server name)
    "JSON" + \x00   (заявява JSON-RPC порт)
    "VERS" + \x00   (заявява version)
    "UUID" + \x00   (заявява server UUID)
    "JVID" + \x06 + MAC[6]  (device MAC, 6 байта)
```

### 5.2 Discovery отговор (Сървър → Устройство)

```
Структура:
  Байт 0:   'E' (0x45) — discovery response
  TLV трипли:
    "NAME" + len + server_name
    "IPAD" + len + ip_address (string)
    "JSON" + len + json_port (string, обикновено "9000")
    "VERS" + len + version
    "UUID" + len + uuid
```

**Пример Python:**
```python
def _tlv(tag: str, val: bytes) -> bytes:
    return tag.encode("ascii") + bytes([len(val)]) + val

response = (
    b"E"
    + _tlv("NAME", b"MyServer")
    + _tlv("IPAD", b"192.168.1.43")
    + _tlv("JSON", b"9000")
    + _tlv("VERS", b"7.9.0")
    + _tlv("UUID", b"my-server-uuid")
)
```

### 5.3 Discovery интервали и таймаути

```
Interval (CONNECTED state):   60 000 ms (60s)
Interval (SEARCHING state):   10 000 ms (10s)
Probe window:                 60s след началото
Server cleanup timeout:       120 000 ms (120s — без отговор)
Player cleanup timeout:       120 000 ms
```

### 5.4 State Machine

```
'disconnected'  → Без активни връзки, таймерът спрян
       ↓
'searching'     → Активно търсене, connect към всички открити сървъри
       ↓
'probing_player'→ Свързан, но update на плейъри (UDAP + wireless scan)
       ↓
'probing_server'→ Свързан, но update само на сървъри (без UDAP)
       ↓
'connected'     → Свързан към текущия плейър, background scan на 60s
```

### 5.5 UDAP — Discovery на Squeezebox устройства

UDAP е протокол за discovery на **некофигурирани** (factory reset) Squeezebox устройства:
```
Broadcast: UDP 17784
Method:    "adv_discover"
Status:    "wait_slimserver"
Types:     "squeezebox", "fab4", "baby"
```

---

## 6. SetupWelcome.lua — Първоначален Setup Wizard

**Файл:** `jive_dump/usr/share/jive/applets/SetupWelcome/SetupWelcomeApplet.lua`  
**Роля:** Управлява first-boot setup процеса.

### 6.1 Setup стъпки

```
step1() → Избор на език (SetupLanguage service)
step2() → Welcome screen
step3() → Мрежова конфигурация (SetupNetworking service)
step6() → Автоматичен избор на local player
step7() → Регистрация в SqueezeNetwork
  ├─ _registerRequest() → RPC: ["register", 0, 100, "service:SN"]
  ├─ _squeezenetworkWait() → 30s polling
  └─ notify_serverLinked → step9()
step8() → Firmware upgrade check
step9() → Cleanup, goHome()
```

### 6.2 Hostname стратегия

```lua
-- SqueezeNetwork hostname (от SqueezeboxBabyMeta.lua):
jnt:setSNHostname("baby.squeezenetwork.com")

-- Достъпва се с:
jnt:getSNHostname()  → "baby.squeezenetwork.com"
                    → заместено от нас с "192.168.1.43" чрез hosts файл
```

### 6.3 DNS хиджак проверка

При неуспех, `_squeezenetworkFailed()` проверява дали DNS резолюцията върна **private IP**:
```lua
-- Счита за DNS hijacking:
192.168.x.x
172.16.0.0/12
10.x.x.x
```

Затова нашият сървър трябва да е на LAN IP (не loopback).

---

## 7. Playback.lua — Аудио Engine

**Файл:** `jive_dump/usr/share/jive/jive/audio/Playback.lua`  
**Роля:** Управлява мрежовия аудио стрийм, декодера и хардуерния изход.

### 7.1 SlimProto опкодове, на които е абониран

| Opcode | Handler | Действие |
|--------|---------|---------|
| `strm` | `_strm()` | Процесира start/stop/pause/flush команди |
| `cont` | `_cont()` | Loop flag, ICY meta interval |
| `audg` | `_audg()` | Volume gain update |
| `aude` | N/A | Audio enable/disable (mute) |
| `setd` | N/A | Set display name |
| `reconnect` | N/A | Trigger reconnect |

### 7.2 Команда `strm-s` — Как устройството стартира стрийм

```
1. Получава strm пакет с command='s'
2. Парсира: format, serverIp, serverPort, httpHeaders
3. Отваря TCP conn към serverIp:serverPort
4. Изпраща httpHeaders (HTTP GET заявка)
5. Получава HTTP отговор (чете Content-Type, Icy-MetaInt)
6. Подава стрийма на аудио декодера по format ('m'=MP3 etc.)
7. Декодерът напълва output буфера
8. При autostart='1': пуска при запълване на threshold
9. Изпраща STAT "STMc" (connected) и STAT "STMt" (playing)
```

### 7.3 Volume mapping таблица

Устройството има 100-елементна таблица за преобразуване volume(1-100) → hardware gain:

```
Volume 1   → Gain 16     (почти тихо)
Volume 50  → Gain ~4096  (средно)
Volume 100 → Gain 65536  (максимум)
```

Кривата е асиметрична (Boom volume curve) — агресивен нарастеж при ниски нива.

### 7.4 Codec формати

| Байт | Формат | Mime type |
|------|--------|---------|
| `'m'` | MP3 | audio/mpeg |
| `'f'` | FLAC | audio/flac |
| `'o'` | OGG/Opus | audio/ogg |
| `'a'` | AAC | audio/aac |
| `'w'` | WMA | audio/wma |
| `'p'` | PCM/WAV | audio/wav |
| `'9'` | OGG Vorbis | audio/ogg |
| `'t'` | Tone generator | — |

---

## 8. Player.lua — Управление на плейъра

**Файл:** `jive_dump/usr/share/jive/jive/slim/Player.lua`  
**Роля:** Представя плейъра, изпраща RPC команди, парсира playerstatus.

### 8.1 Device ID → Model name

```lua
DEVICE_IDS = {
  [2]  = "squeezebox",
  [3]  = "softsqueeze",
  [4]  = "squeezebox2",
  [5]  = "transporter",
  [6]  = "softsqueeze3",
  [7]  = "receiver",
  [8]  = "squeezeslave",
  [9]  = "controller",
  [10] = "boom",
  [11] = "softboom",
  [12] = "squeezeplay",
}
```

### 8.2 RPC команди изпращани от плейъра

| Команда | Параметри | Цел |
|---------|----------|-----|
| `status` | `"-", 10, "menu:menu", "useContextMenu:1", "subscribe:600"` | Получи + абонирай playerstatus |
| `displaystatus` | `"subscribe:showbriefly"` | Кратки display съобщения |
| `mode` | `"play"` или `"stop"` | Пусни/спри |
| `pause` | `"0"` или `"1"` | Unpause/Pause |
| `power` | `"0"/"1", "seq_no:N"` | On/Off с sequence |
| `volume` | `vol, "seq_no:N"` | Ниво на звука |
| `mute` | `mute_state, "seq_no:N"` | Заглуши |
| `playlist` | `"index", idx` | Скочи на запис |
| `playlist` | `"delete", idx` | Изтрий от плейлист |
| `button` | `"power"` | Симулира бутон |
| `repeatToggle` | — | Превключи repeat режим |
| `shuffleToggle` | — | Превключи shuffle режим |

### 8.3 CometD subscription за playerstatus

```json
{
  "channel": "/slim/subscribe",
  "data": {
    "request": ["00:04:20:2c:90:b1", ["status", "-", 10,
                "menu:menu", "useContextMenu:1", "subscribe:600"]],
    "response": "/clientId/slim/playerstatus/00:04:20:2c:90:b1"
  }
}
```

### 8.4 playerstatus отговор — прочитани полета

```json
{
  "mode": "play",
  "power": 1,
  "player_connected": 1,
  "mixer volume": 70,
  "time": 45.3,
  "duration": 0,
  "rate": 1,
  "playlist_tracks": 1,
  "playlist_cur_index": "0",
  "playlist shuffle": 0,
  "playlist repeat": 0,
  "alarm_state": "none",
  "alarm_next": 0,
  "sleep": 0,
  "item_loop": [
    {
      "track": "Station Name",
      "artist": "",
      "album": "",
      "icon-id": "artwork-id",
      "params": { "track_id": "radio-123" }
    }
  ]
}
```

### 8.5 Notifications изпращани от Player

```
playerNew / playerDelete
playerConnected / playerDisconnected
playerPower
playerModeChange
playerTrackChange
playerPlaylistChange
playerShuffleModeChange / playerRepeatModeChange
playerNewName
playerDigitalVolumeControl
playerNeedsUpgrade
playerAlarmState
```

---

## 9. SqueezeboxBaby — Хардуерен Applet

### 9.1 SqueezeboxBabyMeta.lua

**Файл:** `jive_dump/usr/share/jive/applets/SqueezeboxBaby/SqueezeboxBabyMeta.lua`

Ключова регистрация:
```lua
LocalPlayer:setDeviceType("baby", "Squeezebox Radio")
SlimServer:setMinimumVersion("7.4")
jnt:setSNHostname("baby.squeezenetwork.com")    -- ← заместваме с наш IP!
```

Services регистрирани:
```
getBrightness / setBrightness
getWakeupAlarm / setWakeupAlarm
getDefaultWallpaper
poweroff / reboot
lowBattery / lowBatteryCancel / isBatteryLow
wasLastShutdownUnclean
isLineInConnected
overrideAudioEndpoint(override)   -- force Speaker/Headphone/default
```

### 9.2 SqueezeboxBabyApplet.lua — Хардуерни системни файлове

| Системен файл | Цел | Стойности |
|---------------|-----|---------|
| `/sys/class/backlight/mxc_lcdc_bl.0/brightness` | LCD brightness | 0–100 |
| `/sys/class/backlight/mxc_lcdc_bl.0/bl_power` | Backlight on/off | 0=on, 1=off |
| `/sys/bus/i2c/devices/1-0010/ambient` | Ambient light sensor | lux |
| `/sys/devices/.../battery_charge` | Battery заряд (mAh) | read-only |
| `/sys/devices/.../battery_capacity` | Battery капацитет (mAh) | read-only |
| `/sys/devices/.../charger_state` | Зарядно устройство | bits |
| `/sys/bus/i2c/devices/1-0010/alarm_time` | Wake-up alarm epoch | read/write |

### 9.3 Power State Machine

```
ACTIVE  (30s inactivity)→  IDLE  (10min)→  SLEEP  (20min)→  HIBERNATE
  ↑                          ↓               ↓                  ↓
  └── User input ────────────┘    Audio off  Off           Suspend/Poweroff
```

Таймаути:
```lua
idleTimeout      = 30 000 ms    (30s ACTIVE→IDLE)
sleepTimeout     = 600 000 ms   (10min IDLE→SLEEP)
hibernateTimeout = 1 200 000 ms (20min SLEEP→HIBERNATE)
```

### 9.4 Audio Routing

| Endpoint | Кога | Crossover |
|----------|------|---------|
| Speaker | ACTIVE/IDLE, без слушалки | ON |
| Headphone | Jack е включен | OFF (предотвратява пукове) |
| Off | SLEEP/HIBERNATE | — |

### 9.5 Battery Monitoring

| Charger State | Значение |
|--------------|---------|
| 1 | Без батерия (само AC) |
| 2 | AC, батерията е заредена |
| 3 | На батерия |
| 3 + bit5 | Критично ниска батерия |
| state & 8 | Зарежда се |

---

## 10. SlimBrowserApplet.lua — Browse Engine

**Файл:** `net_dump/usr/share/jive/applets/SlimBrowser/SlimBrowserApplet.lua`  
**Роля:** Управлява йерархичната навигация в менютата.

### 10.1 Задължителни полета в item_loop

```json
{
  "text": "Станция Name",
  "id": "station-123",
  "node": "home",
  "hasitems": 1,
  "isaudio": 1,
  "actions": {
    "go": {
      "cmd": ["radios", "items"],
      "params": { "item_id": "123" }
    }
  }
}
```

> ⚠️ **КРИТИЧНО в Lua:** `0` е **truthy**! Никога не поставяй `hasitems: 0` или `isaudio: 0`.  
> Просто **ПРОПУСНИ** ключа изцяло за false стойности.

### 10.2 actions.go.cmd — Как работи

```lua
-- При натискане на item:
_performJSONAction(jsonAction, from, qty, step, sink)
  -- Конструира: { cmd[1], from, qty, params... }
  -- Изпраща като RPC към сървъра
  -- Отговорът попълва нов browse списък
```

Пример item с действие:
```json
{
  "text": "Jazz Radio",
  "isaudio": 1,
  "actions": {
    "go": {
      "cmd": ["playlist", "play"],
      "params": {
        "url": "http://stream.jazz.fm/jazz128.mp3",
        "title": "Jazz Radio"
      },
      "nextWindow": "nowPlaying"
    }
  }
}
```

### 10.3 nextWindow стойности

| Стойност | Действие |
|---------|---------|
| `"nowPlaying"` | Скочи към Now Playing екрана |
| `"playlist"` | Скочи към Playlist view |
| `"home"` | Скочи към Home меню |
| `"parent"` | Затвори текущ, refresh parent |
| `"parentNoRefresh"` | Затвори текущ, без refresh |
| `"grandparent"` | Затвори 2 нива, refresh grandparent |
| `"refresh"` | Refresh текущия прозорец |
| `windowId` | Затвори до прозорец с това ID |

### 10.4 Pagination

```
BLOCK_SIZE = 200 (items per chunk)

Заявка:  { "cmd", offset, count, params... }
         offset = 0-based индекс
         count  = брой записи (200)

Отговор: { item_loop: [...], offset: 0, count: 150 }
         count = ОБЩ брой (не само в тази страница!)
         offset = 0-based начало на item_loop в тази страница
```

### 10.5 RPC команди изпращани от Browse

```lua
-- Home menu
{ "menu", 0, 100, "direct:1" }

-- Artists
{ "artists", 0, 200 }

-- Albums with filter
{ "albums", 0, 200, "artist_id:5", "useContextMenu:1" }

-- Songs
{ "songs", 0, 200, "album_id:10" }

-- Radio items (custom)
{ "radios", "items", 0, 200, "item_id:top" }
```

---

## 11. DB.lua — База данни за Browse Items

**Файл:** `net_dump/usr/share/jive/applets/SlimBrowser/DB.lua`  
**Роля:** Sparse storage за browse списъците.

### 11.1 Задължителен формат на chunk отговор

```json
{
  "count": 150,
  "offset": 0,
  "item_loop": [
    { "text": "...", "id": "..." },
    { "text": "...", "id": "..." }
  ]
}
```

**Правила:**
- `count` — **винаги** задължително (DB assert-ва)
- `offset` + `item_loop` — задължителни само ако `count > 0`
- `item_loop` (не `loop_loop`) — единственото валидно поле

### 11.2 Инвалидация на кеша

Данните се изчистват при:
```lua
if new_count != old_count then reset = true end
if new_timestamp != old_timestamp then reset = true end
```

---

## 12. SlimMenus.lua — Главно Меню

**Файл:** `jive_dump/usr/share/jive/applets/SlimMenus/SlimMenusApplet.lua`  
**Роля:** Получава структурата на менюто от сървъра и я показва.

### 12.1 Команда за зареждане на менюто

При свързване, устройството изпраща:
```json
{
  "channel": "/slim/request",
  "data": {
    "request": ["00:04:20:2c:90:b1", ["menu", 0, 100, "direct:1"]],
    "response": "/clientId/slim/request"
  }
}
```

### 12.2 menustatus subscription

```json
{
  "channel": "/slim/subscribe",
  "data": {
    "request": ["00:04:20:2c:90:b1", ["menustatus"]],
    "response": "/clientId/slim/menustatus/00:04:20:2c:90:b1"
  }
}
```

Push отговорите идват с:
```json
{
  "data": [
    null,
    [ { "item1": ... }, { "item2": ... } ],
    "add",
    "00:04:20:2c:90:b1"
  ]
}
```
- `data[2]` = array от items
- `data[3]` = `"add"` или `"remove"`
- `data[4]` = playerid

### 12.3 Формат на Home menu item

```json
{
  "id": "radio",
  "node": "home",
  "text": "Internet Radio",
  "weight": 30,
  "iconStyle": "hm_radioMySqueeze",
  "window": {
    "windowId": "radio",
    "menuStyle": "album",
    "windowStyle": "icon_list"
  },
  "actions": {
    "go": {
      "cmd": ["radios", "items"],
      "params": { "item_id": "top" },
      "nextWindow": "nowPlaying"
    }
  }
}
```

### 12.4 Филтрирани ID-та (не се показват)

```
"opmlmyapps"      → My Apps
"playerpower"     → Бутон захранване
"settingsPIN"     → PIN (не username/pass)
"settingsAudio"   → Handled locally
"radios"          → Handled locally (SC vs SN разлика)
"music_services"  → App store
"music_stores"    → App store
```

> ⚠️ Използвай `"id": "radio"` (без 's'), не `"id": "radios"`!

### 12.5 Node стойности

| Node | Позиция |
|------|--------|
| `"home"` | Главно меню |
| `"settings"` | Настройки |
| `"networkSettings"` | Мрежови настройки |
| `"myApps"` | Моите Apps |
| `"hidden"` | Скрити (не се показват) |

---

## 13. NowPlaying.lua — Екран „Сега Свири"

**Файл:** `jive_dump/usr/share/jive/applets/NowPlaying/NowPlayingApplet.lua`  
**Роля:** Показва metadata, artwork и контроли за текущия запис.

### 13.1 Данни от playerstatus за NowPlaying

```json
{
  "mode": "play",
  "time": 45.3,
  "duration": 0,
  "remote": 1,
  "current_title": "Radio Station Name",
  "item_loop": [
    {
      "track": "Song Title",
      "artist": "Artist Name",
      "album": "Album Name",
      "text": "formatted text",
      "icon-id": "artwork-123",
      "params": { "track_id": "radio-123" }
    }
  ],
  "remoteMeta": {
    "buttons": {
      "shuffle": { "command": [...], "jiveStyle": "shuffle_off" },
      "repeat":  { "command": [...], "jiveStyle": "repeat_0" },
      "rew": 0,
      "fwd": 1
    }
  }
}
```

### 13.2 Notifications за NowPlaying

| Notification | Trigger |
|-------------|---------|
| `playerTrackChange` | Нов запис |
| `playerPlaylistChange` | Промяна в playlist |
| `playerModeChange` | play/pause/stop |
| `playerShuffleModeChange` | Shuffle: 0=off, 1=song, 2=album |
| `playerRepeatModeChange` | Repeat: 0=off, 1=one, 2=all |
| `playerPower` | Вкл/Изкл |
| `playerTitleStatus` | Временно съобщение (rebuffering, etc.) |
| `playerDigitalVolumeControl` | Промяна на hw volume control |

---

## 14. ChooseMusicSource.lua — Избор на Сървър

**Файл:** `net_dump/usr/share/jive/applets/ChooseMusicSource/ChooseMusicSourceApplet.lua`

### 14.1 Server Connection flow

```
1. User избира сървър от списъка
2. Ако е с парола: squeezeCenterPassword() service
3. За SqueezeNetwork: RPC playerRegister(uuid, mac, name)
4. За local LMS: директно свързване
5. Timeout: 20s (CONNECT_TIMEOUT)
```

### 14.2 Server switching

При смяна на сървъра по време на playback:
- Показва confirmation диалог
- При потвърждение: `ignoreServerConnected=true` → смяна
- При отказ: `serverForRetry` → обратно към стария

---

## 15. SelectPlayer.lua — Избор на Плейър

**Файл:** `jive_dump/usr/share/jive/applets/SelectPlayer/SelectPlayerApplet.lua`

### 15.1 Player setup flow

```
selectPlayer(player):
  1. Ако player:getPin() → PIN entry (SqueezeNetwork)
  2. setCurrentPlayer(player) → глобален текущ плейър
  3. Ако needsNetworkConfig() → WiFi setup
  4. Ако needsMusicSource() → ChooseMusicSource
  5. Иначе → готово
```

### 15.2 Player models и иконки

```
softsqueeze, transporter, squeezebox2, squeezebox3,
squeezebox, slimp3, receiver, boom, controller,
squeezeplay, http, fab4, baby
```

---

## 16. JiveMain.lua — Начало на приложението

**Файл:** `jive_dump/usr/share/jive/jive/JiveMain.lua`

### 16.1 Startup последователност

```
1. NetworkThread()           → background мрежова нишка
2. AppletManager(jnt)        → зарежда applets
3. Iconbar(jnt)              → status bar
4. locale:readGlobalStringsFile()
5. Framework:initIRCodeMappings()
6. HomeMenu → добавя nodes: settings, extras, radios...
7. window:show()
8. appletManager:discover()  → зарежда skin и applets
9. Framework:eventLoop()     → блокира до shutdown
```

### 16.2 Home menu node hierarchy

```
HOME (root)
├── extras         (weight: 50)
│   └── games      (weight: 70)
├── radios         (weight: 20)  "INTERNET_RADIO"
├── _myMusic       (weight: 2, hidden)
└── settings       (weight: 1005)
    ├── screenSettings      (weight: 60)
    ├── settingsAudio       (weight: 40)
    ├── settingsBrightness  (weight: 45)
    └── advancedSettings    (weight: 105)
        ├── networkSettings (weight: 100)
        └── factoryTest     (weight: 120)
```

### 16.3 IR codes

```lua
0x7689c03f → KEY_REW       (Rewind)
0x7689a05f → KEY_FWD       (Forward)
0x7689807f → KEY_VOLUME_UP
0x768900ff → KEY_VOLUME_DOWN
```

---

## 17. Networking.lua — WiFi/Мрежов Мениджър

**Файл:** `jive_dump/usr/share/jive/jive/net/Networking.lua`

### 17.1 Wireless chipsets

```
ar6000 → Atheros
sd8686 / gspi8xxx → Marvell
```

### 17.2 Signal strength → quality mapping

```
level < 175  → quality 1  (слаб)
level < 180  → quality 2
level < 190  → quality 3
level >= 190 → quality 4  (силен)
```

### 17.3 WPA конфигурация

```
"wpa"    → key_mgmt=WPA-PSK, proto=WPA
"wpa2"   → key_mgmt=WPA-PSK, proto=WPA2
"wep40"  → wep_key0 (40-bit)
"wep104" → wep_key0 (104-bit)
none     → key_mgmt=NONE (отворена мрежа)

PSK ≤63 chars → ASCII passphrase
PSK  =64 chars → hex raw key
```

---

## 18. Пълен списък на всички файлове

### jive_dump — Системен дъмп

#### Applets

| Файл | Роля |
|------|-----|
| `AboutJive/` | "За устройството" информационен екран |
| `AlarmSnooze/` | Snooze функция за будилника |
| `BlankScreen/` | Blank screen screensaver |
| `ChooseMusicSource/` | Избор между LMS и SqueezeNetwork |
| `Clock/` | Часовник screensaver |
| `CrashLog/` | Изпраща crash логове към сървъра |
| `CustomizeHomeMenu/` | Потребителска наредба на home items |
| `DebugSkin/` | Debug skin за разработчици |
| `Demo/` | Demo режим |
| `Diagnostics/` | Диагностика на хардуера |
| `Experiments/` | Experimental features |
| `HttpAuth/` | HTTP Basic Auth диалог |
| `ImageViewer/` | Slideshow/image viewer (Flickr, USB, Server) |
| `InfoBrowser/` | HTML info browser (OPML) |
| `LineIn/` | Line-in audio routing applet |
| `LogSettings/` | Log ниво конфигурация |
| `MacroPlay/` | Macro playback (записани действия) |
| `NowPlaying/` | „Сега свири" екран |
| `Playback/` | Audio playback настройки |
| `QVGAbaseSkin/` | Base QVGA skin |
| `QVGAlandscapeSkin/` | Landscape QVGA skin (Baby/Radio) |
| `ScreenSavers/` | Screensaver управление |
| `Screenshot/` | Скрийншот функция |
| `SelectPlayer/` | Избор на плейър |
| `SelectSkin/` | Избор на skin |
| `SetupAppletInstaller/` | Инсталация на applets от сървъра |
| `SetupDateTime/` | Настройка на дата/час |
| `SetupFactoryReset/` | Factory reset |
| `SetupFirmwareUpgrade/` | Firmware update (MTD/UBI flash) |
| `SetupLanguage/` | Избор на език |
| `SetupNetTest/` | Тест на мрежата |
| `SetupNetworking/` | WiFi/Ethernet конфигурация |
| `SetupSoundEffects/` | Sound effects настройки |
| `SetupSqueezebox/` | Setup wizard за нов Squeezebox |
| `SetupSSH/` | SSH enable/disable |
| `SetupTZ/` | Timezone настройка |
| `SetupWallpaper/` | Wallpaper избор |
| `SetupWelcome/` | **First-boot setup wizard** |
| `Shortcuts/` | Shortcuts (preset buttons) |
| `SlimBrowser/` | **Browse engine за menus/radio/music** |
| `SlimDiscovery/` | **UDP server discovery** |
| `SlimMenus/` | **Home menu management** |
| `Spotify/` | Spotify Connect |
| `Squeezebox/` | Base Squeezebox applet |
| `SqueezeboxBaby/` | **Хардуерен applet (Baby/Radio)** |
| `SqueezeNetworkPIN/` | SqueezeNetwork PIN entry |
| `TestAmbient/` | Test на ambient light sensor |
| `TestAudioRouting/` | Test на audio routing |
| `TestDisplay/` | Test на дисплея |
| `TestKeypad/` | Test на бутоните |
| `TestTones/` | Test тонове |
| `UdapControl/` | UDAP protocol контрол |

#### Core Jive Framework

| Файл | Роля |
|------|-----|
| `jive/Applet.lua` | Базов клас за всички applets |
| `jive/AppletManager.lua` | Зарежда/управлява applets |
| `jive/AppletMeta.lua` | Базов клас за Meta файлове |
| `jive/Iconbar.lua` | Status bar (WiFi, battery, clock) |
| `jive/InputToActionMap.lua` | Mapping бутони → actions |
| `jive/irMap_default.lua` | IR remote code mappings |
| `jive/JiveMain.lua` | **Main entry point** |
| `jive/System.lua` | Системни функции (reboot, etc.) |

#### Мрежов слой (jive/net/)

| Файл | Роля |
|------|-----|
| `Comet.lua` | **Bayeux/CometD protocol** |
| `CometRequest.lua` | CometD заявки |
| `DNS.lua` | DNS резолюция |
| `HttpPool.lua` | HTTP connection pool |
| `Networking.lua` | **WiFi/Ethernet management** |
| `NetworkThread.lua` | Background мрежова нишка |
| `Process.lua` | Subprocess изпълнение |
| `RequestHttp.lua` | HTTP заявки |
| `RequestJsonRpc.lua` | JSON-RPC заявки |
| `SlimProto.lua` | **TCP binary protocol** |
| `Socket.lua` | Базов socket клас |
| `SocketHttp.lua` | HTTP socket |
| `SocketHttpQueue.lua` | HTTP socket опашка |
| `SocketTcp.lua` | TCP socket |
| `SocketUdp.lua` | UDP socket |
| `Udap.lua` | UDAP discovery protocol |
| `WakeOnLan.lua` | Wake-on-LAN |

#### Slim слой (jive/slim/)

| Файл | Роля |
|------|-----|
| `ArtworkCache.lua` | Artwork LRU cache |
| `LocalPlayer.lua` | Local softsqueeze player |
| `Player.lua` | **Player state/RPC управление** |
| `SlimServer.lua` | **LMS server connection** |

#### Аудио слой (jive/audio/)

| Файл | Роля |
|------|-----|
| `Playback.lua` | **Audio streaming engine** |
| `Rtmp.lua` | RTMP streaming |
| `SpectrumMeter.lua` | Spectrum analyzer |
| `VUMeter.lua` | VU meter |

#### UI компоненти (jive/ui/)

| Файл | Роля |
|------|-----|
| `Audio.lua` | Audio widget |
| `Button.lua` | Button widget |
| `Canvas.lua` | Drawing canvas |
| `Checkbox.lua` | Checkbox widget |
| `Choice.lua` | Multi-choice widget |
| `ContextMenuWindow.lua` | Context menu |
| `Event.lua` | Event система |
| `Flick.lua` | Flick/swipe gesture |
| `Font.lua` | Font management |
| `Framework.lua` | UI framework core |
| `Group.lua` | Widget group |
| `HomeMenu.lua` | Home menu widget |
| `Icon.lua` | Icon widget |
| `IRMenuAccel.lua` | IR menu acceleration |
| `Keyboard.lua` | On-screen keyboard |
| `Label.lua` | Text label |
| `Menu.lua` | Menu widget |
| `NumberLetterAccel.lua` | Number/letter acceleration |
| `Popup.lua` | Popup window |
| `RadioButton.lua` | Radio button |
| `RadioGroup.lua` | Radio button group |
| `ScrollAccel.lua` | Scroll acceleration |
| `Scrollbar.lua` | Scrollbar widget |
| `ScrollWheel.lua` | Scroll wheel handler |
| `SimpleMenu.lua` | Simple list menu |
| `Slider.lua` | Slider widget (volume) |
| `SnapshotWindow.lua` | Window snapshot |
| `StickyMenu.lua` | Sticky menu |
| `Surface.lua` | Drawing surface |
| `Task.lua` | Cooperative task |
| `Textarea.lua` | Multi-line text |
| `Textinput.lua` | Text input field |
| `Tile.lua` | Tile widget |
| `Timeinput.lua` | Time input |
| `Timer.lua` | Timer widget |
| `Widget.lua` | Base widget class |
| `Window.lua` | Window widget |

#### Utilities (jive/utils/)

| Файл | Роля |
|------|-----|
| `autotable.lua` | Auto-creating table |
| `coxpcall.lua` | Protected call |
| `datetime.lua` | Date/time helpers |
| `debug.lua` | Debug helpers |
| `dumper.lua` | Table dumper |
| `jsonfilters.lua` | JSON filtering |
| `locale.lua` | Internationalization |
| `log.lua` | Logging framework |
| `squeezeos.lua` | SqueezeOS system calls |
| `string.lua` | String extensions |
| `table.lua` | Table utilities |

---

## 19. Критични правила за сървъра

Тези правила са извлечени директно от firmware кода и са **задължителни** за правилна работа:

### 19.1 SlimProto TCP

```
✅ Изпрати vers веднага след HELO
✅ НЕ изпращай serv (кара устройството да disconnect/reconnect)
✅ Keepalive: strm 't' на всеки 25s (устройството READ_TIMEOUT=35s)
✅ SO_KEEPALIVE на socket (OS-level detection на мъртви връзки)
✅ НЕ timeout-вай readexactly() — устройството може да мълчи дълго!
✅ При reconnect HELO: ако има _now_playing, изпрати strm-s отново
```

### 19.2 serverstatus

```
✅ Полето ТРЯБВА да е "player count" (с интервал)
✅ Трябва да има players_loop масив
✅ players_loop items трябва да имат playerid = MAC
```

### 19.3 strm-s пакет

```
✅ format байт: 'm'=MP3, 'o'=OGG/Opus, 'a'=AAC, 'f'=FLAC
✅ Изпрати strm 'q' (stop) преди strm 's' (start)
✅ serverIp: BIG-ENDIAN uint32 (не string!)
✅ serverPort: BIG-ENDIAN uint16
✅ httpHeaders: HTTP/1.0 GET заявка (string, \r\n terminated)
```

### 19.4 Browse items (JSON)

```
✅ Използвай item_loop (НЕ loop_loop)
✅ Винаги включвай offset и count
✅ НЕ поставяй hasitems: 0 или isaudio: 0 — 0 е truthy в Lua!
✅ ПРОПУСНИ ключа изцяло за false стойности
✅ Всеки hasitems:1 item ТРЯБВА да има actions.go.cmd
✅ Всеки isaudio:1 item ТРЯБВА да има actions.go с nextWindow
```

### 19.5 Home menu

```
✅ items трябва да имат node: "home"
✅ НЕ използвай id: "radios" — използвай id: "radio"
✅ menustatus push: data[2]=items, data[3]="add", data[4]=playerid
```

### 19.6 CometD

```
✅ Handshake: върни clientId
✅ Connect: advice.timeout=0 (polling mode)
✅ subscribe: веднага bundle data response в същия reply
✅ Response channel: /clientId/slim/... (с clientId prefix)
```

### 19.7 playerstatus

```
✅ Subscription: /slim/playerstatus/{mac}
✅ mode: "play" | "pause" | "stop"
✅ При radio: duration=0, remote=1, current_title=station_name
✅ item_loop[1] трябва да съдържа track info
```

---

*Документация създадена от анализ на Squeezebox Radio firmware dump (7.7.3-r16676)*  
*Последна актуализация: Март 2026*
