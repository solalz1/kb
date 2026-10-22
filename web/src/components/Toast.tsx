import { useEffect, useState, type ReactNode } from "react";
import { reducedMotion } from "../motion";

/**
 * A glass notice above the tab bar. It rises in (styles.css) and, once its page drops it, sinks away instead of
 * vanishing at once; a new one (another `id`) rises in again. While it leaves, it can't be tapped.
 */
export function Toast({ id, children }: { id?: string | number; children: ReactNode }) {
  const [shown, setShown] = useState<{ id?: string | number; content: ReactNode } | null>(children ? { id, content: children } : null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (children) { setShown({ id, content: children }); setLeaving(false); return; }
    setLeaving(true);
    const timer = window.setTimeout(() => { setShown(null); setLeaving(false); }, reducedMotion() ? 0 : 220);
    return () => window.clearTimeout(timer);
  }, [children, id]);

  if (!shown) return null;
  return <div className={`toast${leaving ? " leaving" : ""}`} role="status" key={shown.id}>{shown.content}</div>;
}
