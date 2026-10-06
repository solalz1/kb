// English for the app shell, shared labels (kinds, Perso categories), the API client and the Claude prompts.
// Keys are the French strings exactly as written in the code.
export default {
  // navigation and login
  "Veille": "Feed",
  "Perso": "Personal",
  "Demander": "Ask",
  "Ajouter": "Add",
  "À faire": "To do",
  "Réglages": "Settings",
  "second cerveau": "second brain",
  "Ce jeton ne correspond pas à KB_API_TOKEN sur le serveur.": "This token doesn't match KB_API_TOKEN on the server.",
  "Serveur injoignable. Vérifie l'adresse de l'API.": "Server unreachable. Check the API address.",
  "connexion": "sign in",
  "Ta knowledge base": "Your knowledge base",
  "Colle le jeton défini dans la variable KB_API_TOKEN de ton serveur. Il reste sur cet appareil.":
    "Paste the token set in your server's KB_API_TOKEN variable. It stays on this device.",
  "Jeton d'accès": "Access token",
  "Adresse de l'API": "API address",
  "Seulement si l'app et l'API sont hébergées séparément.": "Only if the app and the API are hosted separately.",
  "Se connecter": "Sign in",
  "API hébergée ailleurs ?": "API hosted elsewhere?",

  // kinds and genres
  "Vidéo": "Video",
  "Vidéos": "Videos",
  "Élément": "Item",
  "Voir le tweet": "View the tweet",
  "Voir la vidéo": "Watch the video",
  "Écouter": "Listen",
  "Ouvrir le document": "Open the document",
  "Voir le dépôt": "View the repository",
  "Ouvrir la source": "Open the source",
  "à l'instant": "just now",
  "Opinion": "Opinion",
  "Actualité": "News",
  "Tutoriel": "Tutorial",
  "Recherche": "Research",
  "Outil": "Tool",
  "Annonce": "Announcement",
  "Conférence": "Talk",
  "Cours": "Course",
  "Référence": "Reference",
  "Note perso": "Personal note",

  // Perso categories
  "Principe": "Principle",
  "Principes": "Principles",
  "Valeur": "Value",
  "Valeurs": "Values",
  "Leçon": "Lesson",
  "Leçons": "Lessons",
  "Objectif": "Goal",
  "Objectifs": "Goals",
  "Habitude": "Habit",
  "Habitudes": "Habits",
  "Réflexion": "Reflection",
  "Réflexions": "Reflections",
  "Journal": "Journal",
  "Citation": "Quote",
  "Citations": "Quotes",
  "Ressource": "Resource",
  "Ressources": "Resources",
  "Une règle que tu t'es fixée, et pourquoi. Ex. : « Avant une grosse décision, j'attends 24 heures et j'en parle à quelqu'un. »":
    "A rule you've set for yourself, and why. E.g. \"Before a big decision, I wait 24 hours and talk it through with someone.\"",
  "Ce qui compte le plus pour toi, et ce que ça implique concrètement dans tes choix.":
    "What matters most to you, and what it means in practice for your choices.",
  "Ce qui s'est passé, ce que tu en as appris, ce que tu feras autrement la prochaine fois.":
    "What happened, what you learned from it, what you'll do differently next time.",
  "Le but, pourquoi il compte pour toi, l'échéance, comment tu sauras qu'il est atteint.":
    "The goal, why it matters to you, the deadline, how you'll know you've reached it.",
  "La pratique, quand et comment, et pourquoi tu la gardes.": "The practice, when and how, and why you keep it.",
  "Une pensée, une question que tu te poses, une idée sur toi ou sur la vie.":
    "A thought, a question you're asking yourself, an idea about yourself or about life.",
  "Ta journée, un moment marquant, ce que tu as ressenti.": "Your day, a moment that stood out, how you felt.",
  "La phrase, son auteur, et pourquoi elle te parle.": "The quote, who said it, and why it speaks to you.",
  "Un livre, une méthode, une vidéo : ce que tu en retiens et ce que tu veux appliquer.":
    "A book, a method, a video: what you take from it and what you want to apply.",

  // API client
  "Envoi interrompu : vérifie ta connexion et garde l'app ouverte pendant l'envoi.":
    "Upload interrupted: check your connection and keep the app open while it uploads.",
  "Export impossible": "Export failed",
  "Le chat ne répond pas": "The chat isn't responding",

  // messages and errors written by the server
  "Ajouté à ta KB ✓": "Added to your KB ✓",
  "Déjà dans ta KB ✓": "Already in your KB ✓",
  "Jeton invalide ou manquant": "Invalid or missing token",
  "Envoie une URL, un texte ou un fichier": "Send a URL, some text or a file",
  "Message vide": "Empty message",

  // prompts opened in Claude
  ["J'ai besoin d'un conseil. Appelle d'abord l'outil get_principles du connecteur KB avec ma situation : il "
    + "renvoie ma charte (mes principes et mes valeurs) et mes notes perso proches. Conseille-moi à partir de ce qui "
    + "compte pour moi : cite les principes et les notes que tu utilises, signale quand deux de mes principes se "
    + "contredisent, sois franc et concret, sans morale. Termine par un prochain pas.\n\nSituation : {text}"]:
    "I need some advice. First call the get_principles tool of the KB connector with my situation: it returns my "
    + "charter (my principles and values) and my related personal notes. Advise me based on what matters to me: cite "
    + "the principles and notes you use, point out when two of my principles conflict, be frank and concrete, no "
    + "moralizing. End with a next step.\n\nSituation: {text}",
  ["Je démarre un projet. Utilise l'outil find_for_project du connecteur KB (puis get_item sur les éléments clés), "
    + "et rédige-moi un dossier : ce qui peut me servir dans ma knowledge base, avec le lien source de chaque élément, "
    + "les personnes et outils à suivre, et les angles morts.\n\nProjet : {text}"]:
    "I'm starting a project. Use the find_for_project tool of the KB connector (then get_item on the key items) and "
    + "write me a brief: what in my knowledge base can help, with the source link of each item, the people and tools "
    + "to follow, and the blind spots.\n\nProject: {text}",
  ["Réponds à partir de ma knowledge base avec le connecteur KB (search_kb, plusieurs formulations si besoin, "
    + "puis get_item pour vérifier). Cite pour chaque information l'élément et son lien source, et signale ce qui ne "
    + "vient pas de ma KB.\n\nQuestion : {text}"]:
    "Answer from my knowledge base using the KB connector (search_kb, several phrasings if needed, then get_item to "
    + "check). For each piece of information cite the item and its source link, and flag anything that doesn't come "
    + "from my KB.\n\nQuestion: {text}",
} as Record<string, string>;
