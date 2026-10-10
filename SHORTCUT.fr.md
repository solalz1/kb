# Le bouton Partager (iPhone et Mac)

[English](SHORTCUT.md) · **Français**

On crée deux Raccourcis (plus un troisième, facultatif, pour les notes à la voix). iOS n'affiche dans la feuille de partage que celui qui correspond à ce que tu partages, donc tu verras toujours **une seule** option KB :

- **Ajouter à ma KB** : liens et texte (X, YouTube, Safari, TikTok, sélection de texte…).
- **Fichier vers ma KB** : PDF, captures, photos, mémos vocaux, vidéos, documents.

Il te faut l'adresse `https://kb.example.com/api/ingest` et ton `KB_API_TOKEN`. Les deux se copient depuis **Réglages** dans l'app.

Les deux Raccourcis se synchronisent via iCloud : crée-les une fois sur iPhone, ils apparaissent aussi dans le menu Partager du Mac.

## Le plus rapide : les générer (Mac, 2 minutes)

`shortcuts/build.py` écrit les trois Raccourcis ci-dessous sous forme de fichiers prêts à importer. iOS et macOS
n'importent que des Raccourcis **signés**, et seul un Mac sait les signer : lance-le sur ton Mac, depuis le dossier du
dépôt :

```bash
python3 shortcuts/build.py --url https://kb.example.com --sign
```

Double-clique ensuite chaque fichier de `shortcuts/dist/` : Raccourcis te demande ton `KB_API_TOKEN` (il va dans la
première action, « Texte », jamais dans les fichiers) et ajoute le Raccourci. Ils arrivent sur l'iPhone par iCloud. Ils
s'appellent `Add To KB`, `Fichier vers ma KB` et `Note perso` ; renomme-les comme tu veux. Sans Mac, construis-les à la
main ci-dessous.

---

## Raccourci 1 : « Ajouter à ma KB » (liens et texte)

App **Raccourcis** → **+** → nomme-le `Ajouter à ma KB`.

1. Touche **ⓘ (Détails)** → active **Afficher dans la feuille de partage**. Dans la première ligne du raccourci (« Recevoir … en entrée »), ne garde que **URL**, **Pages web Safari**, **Texte** et **Texte enrichi**. Pour « S'il n'y a pas d'entrée », choisis **Obtenir le presse-papiers**.
2. Ajoute **Obtenir les URL de l'entrée** (entrée : *Entrée du raccourci*).
3. Ajoute **Obtenir l'élément de la liste** → *Premier élément* de *URL*.
4. Ajoute **Obtenir le texte de l'entrée** (entrée : *Entrée du raccourci*).
5. Facultatif mais utile : **Demander une entrée** → type *Texte*, question `Pourquoi tu gardes ça ?`. Tu pourras valider sans rien écrire.
   Puis, pour choisir un dossier d'un tap, demande à la KB sa liste du moment (un dossier ajouté dans l'app y
   apparaît tout seul) : **Obtenir le contenu de l'URL** → `https://kb.example.com/api/folders/choices`, méthode
   **GET**, en-tête `Authorization` = `Bearer TON_KB_API_TOKEN` ; **Obtenir la valeur du dictionnaire** → clé
   `choices` ; **Choisir dans la liste** sur cette valeur (question `Où le ranger ?`). La liste propose
   `Automatique` (Claude choisit), tes dossiers, puis `Espace Perso` (tes notes de développement personnel).
6. Ajoute **Obtenir le contenu de l'URL** :
   - URL : `https://kb.example.com/api/ingest`
   - Touche **Afficher plus** :
     - Méthode : **POST**
     - En-têtes : `Authorization` = `Bearer TON_KB_API_TOKEN`
     - Corps de la requête : **JSON**, avec trois champs *Texte* :
       - `url` = *Élément de la liste* (étape 3)
       - `text` = *Texte* (étape 4)
       - `note` = *Entrée fournie* (étape 5), ou laisse vide
       - `folder` = *Élément choisi* (étape 5), si tu as ajouté la liste
7. Ajoute **Obtenir la valeur du dictionnaire** → clé `message` dans *Contenu de l'URL*.
8. Ajoute **Afficher la notification** → *Valeur du dictionnaire*.
9. Pour les sites qui refusent les serveurs (Medium…) : ajoute le champ `page_follows` = `1` au JSON de l'étape 6, puis
   **Obtenir la valeur du dictionnaire** → clé `page_wanted` dans *Contenu de l'URL*, et **Si** *cette valeur*
   **a une valeur quelconque** :
   - **Obtenir la valeur du dictionnaire** → clé `id` dans *Contenu de l'URL* ;
   - **Obtenir le contenu de l'URL** → *Élément de la liste* (étape 3), méthode **GET** (ton téléphone lit la page lui-même) ;
   - **Obtenir le contenu de l'URL** → `https://kb.example.com/api/items/` *id* `/page`, méthode **POST**, même
     en-tête `Authorization`, corps **Formulaire** avec un champ *Fichier* `page` = la page que tu viens d'obtenir.
   - **Fin du si**. L'API ne le demande que pour les sites qui la refusent : les autres partages n'attendent pas.

Test : dans l'app X, touche Partager sur un tweet → **Ajouter à ma KB** → « Ajouté à ta KB ✓ ».

