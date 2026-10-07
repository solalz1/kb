# Mise en route

[English](SETUP.md) · **Français**

Compte environ une heure. À la fin, tu as l'app sur `https://kb.example.com` (iPhone et Mac), le bouton Partager, le connecteur dans Claude et, si tu le veux, une copie à jour dans Notion.

## 0. Ce qu'il te faut

- Ta propre copie du dépôt : **Fork** sur GitHub (public ou privé, il ne contient aucune donnée). Pour récupérer les mises à jour ensuite, **Sync fork** sur GitHub. Si tu gardes un `.env` en local, ne le commite jamais : il est dans `.gitignore`.
- Sur le Mac : `git`, et éventuellement Docker Desktop pour tester en local.
- `openssl rand -hex 32`, lancé deux fois : la première valeur sera `KB_API_TOKEN`, la seconde `KB_MCP_SECRET`. Garde-les dans ton gestionnaire de mots de passe.

## 1. Les clés API

| Service | Où | Variable | Remarque |
|---|---|---|---|
| Claude | [platform.claude.com](https://platform.claude.com) → API Keys | `ANTHROPIC_API_KEY` | Ajoute 10 à 20 $ de crédits. |
| Voyage AI | [dashboard.voyageai.com](https://dashboard.voyageai.com) → API Keys | `VOYAGE_API_KEY` | Ajoute un moyen de paiement pour lever les limites de débit. Puis **Organization → Terms of Service** : refuse l'usage de tes données pour l'entraînement (« opt out » ; il faut le moyen de paiement et les droits admin, et c'est définitif). |
| X | Console développeur X → crée une app → Keys and tokens | `X_BEARER_TOKEN` | Prépaie 10 $ de crédits (pay-per-use, 0,005 $ par post lu). |
| Groq | [console.groq.com](https://console.groq.com) → API Keys | `TRANSCRIPTION_API_KEY` | Pour l'audio et la vidéo. OpenAI marche aussi (voir `.env.example`). |
| Notion (facultatif) | voir l'étape 7 | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` | Copie de la KB dans Notion, tenue à jour. |

Fixe une limite de dépense mensuelle sur chaque console (Anthropic, Voyage, Groq). Côté X, les crédits prépayés servent déjà de plafond.

## 2. Supabase (base de données et fichiers)

1. Crée un projet sur [supabase.com](https://supabase.com), **région Paris (eu-west-3)**, avec un mot de passe de base solide.
2. **SQL Editor** → New query → colle tout le fichier `supabase/migrations/20261002000000_init.sql` → **Run**. Avec la CLI : `supabase link` puis `supabase db push`. Les mises à jour ne demandent rien de plus : l'app réapplique ce fichier à chaque démarrage (il peut être relancé sans risque), donc une nouvelle colonne est en place avant que le nouveau code s'en serve. `/api/health` affiche `"schema": "ok"` ; mets `AUTO_MIGRATE=false` pour le faire à la main.
3. Vérifie dans **Storage** que le bucket privé `kb-files` existe.
4. Bouton **Connect** → onglet **Session pooler** → copie l'URI dans `DATABASE_URL` (remplace `[YOUR-PASSWORD]`). Prends bien le *Session* pooler (port 5432), pas le *Transaction* pooler (6543).
5. **Project Settings → API Keys** : copie la clé *secret* (`sb_secret_…`, ou l'ancienne `service_role`) dans `SUPABASE_SERVICE_KEY`, et l'URL du projet dans `SUPABASE_URL`.

Sécurité : les tables ont le RLS activé et aucun accès pour les rôles publics. Rien n'est lisible via l'API REST publique de Supabase. Seul ton backend, connecté directement à Postgres, y accède.

À savoir : sur le plan gratuit, Supabase met en pause les projets inactifs pendant une semaine. Le worker interroge la base en continu, mais si tu constates une pause, le plan Pro l'évite.

## 3. Railway (l'API, le worker, le connecteur et l'app)

1. [railway.com](https://railway.com) → **New Project → Deploy from GitHub repo** → choisis ton fork. Le `Dockerfile` est détecté via `railway.json`. Si ton fork n'apparaît pas, ou si **Settings → Source** affiche plus tard *Auto deploy unavailable*, donne accès à ton fork à l'app GitHub de Railway (GitHub → Settings → Applications → Railway → Configure) : sans elle, Railway ne voit jamais les nouveaux commits.
2. Onglet **Variables → Raw Editor** : colle le contenu de `.env.example` rempli avec tes valeurs.
3. **Settings → Networking → Generate Domain**. Ouvre `https://<…>.up.railway.app/api/health` : la réponse attendue est `{"ok": true, …, "storage": "ok"}`. Toute autre valeur de `storage` (la réponse de Supabase) veut dire que les fichiers partagés échoueront : vérifie `SUPABASE_URL` (`https://<ref>.supabase.co`), `SUPABASE_SERVICE_KEY` (la clé **secrète** `sb_secret_…`, pas la clé publishable) et le bucket `kb-files`.
4. Domaine perso : **Custom Domain** → `kb.example.com`. Ajoute le **CNAME** `kb` → la cible indiquée par Railway, et le TXT de vérification demandé, **là où ton DNS est géré** : chez ton registrar (ex. Namecheap → Advanced DNS) ou, si les serveurs de noms de ton domaine pointent vers un autre hébergeur comme Netlify, dans les réglages DNS de cet hébergeur (Netlify → Domains → ton domaine → DNS settings → Add new record). Un enregistrement ajouté chez le registrar est ignoré quand les serveurs de noms pointent ailleurs.
5. Mets `PUBLIC_BASE_URL=https://kb.example.com` dans les variables. Railway redéploie tout seul.

Les logs du service (onglet **Deployments**) montrent chaque élément traité : `Traitement …` puis `Prêt … en 12.3s`.

## 4. Installer l'app

- **iPhone** : Safari → `https://kb.example.com` → Partager → **Sur l'écran d'accueil**. Ouvre l'app et colle `KB_API_TOKEN`.
- **Mac** : Safari → **Fichier → Ajouter au Dock**, ou Chrome → icône d'installation dans la barre d'adresse.

## 5. Le bouton Partager

Voir **[SHORTCUT.fr.md](SHORTCUT.fr.md)**.

## 6. Le connecteur dans Claude

1. Sur claude.ai : **Paramètres → Connecteurs → Ajouter un connecteur personnalisé**.
   - Nom : `KB`
   - URL : `https://kb.example.com/mcp/<KB_MCP_SECRET>`
2. Dans une conversation : **+ → Connecteurs → KB** (activé). Le connecteur est aussi disponible dans les apps Claude.
3. Essaie :
   - « Cherche dans ma KB ce que j'ai sauvé sur l'évaluation des agents. »
   - « Je démarre un agent qui trie mes emails : utilise find_for_project et fais-moi un plan. »
   - « Ajoute ce lien à ma KB : https://… »

**Claude Code** (même serveur, avec en-tête) :

```bash
claude mcp add --transport http kb https://kb.example.com/mcp --header "Authorization: Bearer <KB_MCP_SECRET>"
```

Ou, plus simple : dans ton `~/.zshrc`, ajoute `export KB_URL=https://kb.example.com` et `export KB_MCP_SECRET=…`. Le fichier `.mcp.json` du dépôt branche alors ta KB automatiquement quand tu ouvres Claude Code dans ce dossier.

Outils exposés : `search_kb`, `get_item`, `find_for_project`, `get_principles`, `get_digest`, `get_interests`, `add_to_kb`, `list_recent`, `get_related`, `list_actions`, `resurface`, `browse_kb`, `kb_overview`.

Pour un conseil dans Claude : « Utilise get_principles avec ma situation : on me propose un poste mieux payé mais avec beaucoup de déplacements. J'accepte ? » Claude lit tes principes et tes valeurs en entier, puis tes notes proches, et répond à partir d'eux.

Digest hebdomadaire sans rien coder : crée dans Claude une tâche planifiée « Chaque dimanche à 9 h, avec le connecteur KB, fais-moi un digest : ce que j'ai sauvé cette semaine (list_recent), 3 éléments à redécouvrir (resurface) et les actions en attente (list_actions) ».

L'URL du connecteur contient un secret : traite-la comme un mot de passe. Pour la révoquer, change `KB_MCP_SECRET` dans Railway.

## 7. Copie dans Notion (facultatif, conseillé)

Chaque élément a sa page dans une base Notion, réécrite à chaque modification : une deuxième copie de ta KB hors de Supabase, lisible partout, et un export déjà fait.

1. Dans Notion, crée une page vide, par exemple `KB` (elle reste privée).
2. [notion.so/profile/integrations](https://www.notion.so/profile/integrations) (la page s'appelle maintenant **Developer tools → Connections**) → **New connection** → nom `KB`, ton espace de travail, type **API token** (pas OAuth). Dans la connexion, onglet **Configuration** : copie l'**API token** (`ntn_…`) dans `NOTION_TOKEN`, et garde les capacités par défaut (lire, modifier, insérer du contenu). Prends une connexion plutôt qu'un jeton personnel (*personal access token*) : ceux-là expirent (au bout d'un an au plus) et voient tout ton espace de travail.
3. Donne ta page à la connexion : onglet **Content access** → **Edit access** → coche `KB`. Ou, dans la page, menu **•••** (en haut à droite) → **Connexions** → **+ Ajouter une connexion** → `KB`. Une connexion neuve ne voit rien avant ça ; ensuite, elle ne voit que cette page et ce qu'elle y crée.
4. Dans la page, **•••** → **Copier le lien**, et colle le lien dans `NOTION_PARENT_PAGE_ID` (le lien complet marche, l'identifiant de 32 caractères aussi).
5. Facultatif : `NOTION_SPACES=perso` pour ne copier que l'espace Perso.
6. Enregistre les variables : Railway redéploie. En moins d'une minute, une base **Knowledge base** apparaît dans la page, puis se remplit (environ 3 éléments par seconde). Dans l'app, **Réglages → Copie dans Notion** montre l'avancement, la dernière erreur s'il y en a une, et un bouton **Synchroniser maintenant**.

À savoir : la copie va dans un seul sens, de l'app vers Notion. Modifie dans l'app ; une retouche faite dans Notion est écrasée à la modification suivante de l'élément. Supprimer un élément dans l'app met sa page à la corbeille Notion. Si tu supprimes la base, elle est recréée et re-remplie. Dans Notion, ajoute des vues : par exemple un filtre `Espace` = `Perso`, groupé par `Catégorie`.

Langue : **Réglages → Copie dans Notion → Langue de la copie** (français ou anglais). En changer crée une nouvelle base avec ses colonnes, ses fiches et ses intitulés dans cette langue et y recopie tout ; l'ancienne base reste dans Notion jusqu'à ce que tu la supprimes. L'export Markdown a le même choix (**Langue de l'export**).

## 8. Le digest tech (agent de veille)

Chaque matin à `DIGEST_HOUR`, le worker lit Hacker News, les papiers du jour de Hugging Face, les dépôts GitHub qui décollent, une sélection de blogs de labs et d'ingénieurs, et les posts récents des personnes que tu suis sur X. Claude garde ce qui compte pour toi et écrit le digest, du plus général au plus technique. Le lundi, il écrit aussi la semaine en bref et 4 ou 5 projets pour la semaine.

1. Dans Railway, garde `DIGEST_ENABLED=true` (et ajuste `DIGEST_HOUR`, `DIGEST_TIMEZONE` si besoin).
2. Dans l'app, **Digest → Mes intérêts** :
   - écris quelques phrases sur ce que tu veux suivre et à quel niveau ;
   - ajoute les ingénieurs que tu aimes par leur compte X (leurs posts arrivent dans « Tes ingénieurs »), et des blogs par leur adresse (le flux RSS est trouvé tout seul) ;
   - relie ton compte X (**Relier**) : chaque personne que tu suis sur X à partir de là est ajoutée chaque matin, avant le digest. L'agent lit tes 5 derniers abonnements (0,05 $ de crédits X) et continue tant qu'ils sont nouveaux ; **Vérifier maintenant** vérifie tout de suite. **Importer ceux d'avant** ajoute les comptes que tu suivais déjà, une seule fois, à 0,01 $ par compte (le total s'affiche avant). Te désabonner de quelqu'un sur X ne le retire pas ici : mets-le en pause ou retire-le dans l'app ;
   - accepte ou ignore les ingénieurs que l'agent te suggère. Les personnes dont tu sauvegardes deux tweets sont suivies automatiquement.
3. **Digest → Générer maintenant** pour avoir le premier tout de suite (environ une minute). Ensuite, il arrive seul chaque matin.
4. Vote sur les éléments et les projets (pouces, **Garder**, **Je le fais**) : le profil est recalculé chaque lundi à partir de tes sauvegardes, de tes objectifs Perso et de ces votes. **Je le fais** enregistre le projet comme une note taguée `projet`.
5. Facultatif, par e-mail : remplis `SMTP_*` et `DIGEST_EMAIL_TO`. Avec Google Workspace ou Gmail : `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, ton adresse dans `SMTP_USER`, et un [mot de passe d'application](https://myaccount.google.com/apppasswords) (il faut la validation en deux étapes) dans `SMTP_PASSWORD`.

Coût : environ 0,10 à 0,20 $ d'API Claude par jour, plus 0,005 $ par post X lu (au plus `DIGEST_X_MAX_POSTS` par jour ; mets 0 pour ne pas lire X). Dans Claude, `get_digest` lit le dernier digest : tu peux en discuter avec ton abonnement.

## 9. Sauvegardes : ne rien perdre

- **Le plan gratuit de Supabase n'a aucune sauvegarde.** Si le projet est supprimé ou qu'une fausse manipulation vide une table, rien n'est récupérable. Dès que ton espace Perso compte pour toi, passe en **Pro** (25 $/mois) : sauvegarde quotidienne gardée 7 jours (la restauration à la minute près est une option payante).
- **Les sauvegardes de la base n'incluent pas les fichiers** (PDF, images, audio dans Storage). L'export **Avec les fichiers d'origine** des **Réglages**, si : télécharge-le de temps en temps et range-le dans iCloud Drive.
- **La copie Notion** (étape 7) est une deuxième copie continue du texte : tes notes en entier, les résumés, les sources.
- L'export simple (**Télécharger l'export**) est en Markdown : il s'ouvre dans Obsidian et s'importe dans Notion (**Importer → Texte et Markdown**).

## 10. Démarrer ton espace Perso

1. Dans l'app, **Perso → Nouvelle note → Principe**. Écris 5 à 10 principes, un par note, comme tu te les dirais (« Avant une grosse décision, j'attends 24 heures et j'en parle à quelqu'un de confiance. »). Puis tes **valeurs**. Épingle les plus importants : ils passent en premier dans les conseils.
2. Ensuite, au fil de l'eau : leçons, objectifs, habitudes, journal, citations. Sans catégorie, Claude en choisit une.
3. Depuis le bouton Partager, ajoute `#perso` dans ta note (et une catégorie : `#leçon`, `#principe`, `#objectif`…) pour ranger un lien, une vidéo ou un livre dans Perso. Tout élément peut aussi passer de Veille à Perso depuis sa fiche.
4. **Demander → Conseil** : décris la situation ou la décision. La réponse cite tes notes ; touche un numéro pour voir laquelle.

## 11. Vérifier que tout marche

1. Partage un tweet avec le Raccourci : une notification « Ajouté à ta KB ✓ » apparaît.
2. Dans l'app, la fiche passe de « Lecture et résumé en cours… » à une fiche complète en 10 à 60 secondes.
3. Pose une question dans **Demander** : la réponse cite `[1]`, et la source renvoie vers le tweet original.
4. Écris un principe dans Perso, puis pose une question en mode **Conseil** : le principe apparaît comme source `[1]`.
5. Si Notion est configuré : l'élément apparaît dans la base Notion en moins d'une minute.
6. **Digest → Générer maintenant** : un digest apparaît en une à deux minutes, avec le lien de chaque source.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Le Raccourci affiche une erreur 401 | Jeton différent de `KB_API_TOKEN`. |
| Fiche en erreur « X_BEARER_TOKEN invalide » ou « Crédits X API épuisés (402) » | Clé X erronée ou crédits à recharger. Puis **Retraiter**. |
| Vidéo YouTube sans transcription | YouTube bloque l'IP de Railway : renseigne `YOUTUBE_PROXY_URL` (proxy résidentiel). |
| « Fichier trop volumineux » | Plus de `MAX_UPLOAD_MB` : partage le lien plutôt que le fichier. |
| LinkedIn ou Instagram : fiche presque vide | Contenu derrière un login : partage une capture d'écran. |
| Le connecteur Claude ne répond pas | Teste : `curl -X POST https://kb.example.com/mcp/<secret> -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` |
| Recherche vide alors que la fiche existe | La fiche n'est pas encore « prête », ou `VOYAGE_API_KEY` manque (voir les logs). |
| Notion : « page parente introuvable » | La page n'est pas connectée à l'intégration (étape 7.2), ou `NOTION_PARENT_PAGE_ID` pointe vers une autre page. |
| Pas de digest le matin | `DIGEST_ENABLED=true` ? Les logs affichent `Digest activé` au démarrage et `Digest … prêt` chaque matin. **Régénérer** affiche l'erreur s'il y en a une. |
| « Tes ingénieurs » reste vide | Il faut un compte X pour chaque personne suivie, `X_BEARER_TOKEN`, `DIGEST_X_MAX_POSTS` > 0 et des crédits X. |
| Notion : rien n'apparaît | Regarde **Réglages → Copie dans Notion** : la variable manquante ou la dernière erreur y est indiquée. |

Pour reconstruire une fiche après un changement de réglage : bouton **Retraiter** dans la fiche.
