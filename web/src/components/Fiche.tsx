import { AlertTriangle, Loader2, Pin } from "lucide-react";
import { Link } from "react-router-dom";
import type { ItemSummary } from "../api";
import { ago, hostOf } from "../kinds";
import { headLabel } from "../perso";

export function Fiche({ item, compact = false }: { item: ItemSummary; compact?: boolean }) {
  const pending = item.status === "pending" || item.status === "processing";
  const failed = item.status === "error";
  const perso = item.space === "perso";
  const who = item.author || item.site_name || hostOf(item.source_url);
  const title = item.title || (pending ? item.source_url || "Nouvel élément" : "Sans titre");

  return (
    <Link to={`/item/${item.id}`} className={`fiche${pending ? " pending" : ""}`} data-kind={item.kind ?? undefined}
          data-space={perso ? "perso" : undefined}>
      <div className="fiche-head">
        <span className="kind">{headLabel(item)}</span>
        {who && <span className="who">{who}</span>}
        {item.pinned && <Pin size={14} className="pin" aria-label="Épinglé" />}
        <span className="when">{ago(item.created_at)}</span>
      </div>
      <div className="fiche-body">
        <div className="fiche-text">
          <h3>{title}</h3>
          {pending && (
            <span className="status"><Loader2 size={15} className="spin" />
              {item.kind === "note" ? " Rangement et résumé en cours…" : " Lecture et résumé en cours…"}</span>
          )}
          {failed && (
            <span className="status err"><AlertTriangle size={15} /> Échec du traitement : ouvre la fiche pour réessayer</span>
          )}
          {!pending && !failed && item.summary && <p className="ruled">{item.summary}</p>}
          {!compact && item.excerpt && <p className="excerpt">{item.excerpt.slice(0, 220)}…</p>}
          {!compact && item.user_note && <p className="note-ink">{item.user_note}</p>}
          {!compact && item.tags?.length > 0 && (
            <div className="tags">{item.tags.slice(0, 5).map((t) => <span key={t}>#{t}</span>)}</div>
          )}
        </div>
        {!compact && item.thumbnail && (
          <img className="thumb" src={item.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer"
               onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />
        )}
      </div>
    </Link>
  );
}
