# 🎯 betX — Stratégie de Value Betting

> Document de référence décrivant le fonctionnement complet du système betX :
> modèle de prédiction, détection de value, staking et gestion du risque.

---

## Table des matières

1. [Principe du Value Betting](#1--principe-du-value-betting)
2. [Architecture du modèle](#2--architecture-du-modèle)
3. [Pipeline de calcul (étape par étape)](#3--pipeline-de-calcul-étape-par-étape)
4. [Détection du Value Bet (Edge)](#4--détection-du-value-bet-edge)
5. [Staking — Gestion des mises](#5--staking--gestion-des-mises)
6. [Paramètres de configuration](#6--paramètres-de-configuration)
7. [Infrastructure & Automatisation](#7--infrastructure--automatisation)

---

## 1 — Principe du Value Betting

Le value betting consiste à parier **uniquement** quand notre estimation de la probabilité d'un résultat est **supérieure** à celle implicite dans la cote du bookmaker.

```
Cote bookmaker = 1.80  →  Probabilité implicite = 1 / 1.80 = 55.6%
Notre modèle dit      →  Probabilité = 65.0%
Edge = 65.0% - 55.6%  →  +9.4%  ✅ Value bet
```

On ne parie **jamais** par intuition. Chaque mise est justifiée par un edge positif calculé mathématiquement. À long terme, les mathématiques font le travail : même si on perd des paris individuels, la valeur attendue positive se matérialise sur un grand nombre de mises.

---

## 2 — Architecture du modèle

Le modèle est un **hybride Poisson + Dixon-Coles + ELO + xG** avec deux modes de fonctionnement :

### Mode Enrichi (stats réelles API-Football)

```
API-Football (stats saison) ──→ Buts marqués/encaissés, xG, forme
              │
              ▼
    ELO calculé depuis le Goal Difference réel
              │
              ▼
    Modèle Poisson → λ_home, λ_away
              │
              ▼
    Matrice de scores 8×8 + Dixon-Coles
              │
              ▼
    P(Home), P(Draw), P(Away), O/U, BTTS
              │
              ▼
    Calibration × 0.97 (quasi-brut)
              │
              ▼
    Comparaison vs cotes → edge ≥ 8% → VALUE BET ✅
```

**✅ Avantage** : Le modèle est **100% indépendant des cotes bookmaker**. Les λ Poisson sont calculés à partir des vrais buts et xG de la saison. Le modèle peut donc détecter des erreurs de pricing que le marché fait.

### Mode Dégradé (sans stats)

Quand les stats API-Football ne sont pas disponibles pour un match, le modèle utilise le **consensus bookmaker** (moyenne des cotes) comme proxy de la force des équipes.

**⚠️ Limitation** : Ce mode est circulaire — on utilise les cotes comme input, donc le modèle ne peut pas être structurellement meilleur que le marché. La calibration est plus forte (0.92) pour réduire les faux positifs.

---

## 3 — Pipeline de calcul (étape par étape)

### Étape 1 — Collecte des données

Pour chaque équipe, on récupère depuis l'API-Football (avec cache 24h) :

| Donnée | Description | Exemple (Arsenal) |
|---|---|---|
| `avg_goals_scored` | Buts marqués par match (dom/ext) | 1.8 |
| `avg_goals_conceded` | Buts encaissés par match | 0.9 |
| `xg_for` | Expected Goals créés | 1.8 |
| `xg_against` | Expected Goals concédés | 0.9 |
| `form` | Résultats des 10 derniers matchs | WWDDWDLDWW |

### Étape 2 — Calcul de l'ELO

L'ELO est dérivé du **goal difference réel** de la saison (pas des cotes) :

```
GD_home = avg_goals_scored_home - avg_goals_conceded_home
GD_away = avg_goals_scored_away - avg_goals_conceded_away

ELO_home = 1500 + (GD_home - GD_away) × 100
ELO_away = 1500 - (GD_home - GD_away) × 100
```

Le domicile reçoit un **bonus de +50** en ELO.

**Exemple** : Arsenal (GD = +0.9) vs Wolves (GD = −0.5) :
- `GD_diff = 0.9 - (-0.5) = 1.4`
- ELO Arsenal = 1640, ELO Wolves = 1360

### Étape 3 — Calcul des λ (Lambdas Poisson)

Les λ représentent le **nombre de buts attendus** par équipe. Le calcul combine **6 facteurs** :

```
λ_home = Attaque_h × Défense_a × Moy_ligue × ELO × Forme × Domicile × Fatigue
```

| Facteur | Formule | Rôle |
|---|---|---|
| **Attaque** | `buts_marqués / moy_ligue` (50% buts + 50% xG) | Force offensive relative |
| **Défense adverse** | `buts_encaissés_adverse / moy_ligue` | Faiblesse défensive adverse |
| **Moyenne ligue** | 1.5 (dom) / 1.2 (ext) | Base de référence (~2.7 buts/match) |
| **ELO** | Multiplicateur entre ×0.7 et ×1.4 | Force globale relative |
| **Forme récente** | W=3, D=1, L=0 (pondération dégressive) | Dynamique actuelle |
| **Domicile** | +7% (`home_advantage = 0.07`) | Avantage du terrain |
| **Fatigue** | <3 jours repos → −8% | Impact de l'enchaînement |

Les λ sont **capés** pour éviter les valeurs aberrantes :
- `λ_home` ∈ [0.3, 4.5]
- `λ_away` ∈ [0.2, 4.0]

**Exemple** : Arsenal vs Wolves → λ_home = 3.67 (~3.7 buts attendus), λ_away = 0.76 (~0.8 buts)

### Étape 4 — Matrice de Poisson + Dixon-Coles

On calcule la probabilité de **chaque score possible** (0-0 jusqu'à 7-7) :

```
P(i, j) = [e^(-λh) × λh^i / i!] × [e^(-λa) × λa^j / j!] × DC(i, j)
```

La **correction Dixon-Coles** ajuste les scores faibles car la loi de Poisson seule les estime mal :

| Score | Correction DC | Raison |
|---|---|---|
| 0-0 | `1 - λh × λa × ρ` | Poisson sous-estime les 0-0 |
| 1-0 | `1 + λa × ρ` | Ajustement scores serrés |
| 0-1 | `1 + λh × ρ` | Ajustement scores serrés |
| 1-1 | `1 - ρ` | Poisson surestime les 1-1 |
| Autres | 1.0 | Pas de correction |

Le paramètre `ρ = -0.13` est calibré sur des données historiques.

### Étape 5 — Agrégation des probabilités

On somme la matrice 8×8 pour obtenir les probabilités des marchés :

```
P(Home)  = Σ P(i, j)  pour i > j    (victoire domicile)
P(Draw)  = Σ P(i, j)  pour i = j    (match nul)
P(Away)  = Σ P(i, j)  pour i < j    (victoire extérieur)

P(Over 2.5) = Σ P(i, j)  pour i + j > 2
P(BTTS)     = Σ P(i, j)  pour i ≥ 1 et j ≥ 1
```

**Exemple** : Arsenal vs Wolves → P(Home) = 87.5%, P(Draw) = 8.9%, P(Away) = 3.6%

### Étape 6 — Calibration

La dernière étape applique un **shrink vers 50%** pour éviter l'overconfidence du modèle Poisson :

```
P_final = 0.5 + (P_brut - 0.5) × CALIB
```

| Mode | CALIB | Raison |
|---|---|---|
| **Enrichi** (stats réelles) | **0.97** | Données fiables → quasi-brut |
| **Dégradé** (consensus) | **0.92** | Circulaire → réduction forte |

**Exemple** : Arsenal P_brut = 87.5% → P_final = 0.5 + (0.875 - 0.5) × 0.97 = **86.4%**

---

## 4 — Détection du Value Bet (Edge)

### Formule

```
Edge = P(modèle) - P(cote implicite)
     = P(modèle) - (1 / cote_bookmaker)
```

### Seuils de décision

| Edge | Statut | Action |
|---|---|---|
| **≥ 8%** | ✅ Value Bet | On parie |
| 4% à 8% | ⚠️ Signal faible | On ne parie pas (pas assez fiable) |
| < 0% | ❌ Pas de value | Le bookmaker a raison |

### Pourquoi 8% minimum ?

Un seuil trop bas (ex: 2%) génère beaucoup de **faux positifs** — des paris qui semblent avoir de la value mais qui sont en réalité du bruit statistique. Le seuil de 8% garantit que seuls les écarts significatifs passent le filtre, ce qui protège le capital à long terme.

### Exemple complet

| Match | Sélection | Cote | P(cote) | P(modèle) | Edge | Décision |
|---|---|---|---|---|---|---|
| Arsenal vs Wolves | Home | 1.28 | 78.1% | 86.4% | **+8.3%** | ✅ Value |
| Man City vs Everton | Home | 1.22 | 82.0% | 81.7% | **−0.3%** | ❌ Pas de value |
| Liverpool vs Fulham | Home | 1.35 | 74.1% | 81.7% | **+7.6%** | ⚠️ Juste sous le seuil |

---

## 5 — Staking — Gestion des mises

### Méthode : Critère de Kelly fractionnel

Le **critère de Kelly** calcule la mise optimale pour maximiser la croissance du capital à long terme :

```
Kelly% = (edge × cote - 1) / (cote - 1)
       = (P_modèle × cote - 1) / (cote - 1)
```

On utilise un **Kelly fractionnel** (25% du Kelly plein) pour réduire la variance :

```
Mise = min(Kelly% × 0.25, max_stake_pct) × bankroll
```

### Règles de gestion du risque

| Paramètre | Valeur | Rôle |
|---|---|---|
| `kelly_fraction` | 0.25 | 25% du Kelly optimal (réduction variance) |
| `max_stake_pct` | 1% | Mise max par pari (10€ sur 1000€) |
| `max_total_exposure` | 100% | Exposition totale max du capital |
| `max_bets` | 30 | Nombre max de paris par scan |
| `min_edge` | 8% | Edge minimum pour valider un pari |
| `max_odds` | 5.00 | Cote max autorisée (évite les outsiders extrêmes) |

### Mise à l'échelle proportionnelle

Si l'exposition totale dépasse le plafond, toutes les mises sont **réduites proportionnellement** (pas de coupure brutale) :

```
Si total_mises > max_exposure × bankroll :
    facteur = (max_exposure × bankroll) / total_mises
    chaque mise × facteur
```

Cela garantit que les meilleurs paris (plus gros edge) conservent leur part relative.

---

## 6 — Paramètres de configuration

### Fichier : `betx/config.py`

```python
# ── Seuils de détection ──
min_edge       = 0.08     # Edge minimum 8%
max_odds       = 5.00     # Cote max autorisée

# ── Staking ──
kelly_fraction = 0.25     # 25% du Kelly plein
max_stake_pct  = 0.01     # 1% max par pari
flat_pct       = 0.02     # 2% en mode flat (non utilisé)

# ── Modèle Football ──
home_advantage = 0.07     # +7% pour le domicile
xg_weight      = 0.50     # Poids des xG dans le lambda
goals_weight   = 0.50     # Poids des buts réels
elo_k_factor   = 20.0     # Vitesse d'adaptation de l'ELO

# ── Dixon-Coles ──
rho            = -0.13    # Correction scores faibles

# ── Calibration ──
CALIB_ENRICHED = 0.97     # Mode enrichi (stats réelles)
CALIB_DEGRADED = 0.92     # Mode dégradé (consensus)
```

### Ligues couvertes

| Ligue | ID API-Football | Saison |
|---|---|---|
| Premier League | 39 | 2024 |
| La Liga | 140 | 2024 |
| Serie A | 135 | 2024 |
| Bundesliga | 78 | 2024 |
| Ligue 1 | 61 | 2024 |
| Champions League | 2 | 2024 |
| Europa League | 3 | 2024 |

---

## 7 — Infrastructure & Automatisation

### Stack technique

| Composant | Technologie |
|---|---|
| Langage | Python 3.12 |
| Modèle | Poisson + Dixon-Coles + ELO (scipy, numpy) |
| Cotes | The Odds API v4 (500 req/mois) |
| Stats | API-Football v3 (100 req/jour) |
| Notifications | Telegram Bot API (DM + Channel) |
| Dashboard | Streamlit (port 8501, nginx reverse proxy) |
| VPS | Contabo Ubuntu 22.04 (213.199.41.168) |
| Scheduling | Cron (2 scans/jour) |

### Planning quotidien

| Heure | Action | Mode |
|---|---|---|
| **08h00** | `daily_scan.py --resend` | 🔁 Rappel — Renvoie les paris de la veille (pas d'appel API) |
| **15h00** | `daily_scan.py --notify` | 🔍 Scan réel — Récupère les cotes, enrichit, prédit, envoie les value bets |

### Flux du scan quotidien

```
15h00 — Cron déclenche daily_scan.py --notify
  │
  ├─ 1. The Odds API → récupère les cotes du jour (7 ligues)
  │
  ├─ 2. Parsing → extrait les matchs, cotes, consensus
  │
  ├─ 3. API-Football → enrichit avec stats réelles (cache 24h)
  │       └─ Fuzzy matching (60+ alias) pour lier les noms d'équipes
  │
  ├─ 4. Modèle → predict_football() pour chaque match
  │       ├─ Mode enrichi (stats dispo) → CALIB 0.97
  │       └─ Mode dégradé (pas de stats) → CALIB 0.92
  │
  ├─ 5. Value Engine → filtre edge ≥ 8%, cote ≤ 5.00
  │
  ├─ 6. Staking Engine → Kelly fractionnel, max 1% par pari
  │
  ├─ 7. Export → data/daily_bets.json (pour le dashboard)
  │
  └─ 8. Telegram → envoie le récap (DM + Channel)
          └─ 📊 Lien vers le Dashboard Live
```

### Notifications Telegram

Les résultats sont envoyés à :
- **DM personnel** : Notification directe
- **Channel betX** : Historique public

Chaque message contient :
- Date et nombre de value bets
- Tableau détaillé (match, sélection, cote, edge, mise)
- KPIs (exposition totale, edge moyen)
- Lien vers le Dashboard Live

---

## Résumé en une phrase

> **betX détecte les erreurs de pricing des bookmakers en comparant leurs cotes à un modèle Poisson indépendant alimenté par les vraies stats de la saison (buts, xG, ELO, forme), et ne parie que quand l'avantage statistique dépasse 8%.**

---

*Dernière mise à jour : 22 février 2026*
