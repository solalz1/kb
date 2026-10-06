import type { Kind } from "./api";

// Chaque type de contenu a sa couleur de fiche bristol (la couleur porte l'information).
export const KINDS: Record<Kind, { label: string; plural: string }> = {
  tweet: { label: "Tweet", plural: "Tweets" },
  article: { label: "Article", plural: "Articles" },
  youtube: { label: "YouTube", plural: "YouTube" },
  video: { label: "Vidéo", plural: "Vidéos" },
  audio: { label: "Audio", plural: "Audio" },
  pdf: { label: "PDF", plural: "PDF" },
  paper: { label: "Paper", plural: "Papers" },
  image: { label: "Image", plural: "Images" },
  document: { label: "Document", plural: "Documents" },
  note: { label: "Note", plural: "Notes" },
  repo: { label: "GitHub", plural: "GitHub" },
};

export const FILTER_ORDER: Kind[] = ["tweet", "article", "youtube", "video", "pdf", "paper", "image", "note", "audio", "repo", "document"];

export const kindLabel = (k: Kind | null | undefined) => (k && KINDS[k]?.label) || "Élément";

export function sourceLabel(kind: Kind | null | undefined): string {
  switch (kind) {
    case "tweet": return "Voir le tweet";
    case "youtube": case "video": return "Voir la vidéo";
    case "audio": return "Écouter";
    case "pdf": case "paper": return "Ouvrir le document";
    case "repo": return "Voir le dépôt";
    default: return "Ouvrir la source";
  }
}

const rtf = new Intl.RelativeTimeFormat("fr", { numeric: "auto" });

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return "à l'instant";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

export function fullDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

const GENRES: Record<string, string> = {
  thread: "Thread", opinion: "Opinion", news: "Actualité", tutorial: "Tutoriel", paper: "Recherche", tool: "Outil",
  announcement: "Annonce", talk: "Conférence", podcast: "Podcast", course: "Cours", reference: "Référence",
  dataset: "Dataset", inspiration: "Inspiration", "personal-note": "Note perso",
};
export const genreLabel = (g: string | null | undefined) => (g && GENRES[g]) || null;

export const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
