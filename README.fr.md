# KB : une knowledge base personnelle

[English](README.md) · **Français**

Tu partages n'importe quoi depuis ton iPhone ou ton Mac (tweet, thread, article, vidéo YouTube ou TikTok, PDF, capture d'écran, note vocale, document Word…). Claude le lit, le résume, le tague et le relie à ce que tu as déjà sauvé. Ensuite tu peux le retrouver, l'interroger et t'en servir pour démarrer un projet.

- **Capture** : bouton Partager d'iOS/macOS (Raccourci), app web, ou directement depuis Claude.
- **Tous les formats** : X (API officielle, threads compris), articles, arXiv, GitHub, YouTube, TikTok/Instagram/Vimeo/podcasts, audio, vidéo, PDF (même scannés), images, Word/PowerPoint/Excel, notes.
- **Sources toujours visibles** : chaque fiche garde l'URL d'origine (ex. `https://x.com/handle/status/…`), l'auteur, la date et le fichier original. Chaque réponse du chat cite ses sources `[1]`, avec un lien.
- **Chat + mode projet** : « Qu'est-ce que ma KB dit de… ? » et « Je lance tel projet, qu'est-ce qui peut me servir ? », qui donne un dossier sourcé avec ce qui est utile, qui suivre et les angles morts.
- **Espace Perso** : un département à part pour le développement personnel. Tu y écris à la main tes principes, tes valeurs, tes leçons, tes objectifs, tes habitudes et ton journal (ou tu y ranges n'importe quel partage avec `#perso`). Tes notes sont gardées mot pour mot ; Claude ajoute seulement un résumé, des tags et des liens.
- **Mode Conseil** : « Est-ce que j'accepte ce poste ? » La réponse s'appuie sur *tes* principes et *tes* valeurs (toujours relus en entier), puis sur tes leçons et tes notes. Elle cite chacun, nomme les tensions entre eux et se termine par un prochain pas. Aussi dans Claude, via le connecteur (`get_principles`).
- **Agent de veille tech** : chaque matin, un digest des dernières 24 h rangé du plus général au plus technique (Hacker News, papiers Hugging Face, dépôts GitHub qui montent, blogs des labs et des ingénieurs, les personnes que tu suis sur X), ajusté à ce que tu sauvegardes. Chaque lundi, la semaine en bref et 4 ou 5 projets à mener dans la semaine, chacun avec un plan et un livrable. Il apprend de tes sauvegardes, de tes objectifs Perso et de tes votes, et te suggère des ingénieurs à suivre. À lire dans l'app, par e-mail ou dans Claude (`get_digest`).
- **Ne rien perdre** : copie automatique dans Notion en option (une page par élément, tenue à jour), et export Markdown avec les fichiers d'origine, qui s'ouvre dans Obsidian et s'importe dans Notion.
- **Connecteur Claude (MCP)** : ta KB est disponible dans Claude (web, desktop, mobile) et dans Claude Code. Claude y cherche, la parcourt par type, tag ou personne, et lit les éléments en entier avant de répondre. Un bouton « Demander dans Claude » dans l'app y ouvre la question : elle passe alors par ton abonnement Claude, pas par les crédits API.
- **En plus** : liens automatiques entre éléments (avec la raison du lien), détection de doublons, « à redécouvrir », actions extraites (outils à tester, papiers à lire), pages par personne/outil/concept, export Markdown compatible Obsidian.

## Architecture

```
iPhone / Mac ──Partager──▶ Raccourci ──POST /api/ingest──┐
App PWA (kb.example.com) ────────────────────────────────────┤
Claude (connecteur MCP) ── /mcp/<secret> ────────────────┤
                                                         ▼
                      ┌──────────── Railway : 1 conteneur ────────────┐
                      │ FastAPI  ─  worker (threads)  ─  serveur MCP   │
                      │   extraction ▸ Claude Haiku ▸ chunks ▸ Voyage  │
                      │   ▸ liens auto  │  chat RAG : Claude Sonnet    │
                      └───────────────────────┬────────────────────────┘
                                              ▼
                    Supabase : Postgres + pgvector (recherche hybride) + Storage
                                              │
                    Notion (facultatif) : une page par élément, tenue à jour

Agent de veille (dans le worker, chaque matin) : HN · papiers HF · GitHub · blogs · X ──▶ Claude ──▶ app / e-mail / Claude
```

| Dossier | Contenu |
|---|---|
| `supabase/migrations/` | Schéma : items, chunks (pgvector + plein texte FR/EN), liens, actions, recherche hybride RRF, file d'attente |
| `backend/app/` | API FastAPI, worker, extracteurs par format, enrichissement, chat, serveur MCP, export |
| `web/` | PWA React (Veille et Perso, fiche, éditeur de notes, chat, ajout, à faire, réglages), installable sur iPhone et Mac |
| `SETUP.fr.md` | Mise en route pas à pas (comptes, Supabase, Railway, domaine, connecteur Claude) |
| `SHORTCUT.fr.md` | Créer le Raccourci du bouton Partager |

## Confidentialité

Le dépôt est public, tes données non.

- **Code et données séparés.** Le dépôt ne contient que du code : ni domaine, ni identifiant, ni clé, ni élément. Tes éléments vivent dans ton propre projet Supabase (RLS activé, aucun accès public), tes clés dans les variables d'environnement de Railway, et l'app ne répond à rien sans ton jeton. Elle demande aussi aux moteurs de recherche de ne pas l'indexer.
- **Ce qui sort de ton serveur, et pourquoi.** Pour traiter un élément, son contenu part vers l'API Anthropic (résumé, tags, réponses du chat ; les données de l'API ne servent pas à entraîner les modèles par défaut) et vers Voyage AI (embeddings pour la recherche ; désactive l'usage pour l'entraînement dans le tableau de bord Voyage, voir [SETUP.fr.md](SETUP.fr.md)). L'audio et la vidéo passent par ton service de transcription (Groq ou OpenAI), les tweets sont lus via l'API X, et la copie Notion, si tu l'actives, écrit dans ton propre espace Notion. L'agent de veille ne lit que des sources publiques ; pour les trier, il envoie à Claude un résumé de tes intérêts (tags principaux, personnes, objectifs Perso). Les notes Perso suivent le même chemin, et rien d'autre.
- **Sauvegardes.** Le plan gratuit de Supabase n'a aucune sauvegarde. Voir [SETUP.fr.md](SETUP.fr.md), étape 9 : copie Notion, export complet et Supabase Pro.