## Raccourci 2 : « Fichier vers ma KB » (PDF, images, audio, vidéo)

1. **ⓘ** → **Afficher dans la feuille de partage**. Types reçus : **Images**, **PDF**, **Fichiers** et **Médias** uniquement.
2. Facultatif : **Demander une entrée** → `Pourquoi tu gardes ça ?`, puis le même choix de dossier que dans le Raccourci 1 (les trois actions qui finissent par **Choisir dans la liste**).
3. Ajoute **Répéter avec chaque élément** de *Entrée du raccourci*, et dans la boucle :
   - **Obtenir le contenu de l'URL** → `https://kb.example.com/api/ingest`, méthode **POST**, même en-tête `Authorization`.
     Corps de la requête : **Formulaire**, avec :
     - `file` → type **Fichier** = *Élément répété*
     - `note` → type **Texte** = *Entrée fournie*
     - `folder` → type **Texte** = *Élément choisi*
   - **Obtenir la valeur du dictionnaire** → clé `message` dans *Contenu de l'URL*.
4. Après la boucle : **Afficher la notification** → *Résultats de la répétition* : la réponse du serveur pour chaque fichier, erreurs comprises.

Test : dans Photos, partage une capture d'écran → **Fichier vers ma KB**.

Les photos HEIC de l'iPhone sont converties automatiquement. Une vidéo de plus de 50 Mo sera refusée (limite du stockage gratuit) : pour une longue vidéo, partage plutôt son lien.

## Raccourci 3 (facultatif) : « Note perso » (à la voix)

Pour noter une leçon, un principe ou une pensée en quelques secondes, avec Siri ou depuis l'écran d'accueil. La note va directement dans l'espace Perso, mot pour mot.

1. Nouveau Raccourci `Note perso` (pas besoin de l'afficher dans la feuille de partage).
2. Ajoute **Dicter le texte** (ou **Demander une entrée** de type *Texte* si tu préfères taper).
3. Facultatif : **Choisir dans le menu** avec les options `Principe`, `Valeur`, `Leçon`, `Objectif`, `Habitude`, `Réflexion`, `Journal`, `Citation`. Si tu sautes cette étape, Claude choisit la catégorie.
4. Ajoute **Obtenir le contenu de l'URL** :
   - URL : `https://kb.example.com/api/notes`
   - Méthode **POST**, même en-tête `Authorization` = `Bearer TON_KB_API_TOKEN`
   - Corps de la requête : **JSON**, champs *Texte* :
     - `content` = *Texte dicté*
     - `space` = `perso`
     - `category` = *Élément choisi* (étape 3), ou supprime ce champ
5. **Obtenir la valeur du dictionnaire** → clé `message`, puis **Afficher la notification**.

Test : « Dis Siri, Note perso », dicte « Je ne réponds plus aux mails après 20 h », choisis **Habitude**.

## Sur Mac

- Les deux Raccourcis apparaissent dans **Partager** (Safari, Finder, Aperçu…).
- Pour les avoir au clic droit dans le Finder : ouvre le Raccourci dans l'app Raccourcis du Mac → **ⓘ** → **Utiliser comme action rapide** → coche **Finder**.
- Dans n'importe quel navigateur, tu peux aussi créer un signet avec cette adresse :

  ```
  javascript:location.href='https://kb.example.com/add?url='+encodeURIComponent(location.href)
  ```

  Il ouvre la page **Ajouter** de l'app avec le lien déjà rempli.

## Astuces

- **Threads X** : partage le **dernier** tweet d'un thread. Tout ce qui précède est récupéré. Pour un thread de moins de 7 jours, le premier tweet suffit aussi.
- **Texte sélectionné** dans Safari : sélectionne le passage, puis Partager → **Ajouter à ma KB**. L'extrait est gardé avec la page.
- **Sites qui refusent les serveurs** (Medium…) : partage normalement, depuis l'app du site ou Safari. Le Raccourci
  fait lire la page par ton téléphone et l'envoie. Articles réservés aux membres : le texte complet ne vient que de
  Safari, connecté.
- **Idée au vol** : lance **Ajouter à ma KB** sans rien partager (depuis l'écran d'accueil ou Siri) : il prend le contenu du presse-papiers. Tu peux aussi dicter une note dans l'app.
- Pour aller plus vite, supprime l'étape « Pourquoi tu gardes ça ? ». Tu pourras ajouter la note plus tard dans la fiche.
- **Dossiers** : les Raccourcis générés demandent « Où le ranger ? » avec les dossiers de la KB tels qu'ils sont au
  moment du partage : un dossier créé dans l'app (**Dossiers**) est proposé la fois suivante, sans regénérer le
  Raccourci. `Automatique` laisse Claude choisir ; un élément rangé à la main reste dans son dossier.
- **Ranger dans Perso** : choisis `Espace Perso` dans « Où le ranger ? ». Sans cette liste, écris `#perso` dans la note « Pourquoi tu gardes ça ? » (ex. `#perso #ressource à relire avant mes objectifs 2027`). L'élément va dans l'espace Perso, et un hashtag de catégorie (`#principe`, `#valeur`, `#leçon`, `#objectif`, `#habitude`, `#réflexion`, `#journal`, `#citation`, `#ressource`) le range directement. Ces hashtags sont retirés de la note.
