import { lang, locale, t } from "./i18n";

/** "2026-10-07" for a local date (never toISOString: that would be the UTC day). */
export const keyOf = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const parseKey = (key: string) => {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d);
};

/** A real calendar day ("2026-02-30" is not one: Date would roll it over to March). */
export const isDayKey = (s: string | null | undefined): s is string =>
  Boolean(s && /^\d{4}-\d{2}-\d{2}$/.test(s) && keyOf(parseKey(s)) === s);

export const capitalize = (s: string) => (lang === "fr" ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** "Samedi 21 novembre 2026" (without the year: "Samedi 21 novembre"). */
export const dayLabel = (d: Date, year = true) =>
  capitalize(d.toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long", ...(year ? { year: "numeric" } : {}) }));

/** « Il y a 3 mois », « Il y a 1 an », « Il y a 2 ans ». */
export const agoLabel = (months: number) =>
  months % 12 ? (months > 1 ? t("Il y a {n} mois", { n: months }) : t("Il y a 1 mois"))
    : months === 12 ? t("Il y a 1 an") : t("Il y a {n} ans", { n: months / 12 });

/** Where a journal day opens: /journal for today, /journal/<day> otherwise. */
export const journalHref = (key: string, today = keyOf(new Date())) => (key === today ? "/journal" : `/journal/${key}`);