## Démarrage rapide

Suis **[SETUP.fr.md](SETUP.fr.md)** (environ une heure), puis **[SHORTCUT.fr.md](SHORTCUT.fr.md)**.

En local, avec Docker : `cp .env.example .env`, remplis les clés, puis `docker compose up --build` et ouvre http://localhost:8000.

Pour développer sans Docker :

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://localhost:8000
# front (autre terminal) : relaie /api vers le backend
cd web && npm install && npm run dev     # http://localhost:5173
# tests : il faut un Postgres + pgvector, par exemple celui de docker compose (port 54322)
cd backend && KB_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/kb_test pytest
```

## Coûts indicatifs (usage perso intensif)

| Poste | Ordre de grandeur |
|---|---|
| Enrichissement d'un élément (Claude Haiku 4.5) | ~1 centime, plus pour un long PDF |
| Question dans l'app | ~2 centimes (Haiku) à ~20 centimes (Fable) ; ~4 centimes avec Sonnet 5.5, le modèle par défaut. Le modèle se choisit dans le chat |
| Question via le connecteur dans Claude | incluse dans ton abonnement Claude |
| Lecture d'un tweet (API X) | 0,005 $ par post lu |
| Embeddings (Voyage) | négligeable |
| Transcription (Groq Whisper) | quelques centimes par heure d'audio |
| Railway | ~5 $/mois |
| Supabase | 0 $ (gratuit) ou 25 $/mois (Pro, avec sauvegardes quotidiennes) |
| Copie Notion | gratuite (fonctionne avec le plan Notion gratuit) |
| Digest tech | ~0,10 à 0,20 $/jour d'API Claude, plus 0,005 $ par post X lu (plafonné par `DIGEST_X_MAX_POSTS`) |

## Limites connues

- **Threads X anciens** : l'API ne permet de chercher la suite d'un thread que sur les 7 derniers jours. Pour un vieux thread, partage son **dernier** tweet : tout ce qui précède est récupéré.
- **YouTube** bloque souvent les IP des serveurs cloud. Si les transcriptions manquent, ajoute un proxy résidentiel (`YOUTUBE_PROXY_URL`).
- **LinkedIn, Instagram (posts privés)** : contenu derrière un login. Partage plutôt une capture d'écran.
- **Fichiers > 50 Mo** : limite du plan gratuit Supabase. Pour une longue vidéo, partage plutôt le lien.

## Licence

MIT, voir [LICENSE](LICENSE). Les dépendances sont sous licences permissives (MIT, BSD, Apache-2.0, LGPL, Unlicense). Pour signaler une faille : [SECURITY.md](SECURITY.md).
