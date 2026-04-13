# Firebase Cost-Zero Strategy (Spark / Free Tier)

Objectif: utiliser Firebase sans facturation.

## Principes

- Ne pas stocker les gros historiques sur Firebase: conserver l'historique complet sur le VPS (`SQLite + exports CSV/JSON`).
- Utiliser Firebase uniquement pour un "snapshot léger" dashboard (quelques documents).
- Eviter les Cloud Functions en production: préférer le cron VPS déjà en place.
- Toujours rester sur le plan `Spark` (pas de passage Blaze).

## Repartition recommandée

- VPS (source de vérité):
  - `data/betx.db`
  - historique pronostics externes
  - classement détaillé
- Firebase (optionnel, léger):
  - collection `public_dashboard`
  - 1 document leaderboard global
  - 1 document recommandations du jour
  - TTL 7 jours pour éviter l'accumulation

## Firestore Cost Guardrails

- Limiter le nombre de lectures depuis le front:
  - 1 lecture leaderboard
  - 1 lecture recommandations
- Pas de requêtes non indexées ni scans complets.
- Pas de sous-collections volumineuses.
- Document unique versionné (`public_dashboard/current`).

## Cloud Functions Cost Guardrails

- Recommandation: `0 function` (désactivé) et utiliser cron VPS.
- Si une function est obligatoire:
  - region unique
  - `minInstances = 0`
  - timeout court
  - pas de trigger Firestore en boucle
  - batch unique quotidien

## Monitoring

- Configurer des alertes de quota sur Firebase Console.
- Vérifier chaque semaine:
  - Read count
  - Storage size
  - Invocations functions

## Règle simple

Si un besoin augmente les coûts Firebase, on le déporte sur VPS en priorité.
