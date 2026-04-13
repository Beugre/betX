#!/bin/bash
# ─── betX – Script de déploiement VPS (Production) ──────────────────────
#
# Usage :
#   chmod +x deploy.sh
#   ./deploy.sh                  # Déploiement complet
#   ./deploy.sh --no-ssl         # Sans HTTPS (pas de domaine)
#   ./deploy.sh --domain betx.monsite.com
#
# Prérequis sur le VPS :
#   - Ubuntu 20.04+ / Debian 11+
#   - Python 3.11+
#   - Accès root (sudo)
#
# Ce script :
#   1. Installe Python + dépendances système
#   2. Crée le venv + installe les packages
#   3. Configure nginx (reverse proxy)
#   4. Active HTTPS avec Let's Encrypt (optionnel)
#   5. Configure le cron quotidien (scan + email via EmailJS)
#   6. Lance Streamlit en service systemd
# ─────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── Arguments CLI ────────────────────────────────────────────────────────

DOMAIN=""
SKIP_SSL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --domain)    DOMAIN="$2"; shift 2 ;;
        --no-ssl)    SKIP_SSL=true; shift ;;
        *)           echo "Usage: $0 [--domain betx.example.com] [--no-ssl]"; exit 1 ;;
    esac
done

# ─── Configuration ────────────────────────────────────────────────────────

APP_NAME="betx"
APP_DIR="$HOME/betx"
VENV_DIR="$APP_DIR/.venv"
PYTHON="python3"
STREAMLIT_PORT=8501         # Port interne (nginx le proxifie)
SCAN_HOUR="08:00"           # Heure du scan quotidien (UTC)
SERVER_NAME="${DOMAIN:-_}"  # _ = default server si pas de domaine

echo ""
echo "🚀 ═══════════════════════════════════════════════════"
echo "   betX – Déploiement VPS Production"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "📁 Répertoire  : $APP_DIR"
echo "🌐 Domaine     : ${DOMAIN:-'(aucun – accès par IP)'}"
echo "🔒 SSL/HTTPS   : $([ "$SKIP_SSL" = true ] && echo 'Non' || echo 'Oui (Let'\''s Encrypt)')"
echo "⏰ Scan cron   : $SCAN_HOUR UTC"
echo ""

# ─── 1. Dépendances système ──────────────────────────────────────────────

echo "📦 [1/7] Installation des dépendances système..."

sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip \
    nginx \
    git \
    curl \
    certbot python3-certbot-nginx \
    > /dev/null 2>&1

PYTHON_VERSION=$($PYTHON --version 2>&1)
echo "   ✅ $PYTHON_VERSION"
echo "   ✅ nginx $(nginx -v 2>&1 | grep -oP '[\d.]+')"

# ─── 2. Environnement virtuel + packages ─────────────────────────────────

echo ""
echo "📦 [2/7] Configuration de l'environnement Python..."

if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
    echo "   ✅ venv créé"
fi

source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet \
    httpx \
    python-dotenv \
    rich \
    streamlit \
    pandas \
    numpy \
    scipy

# Installer betx en mode dev si pyproject.toml existe
if [ -f "$APP_DIR/pyproject.toml" ]; then
    pip install --quiet -e "$APP_DIR"
fi

echo "   ✅ Packages Python installés"

# ─── 3. Vérifier .env ────────────────────────────────────────────────────

echo ""
echo "🔑 [3/7] Vérification de la configuration..."

if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo "   ⚠️  .env créé depuis .env.example – CONFIGURE-LE :"
    else
        echo "   ⚠️  .env manquant ! Crée-le avec ces variables :"
    fi
    echo ""
    echo "   nano $APP_DIR/.env"
    echo ""
    echo "   Variables requises :"
    echo "   ─────────────────────────────────────────"
    echo "   ODDS_API_KEY=...           # the-odds-api.com"
    echo "   API_FOOTBALL_KEY=...       # api-football.com"
    echo "   TELEGRAM_BOT_TOKEN=...     # @BotFather"
    echo "   TELEGRAM_CHAT_ID=...       # python daily_scan.py --get-chat-id"
    echo "   ─────────────────────────────────────────"
    echo ""
else
    echo "   ✅ .env présent"
fi

# Créer les dossiers nécessaires
mkdir -p "$APP_DIR/logs" "$APP_DIR/data/cache"
echo "   ✅ Dossiers logs/ et data/cache/ prêts"

# ─── 4. Nginx – Reverse Proxy ────────────────────────────────────────────

echo ""
echo "🌐 [4/7] Configuration nginx (reverse proxy)..."

NGINX_CONF="/etc/nginx/sites-available/$APP_NAME"

sudo tee "$NGINX_CONF" > /dev/null <<NGINXEOF
# betX – Reverse Proxy pour Streamlit
# Généré par deploy.sh le $(date '+%Y-%m-%d %H:%M')

server {
    listen 80;
    server_name $SERVER_NAME;

    # Taille max upload (pour les requêtes Streamlit)
    client_max_body_size 10M;

    # Headers de sécurité
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Reverse proxy vers Streamlit
    location / {
        proxy_pass http://127.0.0.1:$STREAMLIT_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_buffering off;
    }

    # Healthcheck endpoint
    location /healthz {
        return 200 '{"status":"ok","service":"betx"}';
        add_header Content-Type application/json;
    }
}
NGINXEOF

