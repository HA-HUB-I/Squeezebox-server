#!/bin/bash
# =============================================================================
# deploy_lua_patch.sh — Инсталира SqueezeCloud Lua патчове на Squeezebox Radio
#
# Патчовете:
#  1. SetupWelcomeApplet — пропуска SqueezeNetwork регистрацията (signup screen)
#  2. SqueezeboxBabyMeta — пренасочва SN hostname към custom сървър (опционално)
#  3. SlimDiscoveryApplet — сменя порт 9000 → 80 (за Cloudflare Worker)
#
# Използване:
#   bash deploy_lua_patch.sh <IP_НА_SQUEEZEBOX> [IP_НА_СЪРВЪРА] [CUSTOM_HOSTNAME]
#
#   Локален сървър (Python main.py):
#     bash deploy_lua_patch.sh 192.168.1.72 192.168.1.43
#
#   Cloudflare Worker — custom hostname (без /etc/hosts):
#     bash deploy_lua_patch.sh 192.168.1.72 "" squeezecloud.YOUR_SUBDOMAIN.workers.dev
#
#   Cloudflare Worker — /etc/hosts с автоматично разрешаване на IP:
#     bash deploy_lua_patch.sh 192.168.1.72 "$(dig +short squeezecloud.YOUR_SUBDOMAIN.workers.dev | head -1)"
# =============================================================================

set -e

SQUEEZEBOX_IP="${1:-192.168.1.72}"
SERVER_IP="${2:-192.168.1.43}"
CUSTOM_HOSTNAME="${3:-}"   # Ако е зададен — ще се патчне SqueezeboxBabyMeta + SlimDiscovery
SN_PORT="${4:-80}"         # Порт за Cloudflare Worker (default 80); за локален сървър използвай 9000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WELCOME_PATCH="$SCRIPT_DIR/SetupWelcomeApplet.patched.lua"
BABY_META_PATCH="$SCRIPT_DIR/SqueezeboxBabyMeta.patched.lua"
DISCOVERY_PATCH="$SCRIPT_DIR/SlimDiscoveryApplet.patched.lua"

if [ ! -f "$WELCOME_PATCH" ]; then
    echo "Грешка: Не намирам $WELCOME_PATCH"
    exit 1
fi

echo "========================================================"
echo "  Инсталиране на SqueezeCloud Lua патчове"
echo "  Squeezebox IP:        $SQUEEZEBOX_IP"
if [ -n "$CUSTOM_HOSTNAME" ]; then
    echo "  Custom SN hostname:   $CUSTOM_HOSTNAME  (port 80)"
    echo "  Режим:                Директна DNS връзка (без /etc/hosts)"
else
    echo "  SqueezeCloud IP:      $SERVER_IP"
    echo "  Режим:                /etc/hosts пренасочване (port 9000)"
fi
echo "========================================================"

SSH_OPTS="-oKexAlgorithms=+diffie-hellman-group1-sha1 \
          -oHostKeyAlgorithms=+ssh-rsa \
          -oCiphers=+aes128-cbc \
          -oMACs=+hmac-sha1 \
          -oStrictHostKeyChecking=no \
          -o ConnectTimeout=10"

HOSTS_FILE="/mnt/storage/etc/hosts"
WELCOME_TARGET_DIR="/mnt/storage/usr/share/jive/applets/SetupWelcome"
WELCOME_TARGET_FILE="$WELCOME_TARGET_DIR/SetupWelcomeApplet.lua"
BABY_TARGET_DIR="/mnt/storage/usr/share/jive/applets/SqueezeboxBaby"
BABY_TARGET_FILE="$BABY_TARGET_DIR/SqueezeboxBabyMeta.lua"
DISCOVERY_TARGET_DIR="/mnt/storage/usr/share/jive/applets/SlimDiscovery"
DISCOVERY_TARGET_FILE="$DISCOVERY_TARGET_DIR/SlimDiscoveryApplet.lua"

# ── Стъпка 1: /etc/hosts — само ако НЕ се използва custom hostname ──────────
if [ -z "$CUSTOM_HOSTNAME" ]; then
    echo ""
    echo "1. Патчване на /etc/hosts — пренасочване на mysqueezebox.com към $SERVER_IP ..."
    ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "
        grep -v 'mysqueezebox.com\|squeezenetwork.com' $HOSTS_FILE 2>/dev/null \
            | grep -v '^\s*$' > /tmp/hosts.tmp || echo '127.0.0.1 localhost' > /tmp/hosts.tmp
        echo \"$SERVER_IP mysqueezebox.com\"          >> /tmp/hosts.tmp
        echo \"$SERVER_IP www.mysqueezebox.com\"      >> /tmp/hosts.tmp
        echo \"$SERVER_IP update.squeezenetwork.com\" >> /tmp/hosts.tmp
        cp /tmp/hosts.tmp $HOSTS_FILE
        sync
        echo 'hosts файлът е обновен:'
        cat $HOSTS_FILE
    " 2>/dev/null
