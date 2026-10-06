// Ouvrir une question dans Claude (usage couvert par l'abonnement), avec le connecteur KB.
import { t } from "./i18n";

const APP_KEY = "kb_open_in_desktop";

export const claudePrefs = {
  get desktop(): boolean {
    try { return localStorage.getItem(APP_KEY) === "1"; } catch { return false; }
  },
  set desktop(v: boolean) {
    try { v ? localStorage.setItem(APP_KEY, "1") : localStorage.removeItem(APP_KEY); } catch { /* stockage indisponible */ }
  },
};

export function claudePrompt(text: string, mode: "ask" | "project" | "advice" | "item"): string {
  const body = text.trim();
  if (mode === "advice") {
    return t("J'ai besoin d'un conseil. Appelle d'abord l'outil get_principles du connecteur KB avec ma situation : il "
      + "renvoie ma charte (mes principes et mes valeurs) et mes notes perso proches. Conseille-moi à partir de ce qui "
      + "compte pour moi : cite les principes et les notes que tu utilises, signale quand deux de mes principes se "
      + "contredisent, sois franc et concret, sans morale. Termine par un prochain pas.\n\nSituation : {text}", { text: body });
  }
  if (mode === "project") {
    return t("Je démarre un projet. Utilise l'outil find_for_project du connecteur KB (puis get_item sur les éléments clés), "
      + "et rédige-moi un dossier : ce qui peut me servir dans ma knowledge base, avec le lien source de chaque élément, "
      + "les personnes et outils à suivre, et les angles morts.\n\nProjet : {text}", { text: body });
  }
  if (mode === "item") return body;
  return t("Réponds à partir de ma knowledge base avec le connecteur KB (search_kb, plusieurs formulations si besoin, "
    + "puis get_item pour vérifier). Cite pour chaque information l'élément et son lien source, et signale ce qui ne "
    + "vient pas de ma KB.\n\nQuestion : {text}", { text: body });
}

/** Copie le prompt (filet de sécurité si le pré-remplissage ne passe pas) puis ouvre Claude. */
export async function openInClaude(prompt: string): Promise<boolean> {
  let copied = false;
  try { await navigator.clipboard.writeText(prompt); copied = true; } catch { /* presse-papiers refusé */ }
  const q = encodeURIComponent(prompt.slice(0, 12_000));
  const url = claudePrefs.desktop ? `claude://claude.ai/new?q=${q}` : `https://claude.ai/new?q=${q}`;
  if (claudePrefs.desktop) window.location.href = url;
  else window.open(url, "_blank", "noopener");
  return copied;
}
