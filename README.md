# EcoleDirecte ↔︎ Poke MCP Server

Un serveur [FastMCP](https://github.com/jlowin/fastmcp) spécialisé qui :

- se connecte à l'API EcoleDirecte (notes, absences/vie scolaire, messagerie, emploi du temps, timeline),
- gère automatiquement le QCM de double authentification (avec outils MCP pour répondre),
- surveille les mises à jour toutes les `POLL_INTERVAL_SECONDS` secondes et envoie des notifications vers Poke via l'API inbound SMS,
- expose des outils MCP (`get_status`, `respond_qcm`, `list_notes`, etc.) que Poke peut invoquer à la demande.

La logique réseau reprend la doc communautaire [EduWireApps/ecoledirecte-api-docs](https://github.com/EduWireApps/ecoledirecte-api-docs) et le guide MCP de Poke [interaction.co/mcp](https://interaction.co/mcp).

## 1. Prérequis

- Python **3.11+** (la lib `fastmcp` ne s'installe pas sous Python 3.9)
- Un compte EcoleDirecte élève + accès à la messagerie
- Un token API Poke (`https://poke.com/settings/advanced`)
- (Optionnel) un espace Render/railway/autre pour héberger le serveur MCP

## 2. Installation locale

```bash
git clone <ce-repo>
cd poke-ecole-mcp
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configuration

1. Copier le template :

   ```bash
   cp .env.example .env
   ```

2. Renseigner les variables :

   | Variable | Description |
   | --- | --- |
   | `ECOLE_DIRECTE_USERNAME` / `ECOLE_DIRECTE_PASSWORD` | Identifiants élèves |
   | `ECOLE_DIRECTE_USER_AGENT` | Doit rester stable pour un token donné (comme sur ED) |
   | `POKE_API_KEY` | Token Bearer inbound-SMS |
   | `POLL_INTERVAL_SECONDS` | Fréquence de rafraîchissement (par défaut 5 min) |
   | `SCHEDULE_WINDOW_DAYS` | Fenêtre de récupération de l'EDT |
   | `TIMELINE_WINDOW_DAYS` | Fenêtre d'historique pour détecter les events timeline |
   | `ECOLE_DIRECTE_ACCOUNT_INDEX` | Index du compte enfant à suivre si plusieurs élèves |

   Un état persistant (`data/state.json`) conserve le dernier token ED, les identifiants QCM `cn/cv`, et les snapshots utilisés pour détecter les nouvelles entrées.

## 4. Démarrage & test

```bash
source .venv/bin/activate
python src/server.py
```

Le serveur MCP écoute par défaut sur `http://localhost:8000/mcp` (transport HTTP streamable).

Pour le tester :
1. `source .venv/bin/activate`
2. `python src/server.py`
3. Dans un autre terminal : `npx @modelcontextprotocol/inspector`
4. Dans l'inspector, se connecter à `http://localhost:8000/mcp`

## 5. Connexion à Poke

1. Aller sur [`https://poke.com/settings/connections`](https://poke.com/settings/connections) puis “Add MCP”.
2. Renseigner l’URL publique de votre serveur (Render, Railway, etc.) **avec le suffixe `/mcp`**.
3. Une fois la connexion active, demandez à Poke :  
   `Utilise l'intégration "<nom>" et la tool "get_status"`.

Le serveur push des messages vers Poke grâce à l’API inbound :

```
POST https://poke.com/api/v1/inbound-sms/webhook
Authorization: Bearer <POKE_API_KEY>
```

## 6. Outils MCP

| Tool | Description |
| --- | --- |
| `get_status` | Donne l’état de la connexion, les derniers timestamps de sync et le QCM en attente |
| `respond_qcm(answer)` | Valide la double authentification (answer = index ou texte), relance une sync immédiate |
| `sync_now` | Force une synchronisation complète et retourne toutes les nouveautés détectées |
| `list_notes(limit=5)` | Dernières notes avec matière, note, prof et dates |
| `list_messages(limit=5)` | Résumé des derniers messages de la messagerie |
| `list_absences()` | Absences/retards + sanctions tirés de `viescolaire.awp` |
| `list_schedule(days=7, include_cancelled=True)` | Emploi du temps sur la période demandée |

## 7. Gestion du QCM

- Lors d’un nouvel appareil, ED répond `code=250`. Le serveur :
  1. récupère la question/réponses (`/v3/connexion/doubleauth.awp`),
  2. stocke l’état dans `state.json`,
  3. notifie automatiquement via Poke (`type=qcm`).
- Utiliser `respond_qcm` pour renvoyer l’index ou le texte du choix. Les identifiants `cn/cv` sont alors persistés et réutilisés automatiquement lors des futurs logins.

## 8. Notifications automatiques

Le `UpdatePoller` tourne en tâche de fond :

- nouvelles notes (`notes.awp`),
- nouveaux messages (messagerie v3),
- nouvelles absences / retards (`viescolaire.awp`),
- cours annulés (flag `isAnnule` sur l’EDT),
- timeline globale (permet de couvrir les post-its & annonces combinées).

Chaque mise à jour génère un push structuré vers Poke (type, identifiant, résumé).

## 9. Déploiement

- Le dépôt contient un `render.yaml` minimal pour Render.  
- Prévoir les variables d’environnement ci-dessus + un volume persistant pour `data/state.json` si vous souhaitez conserver l’historique entre redéploiements.

---

Si vous avez besoin d’autres modules ED (cahier de texte, documents, etc.) ou d’intégrer d’autres transports MCP, ouvrez simplement une issue ou ajoutez vos propres tools `@mcp.tool` dans `src/server.py`.
