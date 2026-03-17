#!/bin/bash
# =============================================================================
# deploy_lua_patch.sh — Инсталира SetupWelcomeApplet патч и hosts файл на
#                        Squeezebox Radio
#
# Патчът пропуска SqueezeNetwork регистрацията (signup screen) като задава
# registerDone=true ПРЕДИ устройството да се опита да се свърже с SN.
# Освен това пренасочва mysqueezebox.com домейните към локалния SqueezeCloud
# сървър директно в /mnt/storage/etc/hosts на устройството.
#
# Използване:
#   bash deploy_lua_patch.sh <IP_НА_SQUEEZEBOX> [IP_НА_СЪРВЪРА]
#   bash deploy_lua_patch.sh 192.168.1.72
#   bash deploy_lua_patch.sh 192.168.1.72 192.168.1.43
# =============================================================================

set -e

SQUEEZEBOX_IP="${1:-192.168.1.72}"
SERVER_IP="${2:-192.168.1.43}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$SCRIPT_DIR/SetupWelcomeApplet.patched.lua"

if [ ! -f "$PATCH_FILE" ]; then
    echo "Грешка: Не намирам $PATCH_FILE"
    exit 1
fi

echo "========================================================"
echo "  Инсталиране на SetupWelcomeApplet патч"
echo "  Squeezebox IP: $SQUEEZEBOX_IP"
echo "  SqueezeCloud сървър IP: $SERVER_IP"
echo "========================================================"

SSH_OPTS="-oKexAlgorithms=+diffie-hellman-group1-sha1 \
          -oHostKeyAlgorithms=+ssh-rsa \
          -oCiphers=+aes128-cbc \
          -oMACs=+hmac-sha1 \
          -oStrictHostKeyChecking=no \
          -o ConnectTimeout=10"

TARGET_DIR="/mnt/storage/usr/share/jive/applets/SetupWelcome"
TARGET_FILE="$TARGET_DIR/SetupWelcomeApplet.lua"
HOSTS_FILE="/mnt/storage/etc/hosts"

echo ""
echo "1. Патчване на /etc/hosts — пренасочване на mysqueezebox.com към $SERVER_IP ..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "
    # Запазваме оригиналния hosts (само 127.0.0.1 localhost), след което
    # добавяме SqueezeCloud записите по идемпотентен начин.
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

echo ""
echo "2. Създаване на директорията на устройството..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "mkdir -p $TARGET_DIR" 2>/dev/null || true

echo ""
echo "3. Копиране на патчнатия Lua файл..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "cat > $TARGET_FILE" < "$PATCH_FILE"

echo ""
echo "4. Проверка на Lua патча..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "grep 'SqueezeCloud patch' $TARGET_FILE && echo 'Lua патчът е инсталиран успешно!'"

echo ""
echo "5. Рестартиране на устройството..."
echo "   (Устройството ще се рестартира и ще зареди без signup screen)"
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "sync && reboot" 2>/dev/null || true

echo ""
echo "========================================================"
echo "  Готово! Изчакай ~30 секунди докато устройството"
echo "  се рестартира."
echo ""
echo "  Ако signup screen пак се появи:"
echo "  1. Провери hosts файла: cat $HOSTS_FILE"
echo "  2. Провери дали сървърът работи: python main.py"
echo "  3. Провери Lua патча: ssh squeezebox cat $TARGET_FILE | head -5"
echo "========================================================"
