# SqueezeCloud Worker

Cloudflare Worker that impersonates mysqueezebox.com for Squeezebox Radio devices.
No local server needed — works from any network.

## Features

- 📻 Internet Radio (200+ stations via Radio Browser API + static BG stations)
- 🎙️ Podcasts (RSS parsing — БНР, BBC, TED, Radiolab, etc.)
- 🌤️ Weather (Open-Meteo — free, no API key)
- 📰 News (RSS — БНР, Dnevnik, Reuters, BBC)
- 💾 KV caching for radio stations (1 hour TTL)
- 🔌 Works on any WiFi — no router access needed

## Deploy

### 1. Install Wrangler
```bash
npm install -g wrangler
wrangler login
```

### 2. Create KV namespace
```bash
wrangler kv:namespace create "RADIO_KV"
# Copy the ID and paste it in wrangler.toml
```

### 3. Deploy
```bash
wrangler deploy
# You get: https://squeezecloud.YOUR_SUBDOMAIN.workers.dev
```

### 4. Get Worker IP
```bash
dig +short squeezecloud.YOUR_SUBDOMAIN.workers.dev
# or
nslookup squeezecloud.YOUR_SUBDOMAIN.workers.dev
```

### 5. Deploy Lua patch + auto-patch hosts file (една команда!)
```bash
# Синтаксис: bash deploy_lua_patch.sh <IP_НА_SQUEEZEBOX> [IP_НА_СЪРВЪРА]
cd squeezecloud
bash deploy_lua_patch.sh 192.168.1.72 192.168.1.43
```

Скриптът автоматично:
1. Инсталира patched `SqueezeboxBabyMeta.lua` — вика `jnt:setSNHostname(IP)` и пренасочва
   TCP Slim Protocol (порт 3483) към нашия сървър **на Lua ниво** (заобикаля хардкодирания `www.squeezenetwork.com`).
2. Инсталира patched `SetupWelcomeApplet.lua` (пропуска signup screen).
3. Инсталира patched `SlimDiscoveryApplet.lua` (UDP discovery порт).
4. Добавя в `/mnt/storage/etc/hosts` на устройството:
   `mysqueezebox.com`, `www.mysqueezebox.com`, `www.squeezenetwork.com`, `update.squeezenetwork.com`, `config.logitechmusic.com`
   → така и DNS заявките от C слоя се пренасочват.
5. Рестартира устройството.

#### Ръчна промяна на hosts (алтернативно)
```bash
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa -oCiphers=+aes128-cbc -oMACs=+hmac-sha1 root@192.168.1.72

# On the device:
cat > /mnt/storage/etc/hosts << 'EOF'
127.0.0.1 localhost
192.168.1.43 mysqueezebox.com
192.168.1.43 www.mysqueezebox.com
192.168.1.43 www.squeezenetwork.com
192.168.1.43 update.squeezenetwork.com
192.168.1.43 config.logitechmusic.com
EOF
sync

# Verify:
cat /mnt/storage/etc/hosts
```

> **Важно:** `www.squeezenetwork.com` е задължителен — firmware-ът го използва за
> TCP Slim Protocol (порт 3483). Без него аудио командите не стигат до сървъра.

### 6. Restart Squeezebox
Hold power button → reboot
Or from SSH: `reboot`

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| GET /api/v1/login | Device auth by MAC |
| GET /api/v1/time | Server time |
| POST /jsonrpc.js | LMS JSON-RPC commands |
| GET /api/v1/radios | Radio station browse |
| GET /api/v1/apps | Apps list |
| GET /api/v1/weather?lat=X&lon=Y | Weather |
| GET /api/v1/news?lang=bg | News RSS |
| GET /api/v1/podcasts?feed=0 | Podcast episodes |

## Adding More Radio Stations

Edit `STATIC_STATIONS` array in worker.js — add any stream URL:
```js
{ name: "My Station", url: "https://stream.example.com/mp3", genre: "Rock", country: "BG" }
```

## Cloudflare Free Tier Limits
- 100,000 requests/day ✅ (plenty for home use)
- KV: 100,000 reads/day ✅
- No always-on cost ✅

## Troubleshooting

**Device still shows signup screen / "DNS failed"**
→ Re-run the deploy script — it will patch both Lua and hosts automatically:
  `bash deploy_lua_patch.sh 192.168.1.72 192.168.1.43`
→ Or verify hosts file manually: `cat /mnt/storage/etc/hosts`
→ Flush DNS on device: reboot

**No radio stations showing**
→ Test Worker: `curl https://squeezecloud.X.workers.dev/api/v1/radios`

**Weather not working**
→ Open-Meteo is free and needs no API key — check Worker logs in CF dashboard