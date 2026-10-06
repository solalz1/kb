// English for the feeds (Veille and Perso), the item cards, the item page, the note editor and the To do page.
// Keys are the French strings exactly as written in the code.
export default {
  // item cards (Fiche, Embed)
  "Nouvel élément": "New item",
  "Sans titre": "Untitled",
  "Épinglé": "Pinned",
  "Rangement et résumé en cours…": "Filing and summarizing…",
  "Lecture et résumé en cours…": "Reading and summarizing…",
  "Échec du traitement : ouvre la fiche pour réessayer": "Processing failed: open the card to retry",
  "Vidéo YouTube": "YouTube video",

  // feeds (Veille and Perso)
  "Tes principes, tes valeurs, tes leçons et tes objectifs. Le mode Conseil s'appuie dessus pour t'aider à décider.":
    "Your principles, values, lessons and goals. Advice mode draws on them to help you decide.",
  "Nouvelle note": "New note",
  "Nouvelle note · {category}": "New note · {category}",
  "Demander conseil": "Ask for advice",
  "Chercher dans tes notes perso": "Search your personal notes",
  "Chercher dans ta veille": "Search your feed",
  "Chercher une leçon, un principe, un souvenir…": "Search for a lesson, a principle, a memory…",
  "Chercher un sujet, une idée, une personne…": "Search for a topic, an idea, a person…",
  "Filtrer par catégorie": "Filter by category",
  "Filtrer par type": "Filter by type",
  "Retirer le filtre": "Remove filter",
  "Écris d'abord quelques {principles} et {values} : le mode Conseil les relit en entier à chaque question.":
    "Start by writing a few {principles} and {values}: Advice mode reads them in full on every question.",
  "principes": "principles",
  "valeurs": "values",
  "Écrit il y a un moment, toujours vrai ?": "Written a while ago, still true?",
  "Sauvé il y a un moment, toujours d'actualité": "Saved a while ago, still relevant",
  "Ton espace perso est vide": "Your personal space is empty",
  ["Commence par tes principes et tes valeurs : ce sont eux que le mode Conseil relit en entier pour t'aider à décider. "
    + "Ensuite, note tes leçons, tes objectifs, ton journal."]:
    "Start with your principles and values: they're what Advice mode reads in full to help you decide. "
    + "Then write down your lessons, your goals, your journal.",
  "Écrire un principe": "Write a principle",
  "Écrire une valeur": "Write a value",
  "Depuis le bouton Partager, ajoute {tag} à ta note pour ranger un lien ici.":
    "From the Share button, add {tag} to your note to file a link here.",
  "Ta KB est vide pour l'instant": "Your KB is empty for now",
  "Partage un tweet, un article ou un PDF avec le Raccourci « Ajouter à ma KB », ou colle un lien ici.":
    "Share a tweet, an article or a PDF with the \"Add to my KB\" Shortcut, or paste a link here.",
  "Ajouter un premier élément": "Add your first item",
  "Rien ne correspond. Essaie d'autres mots ou retire un filtre.": "Nothing matches. Try other words or remove a filter.",
  "{n} résultats les plus proches": "{n} closest results",
  "{n} résultat le plus proche": "{n} closest result",
  "{n} éléments": "{n} items",
  "{n} élément": "{n} item",
  "Afficher plus": "Show more",
  "Explorer": "Explore",
  "À redécouvrir": "Rediscover",
  "Tags": "Tags",
  "Personnes et idées": "People and ideas",
  "Personnes, outils, concepts": "People, tools, concepts",

  // item page
  "Chargement…": "Loading…",
  "Retour": "Back",
  "Supprimer définitivement cet élément de ta KB ?": "Permanently delete this item from your KB?",
  "publié le {date}": "published on {date}",
  "écrit le {date}": "written on {date}",
  "sauvé le {date}": "saved on {date}",
  "{n} pages": "{n} pages",
  "{n} page": "{n} page",
  "Lecture, résumé et indexation en cours…": "Reading, summarizing and indexing…",
  "Réessayer": "Try again",
  "Modifier": "Edit",
  "Pourquoi tu gardes ça ? (ça aide à le retrouver plus tard)": "Why are you keeping this? (it helps you find it later)",
  "Enregistrer la note": "Save note",
  "Modifier la note": "Edit note",
  "Fichier original": "Original file",
  "Poser une question": "Ask a question",
  ["Avec le connecteur KB, lis l'élément {id} de ma knowledge base (get_item, contenu complet) "
    + "ainsi que ses éléments liés (get_related), puis aide-moi à creuser « {title} ». "
    + "Commence par me dire en trois points ce qu'il faut en retenir."]:
    "With the KB connector, read item {id} from my knowledge base (get_item, full content) "
    + "along with its related items (get_related), then help me dig into \"{title}\". "
    + "Start by telling me the three things to take away from it.",
  "cet élément": "this item",
  "Creuser dans Claude": "Dig deeper in Claude",
  "Aucune": "None",
  ["Ce tweet ouvre peut-être un thread plus long. Pour un thread de plus de 7 jours, partage son dernier tweet : "
    + "tout ce qui précède sera récupéré."]:
    "This tweet may start a longer thread. For a thread older than 7 days, share its last tweet: "
    + "everything before it will be fetched.",
  "En bref": "In short",
  "Points clés": "Key points",
  "Utile pour": "Useful for",
  "Liés dans ta KB": "Related in your KB",
  "Tweets du thread": "Tweets in the thread",
  "Tweet {i} sur {n}": "Tweet {i} of {n}",
  "Retirer {tag}": "Remove {tag}",
  "Ajouter un tag": "Add a tag",
  "Contenu complet": "Full content",
  "Contenu complet ({n} k caractères)": "Full content ({n}k characters)",
  "Épingler": "Pin",
  "Désépingler": "Unpin",
  "Archiver": "Archive",
  "Désarchiver": "Unarchive",
  "Retraiter": "Reprocess",
  "Supprimer": "Delete",

  // extraction hints written by the server (metadata.hint, shown with tServer)
  "Contenu derrière un login : partage plutôt une capture d'écran.": "Content behind a login: share a screenshot instead.",
  ["Transcription introuvable : YouTube bloque probablement l'IP du serveur. "
    + "Configure YOUTUBE_PROXY_URL (voir SETUP.md)."]:
    "No transcript found: YouTube is probably blocking the server's IP. Set YOUTUBE_PROXY_URL (see SETUP.md).",

  // note editor
  ["Seules les notes écrites se modifient ici. Pour un lien ou un fichier, change le titre, la note ou les tags "
    + "depuis sa fiche."]:
    "Only written notes can be edited here. For a link or a file, change the title, note or tags from its card.",
  "Écris quelque chose avant d'enregistrer.": "Write something before saving.",
  "Effacer ce brouillon ?": "Delete this draft?",
  "Annuler": "Cancel",
  "Abandonner": "Discard",
  "Brouillon restauré.": "Draft restored.",
  "Repartir d'une page blanche": "Start from a blank page",
  "privée": "private",
  "Sans catégorie, Claude en choisit une à l'enregistrement.": "Without a category, Claude picks one when you save.",
  "Titre": "Title",
  "Titre (facultatif, sinon Claude en propose un)": "Title (optional, otherwise Claude suggests one)",
  "Texte": "Text",
  "Écris librement : une idée, une leçon, un objectif, une réflexion…": "Write freely: an idea, a lesson, a goal, a reflection…",
  "Une idée, un compte rendu, une citation…": "An idea, meeting notes, a quote…",
  ["Tes {category} font partie de ta charte : le mode Conseil les relit en entier à chaque question. "
    + "Écris-les comme tu te les dirais."]:
    "Your {category} are part of your charter: Advice mode reads them in full on every question. "
    + "Write them the way you'd say them to yourself.",
  "(facultatif, séparés par des virgules)": "(optional, comma-separated)",
  "famille, travail, santé": "family, work, health",
  "Enregistrer": "Save",
  "Résumé, tags et liens sont refaits après l'enregistrement.": "The summary, tags and links are redone after you save.",
  "Ton texte est gardé tel quel ; Claude ajoute résumé, tags et liens.": "Your text is kept as is; Claude adds a summary, tags and links.",

  // To do
  "À tester": "To try",
  "À lire": "To read",
  "À regarder": "To watch",
  "À suivre": "To follow",
  "À acheter": "To buy",
  "Ce que tes sauvegardes suggèrent de faire : outils à tester, papiers à lire, comptes à suivre.":
    "What your saves suggest doing: tools to try, papers to read, accounts to follow.",
  "Afficher aussi ce qui est fait": "Also show what's done",
  "Rien en attente. Les actions apparaissent ici quand un élément sauvegardé en suggère.":
    "Nothing pending. Actions show up here when a saved item suggests some.",
  "Autres": "Other",
  "Source": "Source",
} as Record<string, string>;
