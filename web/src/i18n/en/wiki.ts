// English for the wiki (pages written by Claude), its question mode, its review and its Obsidian copy.
// Keys are the French strings exactly as written in the code (and the server's status messages).
export default {
  // navigation and sections
  "Wiki": "Wiki",
  "Personnel": "Personal",
  "Lectures": "Reading",
  "Business": "Business",
  "Concurrence": "Competitive analysis",
  "Toi : objectifs, principes, leçons, habitudes, journal": "You: goals, principles, lessons, habits, journal",
  "Les sujets que tu creuses sur la durée, avec une thèse qui évolue": "The topics you dig into over time, with a thesis that evolves",
  "Une page par livre, et ses grands thèmes": "A page per book, and its main themes",
  "Tes activités : clients, offres, prospection, méthodes": "Your business: clients, offers, prospecting, methods",
  "Les entreprises, produits et marchés que tu suis": "The companies, products and markets you follow",
  "Thème": "Theme",
  "Entité": "Entity",
  "Livre": "Book",
  "Synthèse": "Synthesis",
  "Réponse gardée": "Kept answer",
  "Page pas encore écrite": "Page not written yet",

  // home
  "Claude range ici tout ce que tu sauvegardes, par thème : une page par sujet, reliée aux autres, mise à jour à chaque partage et relue chaque nuit.":
    "Claude files everything you save here, by theme: one page per topic, linked to the others, updated with every share and reviewed every night.",
  "J'intègre « {title} »… ({n} en attente)": "Integrating “{title}”… ({n} waiting)",
  "{n} élément(s) à intégrer…": "{n} item(s) to integrate…",
  "Relu {when} : {what}": "Reviewed {when}: {what}",
  "{n} intégration(s) en erreur cette semaine": "{n} integration(s) failed this week",
  "Relire maintenant": "Review now",
  "Ouvrir dans Obsidian (zip)": "Open in Obsidian (zip)",
  "Synchro Obsidian": "Obsidian sync",
  "À creuser": "To dig into",
  "À creuser, d'après la dernière relecture": "To dig into, from the last review",
  "Vue": "View",
  "Pages": "Pages",
  "Journal|wiki log": "Log",
  "Filtrer les pages": "Filter pages",
  "Sections": "Sections",
  "Claude est en train d'écrire les premières pages à partir de ce que tu as déjà sauvegardé.":
    "Claude is writing the first pages from what you have already saved.",
  "Le wiki se remplit tout seul : chaque élément que tu partages y est intégré, rangé par thème avec ceux qui en parlent déjà.":
    "The wiki fills itself: every item you share is integrated into it, filed by theme with the ones that already talk about it.",
  "Aucune page ne correspond.": "No page matches.",
  "{n} sources": "{n} sources",
  "{n} source": "{n} source",
  "modifiée par toi {when}": "edited by you {when}",
  "mise à jour {when}": "updated {when}",
  "Relecture lancée : Claude passe le wiki en revue, le journal dira ce qu'il a corrigé.":
    "Review started: Claude is going over the wiki, the log will say what it fixed.",
  "Décompresse le zip et ouvre le dossier « KB Wiki » dans Obsidian (Ouvrir un dossier comme coffre).":
    "Unzip it and open the “KB Wiki” folder in Obsidian (Open folder as vault).",

  // log
  "Intégré": "Integrated",
  "Relecture": "Review",
  "Modifié": "Edited",
  "Obsidian": "Obsidian",
  "Rien encore : chaque intégration, relecture ou modification apparaîtra ici.":
    "Nothing yet: every integration, review or edit will show here.",
  "nouvelle": "new",
  "Rien de durable à intégrer.": "Nothing lasting to integrate.",
  "Fusion : {from} → {into}": "Merged: {from} → {into}",
  "Ta version a remplacé une mise à jour de Claude : la relecture de la nuit reprendra ce qui manque.":
    "Your version replaced an update by Claude: tonight's review will put back what's missing.",

  // a page
  "intégration": "integration",
  "relecture": "review",
  "réponse gardée": "kept answer",
  "modifiée dans Obsidian": "edited in Obsidian",
  "depuis Claude": "from Claude",
  "fusion": "merge",
  "Avec le connecteur KB, lis la page « {title} » de mon wiki (read_wiki_page) et les pages qui y renvoient, puis aide-moi à aller plus loin sur ce sujet : ce qui est solide, ce qui se contredit, ce qu'il me manque.":
    "With the KB connector, read the page “{title}” of my wiki (read_wiki_page) and the pages that link to it, then help me go further on this topic: what is solid, what contradicts, what I'm missing.",
  "Cette page a été fondue dans": "This page was merged into",
  "Cette page a été retirée du wiki.": "This page was removed from the wiki.",
  "Sources ({n})": "Sources ({n})",
  "Pages qui y renvoient": "Pages that link here",
  "Historique": "History",
  "Version {n}": "Version {n}",
  "toi": "you",

  // asking
  "À propos de la page « {title} » : ": "About the page “{title}”: ",
  "Je lis ton wiki…": "Reading your wiki…",
  "Impossible de garder cette réponse": "Couldn't keep this answer",
  "Toujours dans la file du wiki : la page apparaîtra un peu plus tard.": "Still in the wiki's queue: the page will show up a little later.",
  "Répondre à partir de": "Answer from",
  "Les pages de ton wiki : des synthèses par thème de tout ce que tu as lu": "Your wiki's pages: syntheses by theme of everything you read",
  "Les éléments que la recherche trouve, un par un (RAG)": "The items search finds, one by one (RAG)",
  "Éléments": "Items",
  "Les réponses s'appuient sur les pages de ton wiki : des synthèses par thème de tout ce que tu as sauvegardé, avec un renvoi vers chaque page.":
    "Answers draw on your wiki's pages: syntheses by theme of everything you saved, with a link to each page.",
  "d'après ton wiki": "from your wiki",
  "d'après tes éléments": "from your items",
  "Garder dans le wiki": "Keep in the wiki",
  "Je range la réponse dans le wiki…": "Filing the answer in the wiki…",
  "Gardée :": "Kept:",
  "Page du wiki": "Wiki page",
  "Je lis l'index du wiki…": "Reading the wiki's index…",
  "Rien dans le wiki là-dessus : je cherche dans tes éléments…": "Nothing in the wiki about it: searching your items…",

  // settings
  "Wiki et Obsidian": "Wiki and Obsidian",
  "Synchro Obsidian pas encore activée": "Obsidian sync not set up yet",
  "Synchro Obsidian activée": "Obsidian sync on",
  "Claude intègre chaque élément au wiki dès qu'il est prêt, et le relit chaque nuit. Le wiki s'ouvre aussi dans Obsidian, avec son graphe.":
    "Claude integrates each item into the wiki as soon as it's ready, and reviews it every night. The wiki also opens in Obsidian, with its graph.",
  "Synchro Obsidian pas encore activée. Ajoute": "Obsidian sync isn't set up yet. Add",
  "dans les variables du serveur (voir SETUP.md, étape Obsidian) : le wiki ira dans un dépôt GitHub privé que ton coffre Obsidian clone, et tes modifications faites dans Obsidian reviendront dans l'app.":
    "to the server's variables (see SETUP.md, Obsidian step): the wiki will go to a private GitHub repository your Obsidian vault clones, and your edits made in Obsidian will come back to the app.",
  "Synchronisé avec {repo}": "Synced with {repo}",
  "Ouvrir le dépôt": "Open the repository",
  "Télécharger le coffre (zip)": "Download the vault (zip)",
  "Dans Obsidian, tes modifications d'une page remplacent la version de l'app ; index.md, log.md et les notes de Sources sont réécrits par la KB.":
    "In Obsidian, your edits to a page replace the app's version; index.md, log.md and the Sources notes are rewritten by the KB.",
  "Tes conventions pour Claude": "Your conventions for Claude",
  "ex. Une page par livre avec ses meilleures citations. En Recherche, finis chaque page par les questions ouvertes.":
    "e.g. One page per book with its best quotes. In Research, end each page with the open questions.",
  "Claude les suit en écrivant chaque page. Tu peux aussi les modifier dans SCHEMA.md, dans Obsidian.":
    "Claude follows them when writing each page. You can also edit them in SCHEMA.md, in Obsidian.",
  "Enregistrer les conventions": "Save the conventions",
  "Notes laissées de côté (renomme-les dans Obsidian pour qu'elles deviennent des pages) :":
    "Notes left aside (rename them in Obsidian for them to become pages):",
};
