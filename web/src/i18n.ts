// Interface language (French or English).
//
// The French text is the key: t("Ajouter à ta KB") shows "Add to your KB" when the app is in English. English
// strings live in src/i18n/en/*.ts (one module per area, merged below). A missing translation shows the French text,
// never an empty label, and is reported once in the console during development.
// Variables: t("{n} éléments", { n: 3 }). Changing the language reloads the app.
// When one French word needs two English ones, the key carries a context after "|", never shown in French:
// t("Annuler") is "Cancel", t("Annuler|undo") is "Undo".

export type Lang = "fr" | "en";

const KEY = "kb_lang";
const modules = import.meta.glob<{ default: Record<string, string> }>("./i18n/en/*.ts", { eager: true });
const EN: Record<string, string> = Object.assign({}, ...Object.values(modules).map((m) => m.default));

function stored(): Lang {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "fr" || v === "en") return v;
  } catch { /* storage unavailable */ }
  return "fr";
}

export const lang: Lang = stored();
export const locale = lang === "fr" ? "fr-FR" : "en-US";
if (typeof document !== "undefined") document.documentElement.lang = lang;

export function setLang(next: Lang): void {
  try { localStorage.setItem(KEY, next); } catch { /* storage unavailable */ }
  window.location.reload();
}

const reported = new Set<string>();

export function t(fr: string, vars?: Record<string, string | number>): string {
  const bar = fr.indexOf("|");
  let out = bar >= 0 ? fr.slice(0, bar) : fr;
  if (lang === "en") {
    const en = EN[fr];
    if (en !== undefined) out = en;
    else if (import.meta.env.DEV && !reported.has(fr)) { reported.add(fr); console.warn("[i18n] missing:", fr); }
  }
  return vars ? out.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : out;
}

/** Text written by the server or by Claude in French (messages, errors) shown as is when there's no translation. */
export const tServer = (text: string | null | undefined): string => (text ? t(text) : "");

type Localizable = {
  title?: string | null;
  summary?: string | null;
  key_points?: string[] | null;
  use_cases?: string[] | null;
  translations?: Partial<Record<string, { title?: string | null; summary?: string | null; key_points?: string[]; use_cases?: string[] }>> | null;
};

/** An item's title, summary, key points and use cases in the interface language, when the KB has them. */
export function localized<T extends Localizable>(item: T): T {
  const tr = item.translations?.[lang];
  if (!tr) return item;
  const out = { ...item };
  if (tr.title) out.title = tr.title;
  if (tr.summary) out.summary = tr.summary;
  if (tr.key_points?.length) out.key_points = tr.key_points;
  if (tr.use_cases?.length) out.use_cases = tr.use_cases;
  return out;
}