# Activer le site
sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default  # Supprimer le site par défaut

# Tester et recharger nginx
sudo nginx -t
sudo systemctl reload nginx

echo "   ✅ nginx configuré (proxy :80 → :$STREAMLIT_PORT)"

# ─── 5. HTTPS avec Let's Encrypt ─────────────────────────────────────────

echo ""
if [ "$SKIP_SSL" = false ] && [ -n "$DOMAIN" ]; then
    echo "🔒 [5/7] Activation HTTPS (Let's Encrypt)..."

    sudo certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --redirect \
        --email "$(grep EMAIL_TO "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 'admin@example.com')"

    echo "   ✅ HTTPS activé pour $DOMAIN"
    echo "   ℹ️  Renouvellement auto via certbot.timer"
else
    echo "🔒 [5/7] SSL ignoré $([ -z "$DOMAIN" ] && echo '(pas de domaine)' || echo '(--no-ssl)')"
fi

# ─── 6. Cron quotidien ───────────────────────────────────────────────────

echo ""
echo "⏰ [6/7] Configuration du scan quotidien..."

CRON_CMD="cd $APP_DIR && $VENV_DIR/bin/python daily_scan.py --notify >> $APP_DIR/logs/cron.log 2>&1"
CRON_HOUR=$(echo $SCAN_HOUR | cut -d: -f1)
CRON_MIN=$(echo $SCAN_HOUR | cut -d: -f2)
CRON_LINE="$CRON_MIN $CRON_HOUR * * * $CRON_CMD"

# Ajouter au crontab (sans dupliquer)
(crontab -l 2>/dev/null | grep -v "daily_scan.py"; echo "$CRON_LINE") | crontab -

echo "   ✅ Cron configuré : scan quotidien à $SCAN_HOUR UTC"

# ─── 7. Service systemd (Streamlit) ──────────────────────────────────────

echo ""
echo "🖥️  [7/7] Lancement du dashboard Streamlit..."

SERVICE_FILE="/etc/systemd/system/${APP_NAME}-dashboard.service"

sudo tee "$SERVICE_FILE" > /dev/null <<SVCEOF
[Unit]
Description=betX Streamlit Dashboard
After=network.target nginx.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/streamlit run app.py --server.port=$STREAMLIT_PORT --server.headless=true --server.address=127.0.0.1 --server.enableCORS=false --server.enableXsrfProtection=false --browser.gatherUsageStats=false
Restart=always
RestartSec=10
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin

# Sécurité
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable "${APP_NAME}-dashboard"
sudo systemctl restart "${APP_NAME}-dashboard"

# Attendre que le service démarre
sleep 3
if systemctl is-active --quiet "${APP_NAME}-dashboard"; then
    echo "   ✅ Streamlit actif sur 127.0.0.1:$STREAMLIT_PORT"
else
    echo "   ⚠️  Le service n'a pas démarré. Vérifier avec :"
    echo "      sudo journalctl -u ${APP_NAME}-dashboard -n 20"
fi

# ─── Résumé final ─────────────────────────────────────────────────────────

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "IP_DU_VPS")
URL="${DOMAIN:-$IP}"
PROTOCOL=$( [ "$SKIP_SSL" = false ] && [ -n "$DOMAIN" ] && echo "https" || echo "http" )

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  🎉 Déploiement betX terminé !"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  📊 Dashboard   : $PROTOCOL://$URL"
echo "  � Telegram    : quotidien à $SCAN_HOUR UTC (@BetX_goat_bot)"
echo "  📁 Données     : $APP_DIR/data/daily_bets.json"
echo "  📋 Logs cron   : $APP_DIR/logs/cron.log"
echo "  📋 Logs Strml  : sudo journalctl -u ${APP_NAME}-dashboard -f"
echo ""
echo "  Commandes utiles :"
echo "  ──────────────────────────────────────────────────────"
echo "  Scan manuel     : cd $APP_DIR && .venv/bin/python daily_scan.py --notify"
echo "  Benchmark manuel: cd $APP_DIR && .venv/bin/python -m betx --site-benchmark --history-days 60"
echo "  Scheduler bench : sudo systemctl status ${APP_NAME}-benchmark-scheduler"
echo "  Status dashboard: sudo systemctl status ${APP_NAME}-dashboard"
echo "  Restart         : sudo systemctl restart ${APP_NAME}-dashboard"
echo "  Logs temps réel : sudo journalctl -u ${APP_NAME}-dashboard -f"
echo "  Renouveler SSL  : sudo certbot renew --dry-run"
echo "  Voir cron       : crontab -l"
echo ""

# ─── Optionnel : Service scheduler benchmark externe ─────────────────────

SCHED_SERVICE_FILE="/etc/systemd/system/${APP_NAME}-benchmark-scheduler.service"

sudo tee "$SCHED_SERVICE_FILE" > /dev/null <<SCHEDSVCEOF
[Unit]
Description=betX External Benchmark Scheduler
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python -m betx --benchmark-scheduler
Restart=always
RestartSec=10
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SCHEDSVCEOF

sudo systemctl daemon-reload
sudo systemctl enable "${APP_NAME}-benchmark-scheduler"
sudo systemctl restart "${APP_NAME}-benchmark-scheduler"
