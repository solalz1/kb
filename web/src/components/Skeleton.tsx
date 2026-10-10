import { t } from "../i18n";

/** Grey bars in the shape of what is loading, shimmering, instead of a "Loading…" line. */
export function Lines({ n = 3, widths = ["92%", "100%", "64%"] }: { n?: number; widths?: string[] }) {
  return (
    <div className="sk-lines" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => <span key={i} className="sk" style={{ width: widths[i % widths.length] }} />)}
    </div>
  );
}

/** Feed cards being loaded. */
export function CardsSkeleton({ n = 4 }: { n?: number }) {
  return (
    <div className="feed-list sk-list" role="status" aria-label={t("Chargement…")}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="fiche sk-card" aria-hidden="true">
          <div className="fiche-text">
            <span className="sk" style={{ width: 120, height: 12 }} />
            <span className="sk" style={{ width: i % 2 ? "70%" : "86%", height: 18 }} />
            <span className="sk" style={{ width: "100%" }} />
            <span className="sk" style={{ width: "58%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** A page whose content is loading: a title and a few paragraphs. */
export function PageSkeleton({ title = true }: { title?: boolean }) {
  return (
    <div className="sk-page" role="status" aria-label={t("Chargement…")}>
      {title && <span className="sk sk-title" />}
      <Lines n={3} />
      <Lines n={2} widths={["100%", "76%"]} />
    </div>
  );
}
