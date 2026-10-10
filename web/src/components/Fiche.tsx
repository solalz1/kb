import { Link } from "react-router-dom";
import type { ItemSummary } from "../api";
import { localized, t } from "../i18n";
import { IconPin, IconSpinner } from "../icons";
import { ago, agoShort, duration, fullDate, hostOf, kindLabel } from "../kinds";
import { headLabel } from "../perso";

/** A card of the feed. On phones a card with a picture keeps its "why" and drops its summary and tags. */
export function Fiche({ item }: { item: ItemSummary }) {
  const pending = item.status === "pending" || item.status === "processing";
  const failed = item.status === "error";
  const perso = item.space === "perso";
  const who = perso ? "" : [item.author || item.site_name || hostOf(item.source_url), duration(item.duration)].filter(Boolean).join(" · ");
  const shown = localized(item);
  const title = shown.title || (pending ? item.source_url || t("Nouvel élément") : t("Sans titre"));
  const journal = item.category === "journal" && item.entry_date;
  const classes = ["fiche", pending && "pending", item.user_note && "has-why", item.thumbnail && "has-thumb"].filter(Boolean).join(" ");

  return (
    <Link to={`/item/${item.id}`} className={classes} data-kind={item.kind ?? undefined} data-space={perso ? "perso" : undefined}>
      <div className="fiche-text">
        <div className="fiche-head">
          {!perso && item.kind && <span className="dot" data-kind={item.kind} />}
          <span className="kind">{headLabel(item)}</span>
          {who && <span className="who">{who}</span>}
          {item.pinned && <IconPin size={14} className="pin" aria-label={t("Épinglé")} aria-hidden={false} role="img" />}
          <span className="when">
            {journal ? fullDate(item.entry_date)
              : <><span className="long">{ago(item.created_at)}</span><span className="short">{perso ? ago(item.created_at) : agoShort(item.created_at)}</span></>}
          </span>
        </div>
        <h3>{title}</h3>
        {pending && (
          <span className="status"><IconSpinner size={15} />
            {item.kind === "note" ? t("Rangement et résumé en cours…") : t("Lecture et résumé en cours…")}</span>
        )}
        {failed && <span className="status err">{t("Échec du traitement : ouvre la fiche pour réessayer")}</span>}
        {!pending && !failed && shown.summary && <p className="summary">{shown.summary}</p>}
        {item.excerpt && <p className="excerpt">{item.excerpt.slice(0, 220)}…</p>}
        {item.user_note && <p className="why">« {item.user_note} »</p>}
        {item.tags?.length > 0 && !perso && <div className="tags">{item.tags.slice(0, 5).map((tag) => <span key={tag}>#{tag}</span>)}</div>}
      </div>
      {item.thumbnail && (
        <img className="thumb" src={item.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer"
             onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />
      )}
    </Link>
  );
}

/** A kraft card that brings back something saved a while ago. */
export function ResurfaceCard({ item }: { item: ItemSummary }) {
  const shown = localized(item);
  return (
    <Link to={`/item/${item.id}`} className="resurface-card">
      <span className="meta">{item.space === "perso" ? headLabel(item) : kindLabel(item.kind)} · {ago(item.created_at)}</span>
      <span className="t">{shown.title || t("Sans titre")}</span>
    </Link>
  );
}

/** Perso: one note to read again, at full width. */
export function ResurfaceNote({ item }: { item: ItemSummary }) {
  const shown = localized(item);
  return (
    <Link to={`/item/${item.id}`} className="resurface-note">
      <span className="head"><b>{headLabel(item)}</b><span>{fullDate(item.created_at)}</span></span>
      <span className="t">{shown.title || t("Sans titre")}</span>
      {shown.summary && <span className="s">{shown.summary.length > 110 ? `${shown.summary.slice(0, 108)}…` : shown.summary}</span>}
    </Link>
  );
}
