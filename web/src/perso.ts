import type { ItemSummary, Kind, Space, SourceCard } from "./api";
import { t } from "./i18n";
import { kindLabel } from "./kinds";

// Catégories de l'espace Perso (mêmes clés que backend/app/taxonomy.py).
// Les principes et les valeurs forment la « charte », relue en entier par le mode Conseil.
export interface Category {
  id: string;
  label: string;
  plural: string;
  charter?: boolean;
  placeholder: string;
}

export const CATEGORIES: Category[] = [
  { id: "principe", label: t("Principe"), plural: t("Principes"), charter: true,
    placeholder: t("Une règle que tu t'es fixée, et pourquoi. Ex. : « Avant une grosse décision, j'attends 24 heures et j'en parle à quelqu'un. »") },
  { id: "valeur", label: t("Valeur"), plural: t("Valeurs"), charter: true,
    placeholder: t("Ce qui compte le plus pour toi, et ce que ça implique concrètement dans tes choix.") },
  { id: "lecon", label: t("Leçon"), plural: t("Leçons"),
    placeholder: t("Ce qui s'est passé, ce que tu en as appris, ce que tu feras autrement la prochaine fois.") },
  { id: "objectif", label: t("Objectif"), plural: t("Objectifs"),
    placeholder: t("Le but, pourquoi il compte pour toi, l'échéance, comment tu sauras qu'il est atteint.") },
  { id: "habitude", label: t("Habitude"), plural: t("Habitudes"),
    placeholder: t("La pratique, quand et comment, et pourquoi tu la gardes.") },
  { id: "reflexion", label: t("Réflexion"), plural: t("Réflexions"),
    placeholder: t("Une pensée, une question que tu te poses, une idée sur toi ou sur la vie.") },
  { id: "journal", label: t("Journal"), plural: t("Journal"),
    placeholder: t("Ta journée, un moment marquant, ce que tu as ressenti.") },
  { id: "citation", label: t("Citation"), plural: t("Citations"),
    placeholder: t("La phrase, son auteur, et pourquoi elle te parle.") },
  { id: "ressource", label: t("Ressource"), plural: t("Ressources"),
    placeholder: t("Un livre, une méthode, une vidéo : ce que tu en retiens et ce que tu veux appliquer.") },
];

export const categoryOf = (id: string | null | undefined) => CATEGORIES.find((c) => c.id === id);
export const categoryLabel = (id: string | null | undefined) => categoryOf(id)?.label ?? null;

export const SPACE_LABEL: Record<Space, string> = { main: t("Veille"), perso: t("Perso") };
export const spaceHome = (space: Space | null | undefined) => (space === "perso" ? "/perso" : "/");

/** Libellé d'en-tête d'une fiche : la catégorie en Perso (+ le type si ce n'est pas une note), le type sinon. */
export function headLabel(item: { space?: Space; category?: string | null; kind: Kind | null }): string {
  if (item.space !== "perso") return kindLabel(item.kind);
  const cat = categoryLabel(item.category) ?? t("Perso");
  return item.kind && item.kind !== "note" ? `${cat} · ${kindLabel(item.kind)}` : cat;
}

/** Une note écrite (ou dictée) se réécrit ; un lien ou un fichier non. */
export const isEditableNote = (item: Pick<ItemSummary, "kind" | "source_url"> & { input_url?: string | null; file_name?: string | null }) =>
  item.kind === "note" && !item.input_url && !item.file_name;

export const sourceSpace = (s: SourceCard): Space => s.space ?? "main";
