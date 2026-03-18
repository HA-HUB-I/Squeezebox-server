# SqueezeCloud — локален FastAPI сървър

Имитира mysqueezebox.com локално. Без cloud, без абонамент.

## Инсталация

```bash
pip install -r requirements.txt
python main.py
```

Сървърът стартира на порт **9000** и показва точните команди за Squeezebox.

## Добавяне в Squeezebox (еднократно, постоянно)

```bash
# SSH към Squeezebox:
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa -oCiphers=+aes128-cbc -oMACs=+hmac-sha1 root@192.168.1.72


# На устройството (смени LOCAL_IP с IP-то от стартовия екран):
echo "LOCAL_IP mysqueezebox.com" >> /mnt/storage/etc/hosts
echo "LOCAL_IP www.mysqueezebox.com" >> /mnt/storage/etc/hosts
echo "LOCAL_IP update.squeezenetwork.com" >> /mnt/storage/etc/hosts
reboot
```

## Autostart (Linux systemd)

```bash
sudo nano /etc/systemd/system/squeezecloud.service
```

```ini
[Unit]
Description=SqueezeCloud Server
After=network.target

[Service]
WorkingDirectory=/path/to/squeezecloud
ExecStart=/usr/bin/python3 main.py
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable squeezecloud
sudo systemctl start squeezecloud
```

## API Endpoints

| Endpoint | Описание |
|----------|----------|
| GET /api/v1/login | Auth по MAC адрес |
| GET /api/v1/time | Сървърно време |
| POST /jsonrpc.js | LMS JSON-RPC команди |
| GET /api/v1/radios | Радио станции |
| GET /api/v1/weather | Времето (Open-Meteo) |
| GET /api/v1/news?lang=bg | Новини RSS |
| GET /api/v1/podcasts?feed=0 | Подкасти |
| GET /docs | Swagger UI |

## Добавяне на станции

В `main.py` намери `STATIC_STATIONS` и добави:
```python
{"name": "Моя Станция", "url": "https://stream.example.com/mp3", "genre": "Rock", "country": "BG"},
```

## Добавяне на подкасти/новини

В `PODCAST_FEEDS` или `NEWS_FEEDS` добави RSS feed URL.
