#!/bin/bash
# =============================================================================
# deploy_lua_patch.sh — Инсталира SetupWelcomeApplet патч на Squeezebox Radio
#
# Патчът пропуска SqueezeNetwork регистрацията (signup screen) като задава
# registerDone=true ПРЕДИ устройството да се опита да се свърже с SN.
#
# Използване:
#   bash deploy_lua_patch.sh <IP_НА_SQUEEZEBOX>
#   bash deploy_lua_patch.sh 192.168.1.72
# =============================================================================

set -e

SQUEEZEBOX_IP="${1:-192.168.1.72}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$SCRIPT_DIR/SetupWelcomeApplet.patched.lua"

if [ ! -f "$PATCH_FILE" ]; then
    echo "Грешка: Не намирам $PATCH_FILE"
    exit 1
fi

echo "========================================================"
echo "  Инсталиране на SetupWelcomeApplet патч"
echo "  Squeezebox IP: $SQUEEZEBOX_IP"
echo "========================================================"

SSH_OPTS="-oKexAlgorithms=+diffie-hellman-group1-sha1 \
          -oHostKeyAlgorithms=+ssh-rsa \
          -oCiphers=+aes128-cbc \
          -oMACs=+hmac-sha1 \
          -oStrictHostKeyChecking=no \
          -o ConnectTimeout=10"

TARGET_DIR="/mnt/storage/usr/share/jive/applets/SetupWelcome"
TARGET_FILE="$TARGET_DIR/SetupWelcomeApplet.lua"

echo ""
echo "1. Създаване на директорията на устройството..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "mkdir -p $TARGET_DIR" 2>/dev/null || true

echo "2. Копиране на патчнатия файл..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "cat > $TARGET_FILE" < "$PATCH_FILE"

echo "3. Проверка..."
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "grep 'SqueezeCloud patch' $TARGET_FILE && echo 'Патчът е инсталиран успешно!'"

echo ""
echo "4. Рестартиране на устройството..."
echo "   (Устройството ще се рестартира и ще зареди без signup screen)"
ssh $SSH_OPTS root@"$SQUEEZEBOX_IP" "sync && reboot" 2>/dev/null || true

echo ""
echo "========================================================"
echo "  Готово! Изчакай ~30 секунди докато устройството"
echo "  се рестартира."
echo ""
echo "  Ако signup screen пак се появи:"
echo "  1. Провери hosts файла: cat /mnt/storage/etc/hosts"
echo "  2. Провери дали сървърът работи: python main.py"
echo "  3. Провери SSH: ssh squeezebox cat $TARGET_FILE | head -5"
echo "========================================================"
