import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type MenuItem = { id: string; label: string; icon?: ReactNode; danger?: boolean; run: () => void };

/**
 * A menu at a point of the screen: a long press on a card (iPhone), a right click (Mac). Glass like the other menus,
 * kept inside the screen, closed by a tap elsewhere, Escape or a scroll; the keyboard starts on its first item.
 */
export function ContextMenu({ at, items, onClose }: { at: { x: number; y: number }; items: MenuItem[]; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ left: number; top: number; origin: string } | null>(null);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const margin = 12;
    const { offsetWidth: w, offsetHeight: h } = el;
    let left = at.x;
    let top = at.y + 10;
    let x = "left";
    let y = "top";
    if (left + w > window.innerWidth - margin) { left = Math.max(margin, at.x - w); x = "right"; }
    if (top + h > window.innerHeight - margin) { top = Math.max(margin, at.y - h - 10); y = "bottom"; }
    setPlace({ left, top, origin: `${y} ${x}` });
    el.querySelector<HTMLElement>("[role='menuitem']")?.focus({ preventScroll: true });
  }, [at.x, at.y]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const all = [...(box.current?.querySelectorAll<HTMLElement>("[role='menuitem']") ?? [])];
      const i = all.indexOf(document.activeElement as HTMLElement);
      all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length]?.focus();
      e.preventDefault();
    };
    const scroll = () => onClose();
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, { passive: true });
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("scroll", scroll); };
  }, [onClose]);

  return createPortal(
    <>
      <div className="menu-catcher" onPointerDown={(e) => { e.preventDefault(); onClose(); }} onContextMenu={(e) => e.preventDefault()} />
      <div ref={box} className="menu context" role="menu"
           style={place ? { left: place.left, top: place.top, transformOrigin: place.origin } : { left: at.x, top: at.y, visibility: "hidden" }}>
        {items.map((it) => (
          <button key={it.id} type="button" role="menuitem" className={it.danger ? "danger" : undefined}
                  onClick={() => { onClose(); it.run(); }}>
            <span>{it.label}</span>{it.icon}
          </button>
        ))}
      </div>
    </>,
    document.body,
  );
}
