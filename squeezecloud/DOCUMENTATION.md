# SqueezeCloud — Техническа Документация

**Проект:** Локален сървър заместващ mysqueezebox.com за Squeezebox Radio устройства  
**Статус:** В разработка  
**Дата:** Март 2026  
**Устройство:** UE Squeezebox Radio, Hardware v7, Firmware 7.7.3 r16676

---

## Съдържание

1. [Предпоставки и проблем](#1-предпоставки-и-проблем)
2. [Архитектура на решението](#2-архитектура-на-решението)
3. [Мрежова диагностика — как го открихме](#3-мрежова-диагностика--как-го-открихме)
4. [DNS пренасочване](#4-dns-пренасочване)
5. [SlimProto — TCP протокол порт 3483](#5-slimproto--tcp-протокол-порт-3483)
6. [UDP Autodiscovery — порт 3483](#6-udp-autodiscovery--порт-3483)
7. [Bayeux/Comet протокол — HTTP /cometd](#7-bayeuxcomet-протокол--http-cometd)
8. [JSON-RPC API — /jsonrpc.js](#8-json-rpc-api--jsonrpcjs)
9. [Главно меню — команда `menu`](#9-главно-меню--команда-menu)
10. [Интернет радио](#10-интернет-радио)
11. [Подкасти](#11-подкасти)
12. [Времето](#12-времето)
13. [Новини](#13-новини)
14. [Файлова система на устройството](#14-файлова-система-на-устройството)
15. [SSH достъп](#15-ssh-достъп)
16. [Инсталация и стартиране](#16-инсталация-и-стартиране)
17. [Известни проблеми и TODO](#17-известни-проблеми-и-todo)
18. [Анализ на файловете в репозиторито (File-by-File)](#18-анализ-на-файловете-в-репозиторито-file-by-file)
19. [Signup Screen — Пълен анализ и решения](#19-signup-screen--пълен-анализ-и-решения)
20. [Бързо валидиране (Quick Validation Checklist)](#20-бързо-валидиране-quick-validation-checklist)
21. [Файлова система на устройството — подробен анализ](#21-файлова-система-на-устройството--подробен-анализ)
22. [Работен процес при промени](#22-работен-процес-при-промени)

---

## 1. Предпоставки и проблем

### Какво е Squeezebox Radio

Squeezebox Radio е мрежов интернет радио плейър произведен от Logitech (под марката UE — Ultimate Ears). Работи на базата на **SqueezeOS** — вграден Linux с Lua-базиран UI framework наречен **Jive**.

Устройството **задължително** се нуждае от сървър за да функционира. Оригинално се свързва към:
- `mysqueezebox.com` — облачен сървър на Logitech (СПРЯН)
- `baby.squeezenetwork.com` — SlimProto endpoint
- `config.logitechmusic.com` — firmware конфигурация
- `update.squeezenetwork.com` — firmware updates
- `fab4.squeezenetwork.com` — допълнителни услуги

**Logitech спря всички тези услуги.** Устройството без сървър показва само "Not connected" и е напълно нефункционално за интернет радио.

### Алтернативи

| Вариант | Описание | Сложност |
|---------|----------|----------|
| Lyrion Music Server (LMS) | Официален open-source наследник на оригиналния сървър | Средна — изисква постоянно работеща машина |
| SqueezeCloud (този проект) | Минимален Python сървър имитиращ API-то | Ниска — един файл, pip install |
| Community firmware 8.5.0 | Премахва зависимостта от cloud изцяло | Висока — изисква LMS за инсталация |

---

## 2. Архитектура на решението

```
┌─────────────────────────────────────────────────────────┐
│                  Squeezebox Radio                        │
│  SqueezeOS / Jive (Lua)                                  │
│                                                          │
│  SlimProto.lua ──TCP 3483──────────────────────────┐    │
│  Comet.lua ─────HTTP /cometd───────────────────────┤    │
│  SlimMenus.lua ─HTTP /jsonrpc.js───────────────────┤    │
│  UDP broadcast ─UDP 3483───────────────────────────┘    │
└──────────────────────┬──────────────────────────────────┘
                       │ (DNS: mysqueezebox.com → 192.168.1.43)
                       │ (DNS: baby.squeezenetwork.com → 192.168.1.43)
                       ▼
┌─────────────────────────────────────────────────────────┐
│              SqueezeCloud (Python/FastAPI)                │
│              192.168.1.43                                │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ UDP :3483   │  │ TCP :3483    │  │ HTTP :9000    │  │
│  │ Discovery   │  │ SlimProto    │  │ FastAPI       │  │
│  │ Broadcast   │  │ HELO/STAT   │  │ /cometd       │  │
│  │ response    │  │ vers/serv   │  │ /jsonrpc.js   │  │
│  └─────────────┘  └──────────────┘  │ /api/v1/*     │  │
│                                      └───────────────┘  │
│                                             │            │
│                              ┌──────────────▼──────┐    │
│                              │   External Sources   │    │
│                              │  Radio Browser API   │    │
│                              │  Open-Meteo Weather  │    │
│                              │  RSS feeds (news)    │    │
│                              │  Podcast RSS         │    │
│                              └─────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Мрежова диагностика — как го открихме

### Инструменти използвани

- **Android port scanner** — открихме отворени портове на устройството
- **Mikrotik Packet Sniffer** — анализ на мрежовия трафик
- **SSH към Squeezebox** — директен достъп до устройството
- **`/var/log/messages`** — Lua application логове

### Открити отворени портове на Squeezebox (192.168.1.72)

| Порт | Протокол | Описание |
|------|----------|----------|
| 7    | TCP      | Echo Service |
| 13   | TCP      | Daytime |
| 22   | TCP      | SSH (legacy криптография) |
| 23   | TCP      | Telnet |
| 37   | TCP      | Time server |

### Mikrotik sniffer анализ

Чрез Mikrotik packet sniffer открихме:

```
192.168.1.72:47195 → 255.255.255.255:3483  UDP  (LMS autodiscovery broadcast)
```

Squeezebox изпраща **UDP broadcast** на целия LAN търсейки LMS сървър. Payload:
```
b'eIPAD\x00NAME\x00JSON\x00VERS\x00UUID\x00JVID'
```

Това е стандартният LMS discovery протокол — `e` = discovery request.

### Лог анализ от `/var/log/messages`

```
Comet {mysqueezebox.com}: _handshake error: baby.squeezenetwork.com Try again
```

Открихме че устройството използва **Bayeux/Comet** протокол за комуникация с cloud сървъра и се опитва да се свърже с `baby.squeezenetwork.com` за SlimProto connection.

---

## 4. DNS пренасочване

### Метод: `/etc/hosts` на устройството

Тъй като `/` е монтирана като `unionfs` с `cramfs` (ro) отдолу и `/mnt/storage` (rw) overlay, промените в `/etc/hosts` са **постоянни** след рестарт:

```
cramfs (ro)     ← оригинален firmware, read-only
unionfs (rw)    ← overlay, промените се запазват тук
/mnt/storage    ← ubifs flash, 7.8MB, rw
```

Файлът `/mnt/storage/etc/hosts` замества оригиналния при четене.

### Правилно съдържание на hosts файла

```bash
cat > /mnt/storage/etc/hosts << 'EOF'
127.0.0.1 localhost
192.168.1.43 mysqueezebox.com
192.168.1.43 www.mysqueezebox.com
192.168.1.43 update.squeezenetwork.com
192.168.1.43 config.logitechmusic.com
192.168.1.43 baby.squeezenetwork.com
192.168.1.43 fab4.squeezenetwork.com
EOF
```

### Важно: Целият трафик е HTTP, не HTTPS

Squeezebox Radio firmware използва **само HTTP** (не HTTPS) за комуникация с cloud сървъра. Няма нужда от SSL сертификати, MITM proxy или сложна TLS конфигурация.

### DNS чрез рутер (алтернатива)

Ако имате достъп до рутера, може да добавите DNS overrides в Mikrotik:
```
/ip dns static add name=mysqueezebox.com address=192.168.1.43
/ip dns static add name=baby.squeezenetwork.com address=192.168.1.43
```

Недостатък: не работи когато устройството е в друга мрежа.

---

## 5. SlimProto — TCP протокол порт 3483

### Описание

SlimProto е binary TCP протокол за real-time комуникация между Squeezebox и сървъра. Използва се за:
- Регистрация на устройството
- Управление на playback (play/pause/volume)
- Keepalive/heartbeat
- Пренасочване към HTTP сървър

### Пакетен формат (от сървъра към устройството)

```
┌─────────────────────────────────────┐
│  2 байта: length (big-endian)       │
│  4 байта: opcode (ASCII)            │
│  N байта: payload                   │
└─────────────────────────────────────┘
```

### Пакетен формат (от устройството към сървъра)

```
┌─────────────────────────────────────┐
│  4 байта: opcode (ASCII)            │
│  4 байта: length (big-endian)       │
│  N байта: payload                   │
└─────────────────────────────────────┘
```

**Важно:** Форматите са различни в двете посоки!

### HELO пакет (от устройство → сървър)

Изпраща се при всяко свързване:

```
bytes 0-3:  opcode = "HELO"
bytes 4-7:  length
bytes 8:    deviceID (9 = Squeezebox Radio)
bytes 9:    revision
bytes 10-15: MAC адрес (6 байта)
bytes 16-31: UUID (16 байта)
...
```

### Отговор на HELO (от сървър → устройство)

```python
# vers — версия на сървъра
_slim_send(writer, b"vers", b"8.5.0")

# serv — пренасочване към HTTP сървър
# payload: 4 байта IP (uint32 big-endian) + 11 байта syncgroupid
ip_int = (192 << 24) | (168 << 16) | (1 << 8) | 43  # 192.168.1.43
serv_payload = ip_int.to_bytes(4, "big") + b"squeezecloud".ljust(11, b"\x00")
_slim_send(writer, b"serv", serv_payload)
```

### serv пакет — критично откритие

От изходния код на SlimProto.lua:
```lua
serv = function(self, packet)
    return {
        serverip    = unpackNumber(packet, 5, 4),    -- IP като uint32
        syncgroupid = string.sub(packet, 9, 19),     -- 11 байта string
    }
end
```

Без `syncgroupid` устройството не приема сървъра за валиден!

### Keepalive

Squeezebox очаква periodic съобщения от сървъра. Без тях генерира `inactivity timeout` след ~19 секунди. Изпращаме `strm t` (timer) на всеки 10 секунди:

```python
_slim_send(writer, b"strm", b"t" + b"\x00" * 24)
```

### STAT пакети (от устройство → сървър)

Устройството изпраща статус updates:

| Event | Описание |
|-------|----------|
| `STMt` | Timer event |
| `STMo` | Output threshold |
| `STMd` | Decoder ready |
| `STMf` | Connection failed |
| `vers` | Version acknowledged |

---

## 6. UDP Autodiscovery — порт 3483

### Описание

При стартиране Squeezebox изпраща UDP broadcast на `255.255.255.255:3483` търсейки LMS сървър в локалната мрежа. Това е **основният начин** за автоматично свързване — без нужда от ръчна конфигурация или hosts пач.

### Discovery request payload

```
b'eIPAD\x00NAME\x00JSON\x00VERS\x00UUID\x00JVID\x00'
```

Байт `e` (0x65) = discovery request. Следващите полета са capabilities на устройството.

### Discovery response формат

```python
name = b"SqueezeCloud"
ip   = b"192.168.1.43"
port = b"9000"
response = b"E" + name + b"\x00" + ip + b"\x00" + port + b"\x00"
```

`E` (0x45) = discovery response.

### Повторяемост

Squeezebox изпраща broadcast на всеки ~50 секунди докато не намери сървър. След успешно свързване спира broadcasts.

### Home Assistant (192.168.1.67) също отговаря

Открихме че HA (с LMS addon или Squeezebox интеграция) също слуша на UDP 3483 и отговаря на broadcasts. Нашият сървър трябва да отговори пръв.

---

## 7. Bayeux/Comet протокол — HTTP /cometd

### Описание

Squeezebox използва **Bayeux protocol** (имплементиран чрез CometD) за persistent HTTP комуникация с cloud сървъра. Всички менюта, плейлисти и команди минават през този канал.

Важно: Logitech имплементацията има отклонения от стандартния Bayeux.

### Connection flow

```
1. POST /cometd  {"channel":"/meta/handshake"}
   ← {"clientId":"e539b0a1","successful":true,"advice":{"timeout":60000}}

2. POST /cometd  [{"channel":"/meta/connect"},
                  {"channel":"/meta/subscribe","subscription":"/e539b0a1/**"}]
   ← [{"channel":"/meta/connect","successful":true},
      {"channel":"/meta/subscribe","successful":true}]

3. POST /cometd  [{"channel":"/slim/subscribe",
                   "data":{"request":["","serverstatus",0,50,"subscribe:60"],
                           "response":"/e539b0a1/slim/serverstatus"}},
                  {"channel":"/slim/subscribe",
                   "data":{"request":["","firmwareupgrade",...],
                           "response":"/e539b0a1/slim/firmwarestatus"}},
                  {"channel":"/slim/request",
                   "data":{"request":["","register",0,100,"service:SN"],
                           "response":"/e539b0a1/slim/request"}}]
   ← [serverstatus response, firmwarestatus response, register response]

4. POST /cometd  {"channel":"/slim/request",
                  "data":{"request":["MAC","menu",0,100,"direct:1"],
                          "response":"/e539b0a1/slim/request"}}
   ← {"channel":"/e539b0a1/slim/request","data":{"count":6,"item_loop":[...]}}

5. POST /cometd  {"channel":"/slim/request",
                  "data":{"request":["MAC","playerRegister",null,"MAC","Name"]}}
   ← {"ok":1,"id":"MAC","name":"Name"}
```

### Channels

| Channel | Посока | Описание |
|---------|--------|----------|
| `/meta/handshake` | C→S | Инициализация, получава clientId |
| `/meta/connect` | C→S | Persistent connection keepalive |
| `/meta/subscribe` | C→S | Subscribe към канал |
| `/slim/subscribe` | C→S | Subscribe с LMS команда |
| `/slim/request` | C→S | Еднократна LMS команда |
| `/e539b0a1/**` | S→C | Push съобщения към клиента |

---

## 8. JSON-RPC API — /jsonrpc.js

### Описание

Класическият LMS JSON-RPC endpoint. Squeezebox го използва за директни заявки (не през Comet).

### Request формат

```json
{
  "id": 1,
  "method": "slim.request",
  "params": ["PLAYER_MAC", ["COMMAND", "ARG1", "ARG2", ...]]
}
```

### Response формат

```json
{
  "id": 1,
  "method": "slim.request",
  "result": { ... }
}
```

### Имплементирани команди

| Команда | Описание | Статус |
|---------|----------|--------|
| `serverstatus` | Статус на сървъра — включва `isSqueezenetwork:1` | ✅ |
| `players` | Списък на свързани устройства | ✅ |
| `status` | Статус на конкретен плейър | ✅ |
| `menu` | Главно навигационно меню | ✅ |
| `radios` | Browse интернет радио станции | ✅ |
| `podcasts` | Browse подкасти | ✅ |
| `favorites` | Любими станции (от STATIC_STATIONS) | ✅ |
| `weather` | Времето | ✅ |
| `news` | Новини от RSS | ✅ |
| `apps` | Списък приложения | ✅ |
| `register` | Регистрация — `{pin:false, connected:1}` | ✅ |
| `playerRegister` | Регистрация на плейър | ✅ |
| `firmwareupgrade` | Firmware update check — `upgradeNeeded:0` | ✅ |
| `browseLibrary` | Alias за favorites | ✅ |
| `play` | Пускане | ✅ (stub) |
| `pause` | Пауза | ✅ (stub) |
| `mixer` | Volume control | ✅ (stub) |

---

## 9. Главно меню — команда `menu`

### Request

```json
["MAC", ["menu", 0, 100, "direct:1"]]
```

### Response формат (критично!)

```json
{
  "count": 6,
  "item_loop": [
    {
      "id": "radios",
      "node": "home",
      "name": "Internet Radio",
      "weight": 20,
      "type": "link",
      "icon": "html/images/radio.png",
      "actions": {
        "go": {
          "cmd": ["radios", 0, 100],
          "player": 0
        }
      }
    }
  ]
}
```

### Полета

| Поле | Описание |
|------|----------|
| `id` | Уникален идентификатор |
| `node` | Родителски node ("home" = главно меню) |
| `name` | Показвано име |
| `weight` | Сортиране (по-малко = по-напред) |
| `type` | "link", "audio", "text", "playlist" |
| `icon` | Относителен път към икона |
| `actions.go.cmd` | LMS команда при натискане |

---

## 10. Интернет радио

### Sources

**Radio Browser API** (community-maintained, безплатен):
- `https://de1.api.radio-browser.info/json/stations/search`
- `https://nl1.api.radio-browser.info` (failover)
- `https://at1.api.radio-browser.info` (failover)

Параметри: `limit=300, hidebroken=true, order=votes, reverse=true, is_https=true`

**Статични BG станции** (вградени в кода):
- БНР Хоризонт, Христо Ботев, Радио България
- Radio 1 Rock, NRJ Bulgaria, Z-Rock Bulgaria

### Кеширане

Резултатите от Radio Browser API се кешират в памет за **1 час** за да не се правят излишни заявки при всяко зареждане на менюто.

### radios команда response

```json
{
  "count": 313,
  "loop_loop": [
    {
      "id": "radio:0",
      "name": "БНР Хоризонт",
      "type": "audio",
      "url": "https://stream.bnr.bg/horizont_24",
      "isaudio": 1,
      "hasitems": 0
    }
  ]
}
```

---

## 11. Подкасти

### RSS Feed парсер

Имплементиран вграден XML RSS парсер с fallback regex за счупен XML.

Търси `<enclosure>` тагове за MP3 файлове:
```xml
<enclosure url="https://example.com/episode.mp3" type="audio/mpeg"/>
```

### Налични feeds

| Канал | Език | URL |
|-------|------|-----|
| БНР Подкасти | BG | bnr.bg/radiobulgaria/podcast |
| Deutsche Welle BG | BG | rss.dw.com |
| BBC Global News | EN | podcasts.files.bbci.co.uk |
| TED Talks Daily | EN | feeds.feedburner.com/TEDTalks_audio |
| Radiolab | EN | feeds.feedburner.com/radiolab |
| 99% Invisible | EN | feeds.simplecast.com |
| Freakonomics Radio | EN | feeds.simplecast.com |

### Кеширане

Podcast feeds се кешират за **15 минути**.

---

## 12. Времето

### API: Open-Meteo

**Безплатен, без API key**, GDPR compliant.

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=42.6977
    &longitude=23.3219
    &current=temperature_2m,weathercode,windspeed_10m,relativehumidity_2m
    &timezone=auto
```

### WMO Weather codes

| Code | Описание |
|------|----------|
| 0 | Ясно ☀️ |
| 1-2 | Предимно ясно 🌤️ |
| 3 | Облачно ☁️ |
| 45-48 | Мъгла 🌫️ |
| 51-65 | Дъжд 🌧️ |
| 71-77 | Сняг ❄️ |
| 80-82 | Валежи 🌦️ |
| 95-99 | Гръмотевична буря ⛈️ |

### Кеширане

Времето се кешира за **10 минути**.

---

## 13. Новини

### RSS парсер

Поддържа CDATA секции и стандартни XML тагове:
```xml
<title><![CDATA[Заглавие на новина]]></title>
```

### Налични feeds

| Канал | Език |
|-------|------|
| БНР Новини | BG |
| Dnevnik.bg | BG |
| Reuters | EN |
| BBC News | EN |
| Al Jazeera | EN |

### Кеширане

Новините се кешират за **5 минути**.

---

## 14. Файлова система на устройството

### Монтирани файлови системи

```
Filesystem     Size    Used  Avail  Mounted on
/dev/root      7.8M   76.0K   7.3M  /           (cramfs, ro)
ubi0:ubifs     7.8M   76.0K   7.3M  /mnt/storage (ubifs, rw)
none           7.8M   76.0K   7.3M  /           (unionfs, rw)
none          30.3M   52.0K  30.3M  /dev
```

### Unionfs overlay механизъм

```
Четене:  unionfs проверява /mnt/storage първо → ако липсва, чете от cramfs
Писане:  unionfs записва в /mnt/storage
```

Т.е. всяко писане в `/etc/` реално отива в `/mnt/storage/etc/`.

### Важни директории

| Път | Описание |
|-----|----------|
| `/etc/hosts` | Hostname resolving (unionfs overlay) |
| `/mnt/storage/etc/hosts` | Реалният файл с overrides |
| `/usr/share/jive/` | Lua source код на firmware |
| `/usr/share/jive/jive/net/SlimProto.lua` | SlimProto протокол |
| `/usr/share/jive/jive/net/Comet.lua` | Bayeux/Comet протокол |
| `/usr/share/jive/applets/` | Приложения (Radio, Podcasts, etc.) |
| `/var/log/messages` | Application логове |

---

## 15. SSH достъп

### Проблем: Остаряла криптография

SSH на устройството поддържа само legacy алгоритми несъвместими с модерни SSH клиенти.

### Решение: Legacy флагове

```bash
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oCiphers=+aes128-cbc \
    -oMACs=+hmac-sha1 \
    root@192.168.1.72
```

Парола: (празна) или `1234`

### ~/.ssh/config (за по-лесен достъп)

```
Host squeezebox
    HostName 192.168.1.72
    User root
    KexAlgorithms +diffie-hellman-group1-sha1
    HostKeyAlgorithms +ssh-rsa
    Ciphers +aes128-cbc
    MACs +hmac-sha1
```

После просто: `ssh squeezebox`

---

## 16. Инсталация и стартиране

### Изисквания

- Python 3.10+
- pip

### Инсталация

```bash
pip install -r requirements.txt
```

**requirements.txt:**
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0
```

### Стартиране

```bash
python main.py
```

При стартиране се показва:
```
════════════════════════════════════════════════════════════
  SqueezeCloud сървър
════════════════════════════════════════════════════════════
  Локално IP:       192.168.1.43
  HTTP порт:        9000  (LMS API)
  TCP порт:         3483  (Slim Protocol)
  UDP порт:         3483  (Autodiscovery broadcast)

  Squeezebox ще се открие АВТОМАТИЧНО чрез broadcast!
```

### Hosts пач на Squeezebox (еднократно)

```bash
# SSH към устройството:
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oCiphers=+aes128-cbc \
    -oMACs=+hmac-sha1 \
    root@192.168.1.72

# На устройството:
cat > /mnt/storage/etc/hosts << 'EOF'
127.0.0.1 localhost
192.168.1.43 mysqueezebox.com
192.168.1.43 www.mysqueezebox.com
192.168.1.43 update.squeezenetwork.com
192.168.1.43 config.logitechmusic.com
192.168.1.43 baby.squeezenetwork.com
192.168.1.43 fab4.squeezenetwork.com
EOF

reboot
```

### Autostart (Linux systemd)

```bash
sudo nano /etc/systemd/system/squeezecloud.service
```

```ini
[Unit]
Description=SqueezeCloud Server
After=network.target

[Service]
WorkingDirectory=/opt/squeezecloud
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5
User=squeezecloud

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable squeezecloud
sudo systemctl start squeezecloud
```

### Добавяне на радио станции

В `main.py`, в `STATIC_STATIONS`:
```python
{"name": "My Station", "url": "https://stream.example.com/128.mp3", "genre": "Rock", "country": "BG"},
```

### Добавяне на подкасти/новини

В `PODCAST_FEEDS` или `NEWS_FEEDS`:
```python
{"name": "My Podcast", "url": "https://example.com/feed.rss", "lang": "bg"},
```

---

## 17. Известни проблеми и TODO

### Текущи проблеми

| Проблем | Статус | Бележка |
|---------|--------|---------|
| SlimProto `inactivity timeout` | 🔄 В процес | Keepalive добавен, нужно тестване |
| `bogus timer` в SlimProto.lua | 🔄 В процес | Race condition при reconnect |
| Меню без икони | 🔄 В процес | Jive ползва собствени icon styles |
| Unreachable `favorites` код | ✅ Фиксиран | Правилен `favorites` handler добавен |
| `register` response format | ✅ Фиксиран | `{pin:false, connected:1}` |
| `serverstatus` без SN полета | ✅ Фиксиран | `isSqueezenetwork:1` добавен |
| `news`/`weather` items без `text` | ✅ Фиксиран | Всички items ползват `text` |
| Липсващ `/api/v1/session` | ✅ Фиксиран | Добавен endpoint |
| Липсващ `/api/v1/deviceRegistration` | ✅ Фиксиран | Добавен endpoint |
| `playerRegister` response | ✅ Фиксиран | |
| UDP discovery | ✅ Работи | |
| Comet handshake | ✅ Работи | |

### TODO

- [ ] Тестване на пълен menu browse flow с реално устройство
- [ ] Имплементация на playback control (реален stream redirect)
- [ ] Favorites persistence (запазване между сесии в JSON файл)
- [ ] Volume control през SlimProto
- [ ] Artwork/album art proxy
- [ ] Web admin UI за управление на станции
- [ ] Добавяне на повече BG радио станции
- [ ] Интеграция с Home Assistant като media player entity
- [ ] Разглеждане на community firmware 8.5.0 за пълна независимост
- [x] Поправка на signup screen (сървърна страна)
- [x] Lua патч за bypass на SN registration (`deploy_lua_patch.sh`)

### Ресурси

- [LMS Community (Lyrion)](https://lyrion.org) — официален open-source наследник
- [Radio Browser API](https://www.radio-browser.info) — community radio database
- [Open-Meteo](https://open-meteo.com) — безплатен weather API
- [clach04/squeezebox_firmware_server](https://github.com/clach04/squeezebox_firmware_server) — reference implementation
- [SlimProto specification](https://wiki.slimdevices.com/index.php/SlimProto_TCP_protocol) — официална документация


---

## 18. Анализ на файловете в репозиторито (File-by-File)

Този раздел документира всеки файл в проекта — предназначение, структура, ключови части и как се валидира.

---

### 18.1 `squeezecloud/main.py` — Главният Python сървър

**Размер:** ~1200 реда  
**Роля:** Единственият сървърен файл. Имитира mysqueezebox.com локално.

#### Основна структура (секции)

| Редове | Секция | Описание |
|--------|--------|----------|
| 1–20 | Imports | FastAPI, asyncio, httpx, XML parser |
| 21–32 | Cache | In-memory cache с TTL |
| 34–35 | Global MAC | `_device_mac` — реалният MAC от HELO/Comet |
| 37–44 | CONFIG | Сървърно наименование, версия, GPS координати |
| 46–61 | STATIC_STATIONS | 13 вградени радио станции |
| 63–72 | PODCAST_FEEDS | 7 подкаст RSS feed-а |
| 74–81 | NEWS_FEEDS | 5 новинарски RSS feed-а |
| 83–91 | FastAPI app | CORS middleware |
| 93–168 | Auth/Login/Session | `/api/v1/login`, `/api/v1/session`, `/api/v1/deviceRegistration`, `/api/v1/firmware` |
| 174–291 | Comet/Bayeux | POST/GET `/cometd` — handshake, connect, subscribe, reconnect |
| 292–466 | `_handle_comet_message` | Обработва всички Bayeux канали |
| 467–556 | JSON-RPC `/jsonrpc.js` | POST и GET endpoints |
| 557–731 | `dispatch_rpc` | Обработва всички LMS команди |
| 732–805 | Radio browse | GET `/api/v1/radios`, Radio Browser API |
| 806–884 | Weather | GET `/api/v1/weather`, Open-Meteo |
| 885–990 | News/Podcasts | GET `/api/v1/news`, `/api/v1/podcasts`, RSS parser |
| 991–1083 | SlimProto TCP | `slim_handle_client`, `_slim_send` |
| 1084–1136 | UDP Discovery | `slim_udp_discovery` |
| 1137–1195 | Startup | `main()` — asyncio gather |

#### Валидиране (quick checks)

```bash
# Синтаксис
python3 -c "import ast; ast.parse(open('main.py').read()); print('OK')"

# Стартиране в тест режим
python main.py

# В друг терминал — тест endpoints
curl -s http://localhost:9000/ | python3 -m json.tool
curl -s "http://localhost:9000/api/v1/login?mac=00:04:20:aa:bb:cc" | python3 -m json.tool
curl -s "http://localhost:9000/api/v1/session" | python3 -m json.tool
curl -s "http://localhost:9000/api/v1/time" | python3 -m json.tool

# Тест JSON-RPC
curl -s -X POST http://localhost:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":1,"method":"slim.request","params":["",["serverstatus",0,50]]}' | python3 -m json.tool

curl -s -X POST http://localhost:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":2,"method":"slim.request","params":["00:04:20:aa:bb:cc",["menu",0,100,"direct:1"]]}' | python3 -m json.tool

# Тест Comet handshake
curl -s -X POST http://localhost:9000/cometd \
  -H "Content-Type: application/json" \
  -d '[{"channel":"/meta/handshake","version":"1.0","supportedConnectionTypes":["long-polling"]}]' | python3 -m json.tool
```

#### Известни проблеми (поправени)

| Проблем | Статус | Поправка |
|---------|--------|---------|
| Unreachable код след `firmwareupgrade` return | ✅ Поправен | Добавен `favorites` handler |
| `register` връщал `item_loop` вместо `connected:1` | ✅ Поправен | Нов отговор: `{count:0, pin:false, connected:1}` |
| `serverstatus` без SN полета | ✅ Поправен | Добавени `isSqueezenetwork:1`, `sn_version` |
| `news`/`weather` ползвали `name` вместо `text` | ✅ Поправен | Всички items ползват `text` |
| Липсващ `/api/v1/session` endpoint | ✅ Поправен | Добавен |
| Липсващ `/api/v1/deviceRegistration` | ✅ Поправен | Добавен |

---

### 18.2 `squeezecloud/SetupWelcomeApplet.lua.orig` — Оригинален Lua applet

**Размер:** 615 реда  
**Роля:** Оригиналният setup wizard на устройството. Управлява целия процес от включване до главното меню.

#### Критично важни функции

| Функция | Ред | Описание |
|---------|-----|----------|
| `startSetup(self)` | 65 | Входна точка при първи старт |
| `step7(self)` | 227 | **Критичен** — след мрежов setup, опитва SN регистрация |
| `_setupDone(self, setupDone, registerDone)` | 547 | Записва настройки `setupDone`/`registerDone` |
| `_registerRequest(self, squeezenetwork)` | 447 | Изпраща `register service:SN` Comet заявка |
| `notify_serverLinked(self, server, wasAlreadyLinked)` | 504 | **Критичен** — извиква `step9` при `pin == false` |
| `_squeezenetworkConnected(self, squeezenetwork)` | 210 | Проверява SN статус |
| `step9(self)` | 467 | Финализира setup, вика `jiveMain:goHome()` |

#### Signup Screen — как се задейства

```
step7()
  ↓
_setupDone(true, false)   ← registerDone = false (НЕ регистриран)
  ↓
settings.registerDone?    ← false → продължава
  ↓
_registerRequest()        ← изпраща register service:SN
  ↓
[чака notify_serverLinked]
  ↓
notify_serverLinked()     ← ако server:getPin() == false → step9()
  ↓
step9()                   ← jiveMain:goHome() → Главно меню ✓
```

**Ако `notify_serverLinked` никога не се извика → устройството остава на signup screen!**

---

### 18.3 `squeezecloud/SetupWelcomeApplet.patched.lua` — Патчнатият applet

**Размер:** 615 реда (идентичен с оригинала)  
**Роля:** Пропуска SN регистрацията. Разликата е само на ред 231.

#### Разлика (patch)

```diff
- self:_setupDone(true, false)
+ self:_setupDone(true, true)  -- SqueezeCloud patch: skip SN registration
```

#### Как работи патчът

```
step7()
  ↓
_setupDone(true, true)    ← registerDone = TRUE (вече "регистриран")
  ↓
settings.registerDone?    ← TRUE → влиза в if-блока
  ↓
_setupComplete(true)      ← setup завършен
  ↓
return                    ← ИЗЛИЗА → _registerRequest() НИКОГА не се вика
  ↓
Главно меню ✓             ← без signup screen!
```

#### Инсталиране на патча

```bash
# Бърза инсталация:
bash squeezecloud/deploy_lua_patch.sh 192.168.1.72

# Или ръчно:
TARGET="/mnt/storage/usr/share/jive/applets/SetupWelcome/SetupWelcomeApplet.lua"
scp -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oCiphers=+aes128-cbc \
    -oMACs=+hmac-sha1 \
    SetupWelcomeApplet.patched.lua root@192.168.1.72:"$TARGET"
```

---

### 18.4 `squeezecloud/deploy_lua_patch.sh` — Скрипт за инсталиране

**Роля:** Автоматизира инсталацията на SetupWelcomeApplet патча.

```bash
# Използване:
bash deploy_lua_patch.sh <IP>
bash deploy_lua_patch.sh 192.168.1.72
```

Стъпки:
1. Проверява дали patch файлът съществува
2. SSH → mkdir на target директорията
3. SCP → копира патчнатия файл
4. Проверява инсталацията
5. Рестартира устройството

---

### 18.5 `squeezecloud/requirements.txt` — Python зависимости

```
fastapi>=0.110.0      # Web framework
uvicorn[standard]     # ASGI сървър (asyncio)
httpx>=0.27.0         # HTTP клиент за external APIs
```

**Инсталация:** `pip install -r requirements.txt`

---

### 18.6 `worker.js` — Cloudflare Worker версия

**Размер:** ~709 реда  
**Роля:** Алтернативна имплементация като Cloudflare Worker (edge функция).

Когато нямаш постоянна машина в мрежата — деплойваш worker.js в Cloudflare и пренасочваш DNS на устройството към него.

**Разлика от main.py:**
- Работи в Cloudflare Edge network (v8 JavaScript runtime)
- Ползва Cloudflare KV за кеш на радио станции
- Няма TCP SlimProto — само HTTP endpoints
- Достъпен от всяка мрежа (не само локална)

**Ограничения:**
- Без SlimProto TCP 3483 — устройството може да не се свърже правилно
- Без UDP discovery — нужен hosts patch на устройството
- Cloudflare free tier: 100,000 заявки/ден

---

## 19. Signup Screen — Пълен анализ и решения

### Симптом

Устройството стартира → свързва се с мрежата → показва **"mysqueezebox.com signup"** екран вместо главното меню.

### Защо се появява signup screen (технически)

```
Jive firmware — step7() ─────────────────────────────────────────────
               │
               ▼ _setupDone(true, false) → registerDone=false
               │
               ▼ settings.registerDone? → false
               │
               ▼ _registerRequest(squeezenetwork)
               │    └─ POST /cometd ["register", 0, 100, "service:SN"]
               │
               ▼ [чака notify_serverLinked callback]
               │
               ├─ Ако callback пристигне с server:getPin()==false:
               │   └─ step9() → goHome() → ГЛАВНО МЕНЮ ✓
               │
               └─ Ако callback НЕ пристигне (сървърът не отговаря правилно):
                   └─ [timeout/error] → SIGNUP SCREEN ✗
```

### Диагностика

#### Стъпка 1: Проверете сервъра

```bash
# Работи ли сървърът?
curl http://192.168.1.43:9000/

# Правилен ли е register отговорът?
curl -s -X POST http://192.168.1.43:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":1,"method":"slim.request","params":["",["register",0,100,"service:SN"]]}'
# Трябва: "pin":false, "connected":1

# Правилен ли е serverstatus?
curl -s -X POST http://192.168.1.43:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":2,"method":"slim.request","params":["",["serverstatus",0,50]]}'
# Трябва: "isSqueezenetwork":1, "pin":false
```

#### Стъпка 2: Проверете hosts файла

```bash
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oCiphers=+aes128-cbc \
    -oMACs=+hmac-sha1 \
    root@192.168.1.72

cat /mnt/storage/etc/hosts
# Трябва: 192.168.1.43 mysqueezebox.com
```

#### Стъпка 3: Проверете Lua патча

```bash
cat /mnt/storage/usr/share/jive/applets/SetupWelcome/SetupWelcomeApplet.lua | grep "SqueezeCloud patch"
# Трябва: -- SqueezeCloud patch: skip SN registration
```

#### Стъпка 4: Логове на устройството

```bash
tail -f /var/log/messages | grep -E "step[0-9]|register|SN|Comet"
```

### Решение A: Сървърни поправки (вече приложено)

Актуализираният `main.py` вече:
1. Връща `pin: false, connected: 1` при `register service:SN`
2. Включва `isSqueezenetwork: 1` в `serverstatus`  
3. Предоставя `/api/v1/session` и `/api/v1/deviceRegistration`

**Рестартирай сървъра и устройството** → може да е достатъчно!

### Решение B: Lua патч (за пълна надеждност)

```bash
bash squeezecloud/deploy_lua_patch.sh 192.168.1.72
```

---

## 20. Бързо валидиране (Quick Validation Checklist)

Използвай тези команди за бързо валидиране след промени:

```bash
cd squeezecloud && python main.py &
sleep 2

echo "=== AUTH ===" && curl -s "http://localhost:9000/api/v1/login?mac=00:04:20:aa:bb:cc" | python3 -m json.tool | grep -E "status|sn_version|userId"
echo "=== SESSION ===" && curl -s "http://localhost:9000/api/v1/session" | python3 -m json.tool | grep -E "loggedIn|userId"
echo "=== REGISTER ===" && curl -s -X POST http://localhost:9000/jsonrpc.js -H "Content-Type: application/json" -d '{"id":1,"method":"slim.request","params":["",["register",0,100,"service:SN"]]}' | python3 -m json.tool | grep -E "pin|connected"
echo "=== SERVERSTATUS ===" && curl -s -X POST http://localhost:9000/jsonrpc.js -H "Content-Type: application/json" -d '{"id":2,"method":"slim.request","params":["",["serverstatus",0,50]]}' | python3 -m json.tool | grep -E "isSqueeze|pin"
echo "=== MENU ===" && curl -s -X POST http://localhost:9000/jsonrpc.js -H "Content-Type: application/json" -d '{"id":3,"method":"slim.request","params":["aa:bb:cc",["menu",0,100]]}' | python3 -m json.tool | grep -E "count|text" | head -10
echo "=== RADIOS ===" && curl -s http://localhost:9000/api/v1/radios | python3 -m json.tool | grep count
echo "=== FAVORITES ===" && curl -s -X POST http://localhost:9000/jsonrpc.js -H "Content-Type: application/json" -d '{"id":4,"method":"slim.request","params":["aa:bb:cc",["favorites",0,5]]}' | python3 -m json.tool | grep -E "count|text" | head -5
```

---

## 21. Файлова система на устройството — подробен анализ

### `/usr/share/jive/` — Lua firmware (cramfs, read-only)

```
/usr/share/jive/
├── jive/
│   ├── net/
│   │   ├── Comet.lua          ← Bayeux client (POST /cometd)
│   │   ├── SlimProto.lua      ← TCP 3483 SlimProto client
│   │   ├── Networking.lua     ← WiFi управление
│   │   └── DNS.lua            ← DNS resolution
│   ├── slim/
│   │   ├── SlimServer.lua     ← Управлява сървърна връзка, isSqueezeNetwork()
│   │   ├── LocalPlayer.lua    ← Локален плейър обект
│   │   └── Player.lua         ← Абстрактен плейър
│   └── ui/
│       ├── Framework.lua      ← UI framework
│       ├── SimpleMenu.lua     ← Менюта
│       └── Window.lua         ← Прозорци
└── applets/
    ├── SetupWelcome/
    │   └── SetupWelcomeApplet.lua  ← SETUP WIZARD (патчваме!)
    ├── SlimBrowser/
    │   └── SlimBrowserApplet.lua   ← Browse менюта
    ├── NowPlaying/
    │   └── NowPlayingApplet.lua    ← Now Playing
    └── Settings/
        └── SettingsApplet.lua      ← Настройки
```

### `/mnt/storage/` — Persistent storage (ubifs, rw)

```
/mnt/storage/
├── etc/
│   └── hosts              ← DNS override (ЗАДЪЛЖИТЕЛНО!)
└── usr/share/jive/
    └── applets/SetupWelcome/
        └── SetupWelcomeApplet.lua  ← Нашият патч
```

### `/var/log/messages` — Логове

```bash
# Live логове от SSH:
ssh ... root@192.168.1.72 "tail -f /var/log/messages"

# Филтрирани:
ssh ... root@192.168.1.72 "grep -E 'step[0-9]|register|SN|Comet' /var/log/messages | tail -50"
```

---

## 22. Работен процес при промени

### Промяна в `main.py`

```bash
# 1. Провери синтаксис
python3 -c "import ast; ast.parse(open('squeezecloud/main.py').read()); print('OK')"

# 2. Стартирай сървъра
cd squeezecloud && python main.py

# 3. В нов терминал — валидиране
curl -s http://localhost:9000/ && echo "OK"

# 4. Рестартирай устройството
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa \
    -oCiphers=+aes128-cbc -oMACs=+hmac-sha1 root@192.168.1.72 "reboot"
```

### Промяна в `SetupWelcomeApplet.patched.lua`

```bash
# 1. Редактирай patched версията
nano squeezecloud/SetupWelcomeApplet.patched.lua

# 2. Провери diff
diff squeezecloud/SetupWelcomeApplet.lua.orig squeezecloud/SetupWelcomeApplet.patched.lua

# 3. Деплой
bash squeezecloud/deploy_lua_patch.sh 192.168.1.72
```
