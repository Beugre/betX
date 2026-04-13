# 🚀 Guide de Déploiement VPS – betX

## Architecture

```
Internet → nginx (:80/:443) → Streamlit (:8501 localhost)
                                    ↑
                              cron (8h UTC) → daily_scan.py → EmailJS API
                                                    ↓
                                            data/daily_bets.json
```

- **nginx** : reverse proxy, HTTPS, headers de sécurité
- **Streamlit** : dashboard web (service systemd, auto-restart)
- **cron** : scan quotidien à 8h UTC + envoi email via EmailJS
- **Pas besoin de Streamlit Cloud** : tout tourne sur ton VPS

---

## Prérequis VPS

| | Minimum | Recommandé |
|---|---|---|
| OS | Ubuntu 20.04 / Debian 11 | Ubuntu 22.04 |
| RAM | 1 GB | 2 GB |
| CPU | 1 vCPU | 2 vCPU |
| Disque | 10 GB | 20 GB |
| Python | 3.11+ | 3.12 |

> Un VPS à **~5€/mois** (OVH, Hetzner, DigitalOcean) suffit largement.

---

## Déploiement

### 1. Copier le projet sur le VPS

```bash
# Option A : Git
ssh user@ton-vps
git clone https://github.com/ton-repo/betx.git ~/betx

# Option B : SCP depuis ton Mac
scp -r /chemin/vers/betX user@ton-vps:~/betx
```

### 2. Configurer .env

```bash
cd ~/betx
cp .env.example .env
nano .env
```

Remplir les variables (voir `.env.example`).

### 3. Lancer le déploiement

```bash
# Sans domaine (accès par IP)
chmod +x deploy.sh
./deploy.sh --no-ssl

# Avec un domaine + HTTPS
./deploy.sh --domain betx.monsite.com
```

Le script fait tout automatiquement :
1. ✅ Installe Python + pip + nginx + certbot
2. ✅ Crée le venv + installe les packages
3. ✅ Configure nginx (reverse proxy)
4. ✅ Active HTTPS Let's Encrypt (si domaine)
5. ✅ Configure le cron (scan 8h UTC)
6. ✅ Lance Streamlit en service systemd

---

## Accéder au dashboard

```
# Sans domaine
http://IP_DU_VPS

# Avec domaine + SSL
https://betx.monsite.com
```

---

## Commandes utiles

```bash
# Scan manuel + email
cd ~/betx && .venv/bin/python daily_scan.py --email

# Status du dashboard
sudo systemctl status betx-dashboard

# Redémarrer le dashboard
sudo systemctl restart betx-dashboard

# Logs en temps réel
sudo journalctl -u betx-dashboard -f

# Voir le cron
crontab -l

# Logs du cron
tail -f ~/betx/logs/cron.log

# Renouveler le certificat SSL
sudo certbot renew --dry-run
```

---

## DNS (si domaine)

Ajouter un **enregistrement A** chez ton registrar :

```
Type: A
Nom:  betx       (ou @ pour le domaine racine)
IP:   IP_DU_VPS
TTL:  3600
```

Attendre la propagation DNS (5-30 min), puis relancer :
```bash
./deploy.sh --domain betx.monsite.com
```

---

## Mise à jour du code

```bash
ssh user@ton-vps
cd ~/betx
git pull
sudo systemctl restart betx-dashboard
```

---

## Sécurité

Le script configure automatiquement :
- ✅ Streamlit écoute sur `127.0.0.1` (pas exposé directement)
- ✅ nginx headers de sécurité (X-Frame-Options, XSS, etc.)
- ✅ HTTPS avec Let's Encrypt (renouvellement auto)
- ✅ systemd sandboxing (NoNewPrivileges, PrivateTmp)

Recommandations supplémentaires :
```bash
# Firewall UFW
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# Fail2ban (protection brute force SSH)
sudo apt install fail2ban
sudo systemctl enable fail2ban
```