else
    echo ""
    echo "1. Custom hostname mode — /etc/hosts НЕ се променя."
    echo "   Устройството ще DNS-разреши $CUSTOM_HOSTNAME директно."
fi

# ── Стъпка 2: SetupWelcomeApplet патч ────────────────────────────────────────
echo ""
echo "2. Създаване на директории..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "mkdir -p $WELCOME_TARGET_DIR" 2>/dev/null || true

echo ""
echo "3. Копиране на SetupWelcomeApplet патч (пропуска signup screen)..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "cat > $WELCOME_TARGET_FILE" < "$WELCOME_PATCH"
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "grep 'SqueezeCloud patch' $WELCOME_TARGET_FILE && echo 'SetupWelcomeApplet патчът е инсталиран!'"

# ── Стъпка 3: SqueezeboxBabyMeta + SlimDiscovery — само с custom hostname ───
if [ -n "$CUSTOM_HOSTNAME" ]; then
    echo ""
    echo "4. Патчване на SqueezeboxBabyMeta.lua — SN hostname → $CUSTOM_HOSTNAME ..."
    if [ ! -f "$BABY_META_PATCH" ]; then
        echo "Предупреждение: Не намирам $BABY_META_PATCH — пропускам."
    else
        # Замени SQUEEZECLOUD_HOSTNAME с реалния hostname (използваме | за разделител,
        # за да избегнем проблеми с точки и наклонени черти в URL-и)
        TMP_BABY=$(mktemp /tmp/SqueezeboxBabyMeta.XXXXXX.lua)
        sed "s|CONFIGURE_ME.squeezecloud.invalid|${CUSTOM_HOSTNAME}|g" "$BABY_META_PATCH" > "$TMP_BABY"
        ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "mkdir -p $BABY_TARGET_DIR" 2>/dev/null || true
        ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "cat > $BABY_TARGET_FILE" < "$TMP_BABY"
        rm -f "$TMP_BABY"
        echo "SqueezeboxBabyMeta патчът е инсталиран!"
    fi

    echo ""
    echo "5. Патчване на SlimDiscoveryApplet.lua — порт 9000 → 80 (Cloudflare Worker)..."
    if [ ! -f "$DISCOVERY_PATCH" ]; then
        echo "Предупреждение: Не намирам $DISCOVERY_PATCH — пропускам."
    else
        # Замени маркера на порта с реалния порт (използваме | за разделител)
        TMP_DISC=$(mktemp /tmp/SlimDiscoveryApplet.XXXXXX.lua)
        sed "s|9000 --\[\[SQUEEZECLOUD_SN_PORT\]\]|${SN_PORT}|g" "$DISCOVERY_PATCH" > "$TMP_DISC"
        ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "mkdir -p $DISCOVERY_TARGET_DIR" 2>/dev/null || true
        ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "cat > $DISCOVERY_TARGET_FILE" < "$TMP_DISC"
        rm -f "$TMP_DISC"
        echo "SlimDiscoveryApplet патчът е инсталиран!"
    fi
else
    echo ""
    echo "4-5. Hostname патчовете се пропускат (използва се /etc/hosts режим)."
fi

# ── Стъпка 6: Рестарт ────────────────────────────────────────────────────────
echo ""
echo "6. Рестартиране на устройството..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "sync && reboot" 2>/dev/null || true

echo ""
echo "========================================================"
echo "  Готово! Изчакай ~30 секунди докато устройството"
echo "  се рестартира."
echo ""
if [ -n "$CUSTOM_HOSTNAME" ]; then
    echo "  Устройството ще се свърже директно към:"
    echo "  $CUSTOM_HOSTNAME:$SN_PORT"
else
    echo "  Устройството ще се свърже към:"
    echo "  $SERVER_IP:9000 (чрез /etc/hosts)"
fi
echo ""
echo "  Ако signup screen пак се появи:"
echo "  1. Провери Lua патча: ssh squeezebox cat $WELCOME_TARGET_FILE | head -5"
if [ -z "$CUSTOM_HOSTNAME" ]; then
    echo "  2. Провери hosts файла: cat $HOSTS_FILE"
    echo "  3. Провери сървъра: python main.py"
else
    echo "  2. Провери Baby Meta: ssh squeezebox cat $BABY_TARGET_FILE | grep setSNHostname"
    echo "  3. Провери Discovery: ssh squeezebox cat $DISCOVERY_TARGET_FILE | grep _serverUpdateAddress"
fi
echo "========================================================"

