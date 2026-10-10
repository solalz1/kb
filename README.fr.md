# KB : une base de connaissances personnelle

[English](README.md) · **Français**

Partage n'importe quoi depuis ton iPhone ou ton Mac (un tweet ou un thread, un article, une vidéo YouTube, un PDF, une capture d'écran, un mémo vocal…). Claude le lit, le résume, le tague et le relie à ce que tu as déjà sauvegardé. Ensuite tu le retrouves, tu lui poses des questions et tu reçois des conseils fondés sur tes propres notes. Tout tourne sur tes propres comptes : tu paies quelques dollars par mois d'API et d'hébergement, et personne d'autre ne voit tes données.

## Ce que ça fait

- **Capture de partout** : le bouton Partager sur iPhone et Mac (Raccourcis tout faits), l'app web, ou Claude lui-même.
- **Tous les formats** : posts et threads X, articles web, arXiv, GitHub, YouTube, TikTok/Instagram/Vimeo, podcasts, audio, vidéo, PDF (scannés aussi), images, Word/PowerPoint/Excel, notes.
- **Sources gardées** : chaque fiche garde le lien d'origine, l'auteur, la date et le fichier. Chaque réponse cite ses sources, avec les liens.
- **Dossiers** : tes propres rayons (ML, Entretien…, à ajouter et renommer dans l'app). Claude range chaque nouvel élément dans celui qui lui correspond ; choisis-le toi-même en partageant, le Raccourci propose toujours tes dossiers du moment.
- **Interroge ta KB** : « Qu'est-ce que j'ai sauvegardé sur… ? », ou « Je commence ce projet, qu'est-ce qui peut m'aider ? », qui renvoie une note sourcée.
- **Espace perso** : écris tes principes, tes valeurs, tes leçons, tes objectifs et un journal quotidien (avec un calendrier). Tes mots restent tels que tu les as écrits. Le **mode Conseil** répond à une décision à partir de *tes* principes, et les cite.
- **Digest tech du matin** (facultatif) : Hacker News, les papiers Hugging Face, les dépôts GitHub qui montent, des blogs et les personnes que tu suis sur X, classés selon ce que tu sauvegardes. Le lundi, la semaine en bref et des idées de projets.
- **Dans Claude** : un connecteur (MCP) permet à Claude de chercher et de lire ta KB sur le web, l'ordinateur, le téléphone et dans Claude Code.
- **Jamais enfermé** : une copie dans Notion tenue à jour (facultative), et un export Markdown complet (prêt pour Obsidian) avec tes fichiers d'origine.
- **Pensé pour le téléphone** : une app à installer, en français ou en anglais, avec des gestes pour épingler, archiver ou supprimer. Elle te montre ce que coûte chaque service, mois par mois.

## Ce qu'il te faut

| Service | Pour quoi | Obligatoire ? | Coût |
|---|---|---|---|
| [API Claude](https://platform.claude.com) | résumés, réponses, digest | oui | ~0,3 ¢ par élément, ~4 ¢ par question |
| [Voyage AI](https://dashboard.voyageai.com) | recherche (embeddings) | oui | gratuit jusqu'à 200 M de tokens |
| [Supabase](https://supabase.com) | base de données et fichiers | oui | l'offre gratuite suffit (25 $/mois avec sauvegardes) |
| [Railway](https://railway.com) | fait tourner l'app | oui | environ 5 $/mois |
| Compte développeur X | lire les tweets, les gens que tu suis | pour les liens X | 0,005 $ par post lu (prépayé) |
| [Groq](https://console.groq.com) (ou OpenAI) | transcrire l'audio et la vidéo | pour l'audio et la vidéo | quelques centimes par heure d'audio |
| Notion | copie de ta KB tenue à jour | non | gratuit |

Il faut aussi un compte GitHub (pour forker ce dépôt) et, pour les Raccourcis tout faits, un Mac. Un nom de domaine est facultatif : Railway te donne une adresse.

## Installer (environ une heure)

1. **Forke** ce dépôt (en haut à droite sur GitHub). Il ne contient que du code : ton fork peut être public ou privé.
2. **Supabase** : crée un projet, colle [`supabase/migrations/20261002000000_init.sql`](supabase/migrations/20261002000000_init.sql) dans le SQL Editor, puis lance-le.
3. **Clés** : crée les clés du tableau ci-dessus, plus deux secrets avec `openssl rand -hex 32` (le mot de passe de ton app, `KB_API_TOKEN`, et le secret du connecteur, `KB_MCP_SECRET`).
4. **Railway** : **New Project → Deploy from GitHub repo** → ton fork. Colle [`.env.example`](.env.example) rempli avec tes valeurs dans **Variables → Raw Editor**, puis **Settings → Networking → Generate Domain**.
5. **L'app** : ouvre cette adresse dans Safari sur ton iPhone → Partager → **Sur l'écran d'accueil**, puis colle ton `KB_API_TOKEN`.
6. **Le bouton Partager** : sur un Mac, dans un clone de ton fork, lance `python3 shortcuts/build.py --url https://ton-adresse --sign`, puis double-clique les fichiers qu'il écrit ([SHORTCUT.fr.md](SHORTCUT.fr.md)).

Chaque clic, les parties facultatives (domaine perso, connecteur Claude, Notion, digest, e-mail) et un tableau de dépannage sont dans **[SETUP.fr.md](SETUP.fr.md)**.

## L'essayer d'abord sur ton ordinateur (10 minutes)

Avec Docker installé, et seulement une clé Claude et une clé Voyage :

```bash
cp .env.example .env
# remplis KB_API_TOKEN, KB_MCP_SECRET (openssl rand -hex 32), ANTHROPIC_API_KEY et VOYAGE_API_KEY
docker compose up --build
```

Ouvre http://localhost:8000 et colle ton `KB_API_TOKEN`. La base tourne dans Docker et les fichiers restent sur ton disque. Les tweets demandent une clé X, l'audio et la vidéo une clé de transcription.

## Mettre à jour

Sur GitHub, ouvre ton fork et clique sur **Sync fork** : Railway redéploie tout seul. Le schéma de la base se met à jour au démarrage de l'app (`/api/health` affiche `"schema": "ok"`) : une mise à jour ne demande jamais d'étape SQL.

Si Railway ne redéploie pas, donne accès à ton fork à son app GitHub (GitHub → Settings → Applications → Railway).

## Ce que ça coûte

Avec un usage quotidien, compte 5 à 15 $ par mois : environ 5 $ de Railway, puis Claude. Claude, c'est environ un tiers de centime par élément sauvegardé (Claude Haiku 5.5), 0,3 à 20 centimes par question selon le modèle, et 0,10 à 0,20 $ par jour pour le digest. Ajoute X si tu sauvegardes des tweets. **Réglages → Coûts** montre ce que chaque service a coûté, ce mois-ci et en tout, en dollars US : synchronisé avec le service quand il a une API pour ça (le solde X, et le chiffre de la Console Claude avec une clé Admin facultative), estimé ailleurs à partir des appels de la KB ([SETUP.fr.md](SETUP.fr.md), étape 12). Les questions posées par le connecteur Claude passent par ton abonnement Claude, pas par les crédits API.

## Confidentialité

Le dépôt est public, tes données ne le sont pas.

- **Le code et les données sont séparés.** Tes éléments vivent dans ton projet Supabase (sécurité au niveau des lignes activée, aucun accès public), tes clés dans les variables Railway. L'app ne répond à rien sans ton jeton et demande aux moteurs de recherche de ne pas l'indexer.
- **Ce qui sort de ton serveur.** Pour traiter un élément, son contenu part vers l'API Anthropic (résumés, réponses ; les données de l'API ne servent pas à l'entraînement par défaut) et vers Voyage AI (recherche ; refuse l'entraînement dans son tableau de bord, voir [SETUP.fr.md](SETUP.fr.md)). L'audio et la vidéo partent chez ton service de transcription, les tweets sont lus par l'API X, et la copie Notion écrit dans ton propre espace. Le digest lit des sources publiques et envoie à Claude un résumé de tes centres d'intérêt.
- **Sauvegardes.** L'offre gratuite de Supabase n'en fait pas : voir [SETUP.fr.md](SETUP.fr.md), étape 9.

## Limites connues

- **Vieux threads X** : l'API X ne cherche que sur les 7 derniers jours. Partage le **dernier** post d'un thread plus ancien : tout ce qui précède est récupéré.
- **YouTube** bloque souvent les serveurs cloud. Si les transcriptions manquent, configure un proxy résidentiel (`YOUTUBE_PROXY_URL`).
- **Sites qui bloquent les serveurs** (Medium et certains journaux répondent 403 aux serveurs cloud) : le Raccourci fait lire la page par ton téléphone et l'envoie, tu partages donc comme d'habitude. Sinon l'app relit la page comme le ferait un navigateur, puis essaie Jina Reader et la Wayback Machine. Articles réservés aux membres : le texte complet vient d'un partage depuis **Safari**, connecté (le Raccourci joint le texte de la page).
- **LinkedIn, Instagram privé** : derrière une connexion. Partage plutôt une capture d'écran.
- **Fichiers de plus de 50 Mo** : la limite de l'offre gratuite de Supabase. Pour une longue vidéo, partage le lien.

## Comment c'est construit

```
iPhone / Mac ──Partager──▶ Raccourci ──POST /api/ingest───┐
App web (PWA à installer) ────────────────────────────────┤
Claude (connecteur MCP) ── /mcp/<secret> ─────────────────┤
                                                          ▼
                      ┌──────────── Railway : 1 conteneur ─────────────┐
                      │ FastAPI  ─  worker (threads)  ─  serveur MCP    │
                      │   extraction ▸ Claude Haiku ▸ morceaux ▸ Voyage │
                      │   ▸ liens auto  │  chat : Claude Sonnet         │
                      └───────────────────────┬─────────────────────────┘
                                              ▼
                 Supabase : Postgres + pgvector (recherche hybride) + Storage
                                              │
                        Notion (facultatif) : une page par élément, tenue à jour

Agent du digest (dans le worker, chaque matin) : HN · papiers HF · GitHub · blogs · X ──▶ Claude ──▶ app / e-mail / Claude
```

| Dossier | Contenu |
|---|---|
| `backend/app/` | API FastAPI, worker, un extracteur par source, enrichissement, chat, agent du digest, serveur MCP, export |
| `web/` | l'app React (fils, fiche, notes, journal, chat, digest, réglages) |
| `supabase/migrations/` | le schéma de la base, appliqué à chaque démarrage |
| `shortcuts/` | le générateur des Raccourcis iPhone et Mac |

Développer sans Docker :

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://localhost:8000
cd web && npm install && npm run dev     # http://localhost:5173, redirige /api vers le backend
# les tests demandent Postgres + pgvector, par exemple celui de docker compose (port 54322)
cd backend && KB_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/kb_test pytest
```

Le dépôt contient un `CLAUDE.md` et deux skills (`/debug-item`, `/add-source`) pour travailler dessus avec Claude Code.

## Licence

MIT, voir [LICENSE](LICENSE). Pour signaler une vulnérabilité, voir [SECURITY.md](SECURITY.md).
