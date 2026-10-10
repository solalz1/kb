import type { Kind } from "./api";
import { lang, locale, t } from "./i18n";

// Chaque type de contenu a sa couleur de fiche bristol (la couleur porte l'information).
export const KINDS: Record<Kind, { label: string; plural: string }> = {
  tweet: { label: "Tweet", plural: "Tweets" },
  article: { label: "Article", plural: "Articles" },
  youtube: { label: "YouTube", plural: "YouTube" },
  video: { label: t("Vidéo"), plural: t("Vidéos") },
  audio: { label: "Audio", plural: "Audio" },
  pdf: { label: "PDF", plural: "PDF" },
  paper: { label: t("Papier"), plural: t("Papiers") },
  image: { label: "Image", plural: "Images" },
  document: { label: "Document", plural: "Documents" },
  note: { label: "Note", plural: "Notes" },
  repo: { label: "GitHub", plural: "GitHub" },
};

export const FILTER_ORDER: Kind[] = ["tweet", "article", "youtube", "video", "pdf", "paper", "image", "note", "audio", "repo", "document"];

/** The feed's type filters: a few groups of kinds (sent to the API as a comma-separated list). */
export const KIND_FILTERS: { id: string; label: string; short?: string; kinds: Kind[] }[] = [
  { id: "tweet", label: "Tweets", kinds: ["tweet"] },
  { id: "article", label: "Articles", kinds: ["article"] },
  { id: "video", label: t("Vidéos"), kinds: ["youtube", "video", "audio"] },
  { id: "paper", label: t("Papiers & PDF"), short: t("Papiers"), kinds: ["paper", "pdf", "document"] },
  { id: "image", label: "Images", kinds: ["image"] },
  { id: "note", label: "Notes", kinds: ["note"] },
  { id: "repo", label: "GitHub", kinds: ["repo"] },
];
export const kindsOf = (filter: string) => KIND_FILTERS.find((f) => f.id === filter)?.kinds.join(",") ?? filter;

/** "2 h 04", "34 min": how long a video or a podcast lasts. */
export function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 1) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("{n} min", { n: Math.max(1, minutes) });
  return t("{h} h {mm}", { h: Math.floor(minutes / 60), mm: String(minutes % 60).padStart(2, "0") });
}

export const kindLabel = (k: Kind | null | undefined) => (k && KINDS[k]?.label) || t("Élément");

export function sourceLabel(kind: Kind | null | undefined): string {
  switch (kind) {
    case "tweet": return t("Voir le tweet");
    case "youtube": case "video": return t("Voir la vidéo");
    case "audio": return t("Écouter");
    case "pdf": case "paper": return t("Ouvrir le document");
    case "repo": return t("Voir le dépôt");
    default: return t("Ouvrir la source");
  }
}

const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return t("à l'instant");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

/** "2 h", "hier", "3 j": the short form of `ago`, for phone cards. */
export function agoShort(iso: string | null | undefined): string {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return t("à l'instant");
  if (diff < 3600) return t("{n} min", { n: Math.round(diff / 60) });
  if (diff < 86400) return t("{n} h", { n: Math.round(diff / 3600) });
  const days = Math.round(diff / 86400);
  if (days === 1) return t("hier");
  if (days < 30) return t("{n} j", { n: days });
  return new Date(iso).toLocaleDateString(locale, { day: "numeric", month: "short" });
}

export function fullDate(iso: string | null | undefined): string {
  if (!iso) return "";
  // a bare day ("2026-10-07") is a local date, not midnight UTC
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = day ? new Date(+day[1], +day[2] - 1, +day[3]) : new Date(iso);
  return d.toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" });
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

const GENRES: Record<string, string> = {
  thread: "Thread", opinion: t("Opinion"), news: t("Actualité"), tutorial: t("Tutoriel"), paper: t("Recherche"),
  tool: t("Outil"), announcement: t("Annonce"), talk: t("Conférence"), podcast: "Podcast", course: t("Cours"),
  reference: t("Référence"), dataset: "Dataset", inspiration: "Inspiration", "personal-note": t("Note perso"),
};
export const genreLabel = (g: string | null | undefined) => (g && GENRES[g]) || null;

export const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
