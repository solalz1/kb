// The wiki's vocabulary (same keys as backend/app/wiki.py) and how a page's Markdown becomes HTML in the app.
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { WikiKind, WikiSection } from "./api";
import { t } from "./i18n";

export const SECTIONS: { key: WikiSection; label: string; hint: string }[] = [
  { key: "personal", label: t("Personnel"), hint: t("Toi : objectifs, principes, leçons, habitudes, journal") },
  { key: "research", label: t("Recherche"), hint: t("Les sujets que tu creuses sur la durée, avec une thèse qui évolue") },
  { key: "reading", label: t("Lectures"), hint: t("Une page par livre, et ses grands thèmes") },
  { key: "business", label: t("Business"), hint: t("Tes activités : clients, offres, prospection, méthodes") },
  { key: "competitive", label: t("Concurrence"), hint: t("Les entreprises, produits et marchés que tu suis") },
];

export const sectionLabel = (key: WikiSection | null | undefined) => SECTIONS.find((s) => s.key === key)?.label ?? "";

export const KIND_LABEL: Record<WikiKind, string> = {
  theme: t("Thème"), entity: t("Entité"), book: t("Livre"), synthesis: t("Synthèse"), answer: t("Réponse gardée"),
};

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

const LINK = /\[\[([^[\]|#\n]+)(#[^[\]|\n]*)?(\|[^[\]\n]*)?\]\]/g;
const CITE = /\[src:([0-9a-f]{8})\]/gi;

/**
 * A page's Markdown as HTML. [[Title]] leads to that page (greyed when it isn't written yet); [src:ref] becomes the
 * number of the source in the page's list, which opens the item. Links carry `data-to`: the page navigates in the app.
 */
export function renderWiki(md: string, resolved: Record<string, string>,
                           refs: Record<string, { id: string; title: string | null; n?: number }>): string {
  let text = md.replace(LINK, (_m, target: string, _heading?: string, alias?: string) => {
    const label = esc((alias ? alias.slice(1) : target).trim());
    const slug = resolved[target.trim().toLowerCase()];
    return slug
      ? `<a class="wikilink" data-to="/wiki/${encodeURIComponent(slug)}" href="/wiki/${encodeURIComponent(slug)}">${label}</a>`
      : `<span class="wikilink missing" title="${esc(t("Page pas encore écrite"))}">${label}</span>`;
  });
  text = text.replace(CITE, (_m, ref: string) => {
    const it = refs[ref.toLowerCase()];
    if (!it) return "";
    return `<a class="src-ref" data-to="/item/${it.id}" href="/item/${it.id}" title="${esc(it.title ?? "")}">${it.n ?? "↗"}</a>`;
  });
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["data-to"] });
}

/** A click on a link drawn by renderWiki: handled by the app's router instead of a full page load. */
export function internalTarget(e: React.MouseEvent): string | null {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return null;
  const a = (e.target as HTMLElement).closest("a[data-to]") as HTMLAnchorElement | null;
  return a?.dataset.to ?? null;
}
