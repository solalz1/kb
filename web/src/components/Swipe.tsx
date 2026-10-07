import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** One action revealed behind a card. */
export type SwipeAction = {
  id: string;
  label: string;
  icon: ReactNode;
  tone: "pin" | "ink" | "danger";
  run: () => void;
};

export type SwipeSide = "start" | "end" | null;

type Props = {
  children: ReactNode;
  /** Revealed by swiping right. A long swipe runs it. */
  start?: SwipeAction;
  /** Revealed by swiping left, left to right. A long swipe runs the last one (the one at the edge). */
  end?: SwipeAction[];
  open: SwipeSide;
  onOpenChange: (side: SwipeSide) => void;
};

const BUTTON = 84;      // width of one revealed action
const LOCK = 8;         // px before deciding whether the finger scrolls or swipes
const KEEP_OPEN = 48;   // a shorter swipe closes the row again
const LONG = 0.45;      // a swipe past this share of the width runs the action at the edge…
const BEYOND = 56;      // …and always well past the open buttons

/**
 * Swipe a card left or right to reveal actions, as in Mail on iPhone. Touch and pen only: with a mouse the card
 * stays a plain link (its page has the same actions). Vertical moves are left to the browser (touch-action: pan-y).
 */
export function SwipeRow({ children, start, end = [], open, onOpenChange }: Props) {
  const row = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ id: number; x: number; y: number; base: number; mode: "pending" | "swipe" | "scroll" } | null>(null);
  const swiped = useRef(false);            // swallow the click that ends a swipe
  const leavingRef = useRef(false);
  const offsetRef = useRef(0);             // the latest offset, for handlers that run before a re-render
  const [offset, setOffsetState] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const sideRef = useRef<"start" | "end">("end");   // the side last revealed: its panel shrinks back with the card
  const setOffset = (x: number) => {
    offsetRef.current = x;
    if (x) sideRef.current = x > 0 ? "start" : "end";
    setOffsetState(x);
  };

  const rest = open === "start" && start ? BUTTON : open === "end" && end.length ? -BUTTON * end.length : 0;
  useLayoutEffect(() => {
    if (!gesture.current && !leavingRef.current) setOffset(rest);
  }, [rest]); // eslint-disable-line react-hooks/exhaustive-deps

  // Once the finger is swiping sideways, the page must not scroll (or coast) up or down: a coasting page makes the
  // browser swallow the next tap, which would then miss the revealed buttons. React's touch handlers are passive.
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    const hold = (e: TouchEvent) => { if (gesture.current?.mode === "swipe" && e.cancelable) e.preventDefault(); };
    el.addEventListener("touchmove", hold, { passive: false });
    return () => el.removeEventListener("touchmove", hold);
  }, []);

  // a tap anywhere else closes an open row
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => { if (!row.current?.contains(e.target as Node)) onOpenChange(null); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open, onOpenChange]);

  const width = () => row.current?.offsetWidth || 360;
  const longStart = () => Math.max(width() * LONG, BUTTON + BEYOND);
  const longEnd = () => Math.max(width() * LONG, BUTTON * end.length + BEYOND);

  const clamp = (x: number) => {
    const w = width();
    let v = x;
    if (!start) v = Math.min(v, 0);
    if (!end.length) v = Math.max(v, 0);
    return Math.max(-w, Math.min(w, v));
  };

  const run = (action: SwipeAction, side: "start" | "end") => {
    if (side === "end") {
      // the card slides away before the list drops it (or comes back if the action failed)
      leavingRef.current = true;
      setLeaving(true);
      setOffset(-width());
      window.setTimeout(() => {
        action.run();
        leavingRef.current = false;
        setLeaving(false);
        setOffset(0);
      }, 180);
    } else {
      action.run();
      setOffset(0);
    }
    onOpenChange(null);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    swiped.current = false;
    if (e.pointerType === "mouse" || leavingRef.current) return;
    gesture.current = { id: e.pointerId, x: e.clientX, y: e.clientY, base: offsetRef.current, mode: "pending" };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const g = gesture.current;
    if (!g || g.id !== e.pointerId || g.mode === "scroll") return;
    const dx = e.clientX - g.x;
    const dy = e.clientY - g.y;
    if (g.mode === "pending") {
      if (Math.abs(dx) < LOCK && Math.abs(dy) < LOCK) return;
      if (Math.abs(dy) >= Math.abs(dx)) { g.mode = "scroll"; return; }
      g.mode = "swipe";
      row.current?.setPointerCapture(e.pointerId);
      setDragging(true);
      if (open) onOpenChange(null);
    }
    setOffset(clamp(g.base + dx));
  };

  const finish = (e: React.PointerEvent, cancelled: boolean) => {
    const g = gesture.current;
    if (!g || g.id !== e.pointerId) return;
    gesture.current = null;
    if (g.mode === "pending" && !cancelled && open && !(e.target as HTMLElement).closest(".swipe-actions")) {
      // a tap on an open card closes it, without waiting for a click the browser may never send
      swiped.current = true;
      onOpenChange(null);
      return;
    }
    if (g.mode !== "swipe") return;
    swiped.current = true;
    setDragging(false);
    const x = offsetRef.current;
    if (!cancelled && start && x > longStart()) return run(start, "start");
    if (!cancelled && end.length && x < -longEnd()) return run(end[end.length - 1], "end");
    if (!cancelled && start && x > KEEP_OPEN) { onOpenChange("start"); setOffset(BUTTON); return; }
    if (!cancelled && end.length && x < -KEEP_OPEN) { onOpenChange("end"); setOffset(-BUTTON * end.length); return; }
    onOpenChange(null);
    setOffset(0);
  };

  const onClickCapture = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest(".swipe-actions")) return;   // the revealed buttons
    if (swiped.current || open) {
      e.preventDefault();
      e.stopPropagation();
      swiped.current = false;
      if (open) onOpenChange(null);
    }
  };

  const armedEnd = end.length > 0 && offset < -longEnd();
  const armedStart = !!start && offset > longStart();
  const shown = (side: "start" | "end") => open === side || (side === "start" ? offset > 0 : offset < 0);

  return (
    <div ref={row} className={`swipe${dragging ? " dragging" : ""}${leaving ? " leaving" : ""}`}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove}
         onPointerUp={(e) => finish(e, false)} onPointerCancel={(e) => finish(e, true)}
         onClickCapture={onClickCapture}>
      {start && sideRef.current === "start" && (
        <div className="swipe-actions start" style={{ width: Math.max(0, offset) }} aria-hidden={!shown("start")}>
          <SwipeButton action={start} armed={armedStart} tabIndex={open === "start" ? 0 : -1}
                       onClick={() => run(start, "start")} />
        </div>
      )}
      {end.length > 0 && sideRef.current === "end" && (
        <div className="swipe-actions end" style={{ width: Math.max(0, -offset) }} aria-hidden={!shown("end")}>
          {end.map((a, i) => (
            <SwipeButton key={a.id} action={a} tabIndex={open === "end" ? 0 : -1}
                         armed={armedEnd && i === end.length - 1} hidden={armedEnd && i < end.length - 1}
                         onClick={() => run(a, "end")} />
          ))}
        </div>
      )}
      <div className="swipe-front" style={{ transform: offset ? `translateX(${offset}px)` : undefined }}>
        {children}
      </div>
    </div>
  );
}

function SwipeButton({ action, armed, hidden, tabIndex, onClick }: {
  action: SwipeAction; armed?: boolean; hidden?: boolean; tabIndex: number; onClick: () => void;
}) {
  // A finger runs the action when it lifts: right after a swipe, browsers may drop the click of a quick tap.
  // A mouse or the keyboard go through the click as usual.
  const press = useRef<{ id: number; x: number; y: number } | null>(null);
  const ran = useRef(false);
  return (
    <button type="button" className={`swipe-btn${armed ? " armed" : ""}${hidden ? " hidden" : ""}`} data-tone={action.tone}
            data-action={action.id} tabIndex={tabIndex}
            onPointerDown={(e) => { press.current = e.pointerType === "mouse" ? null : { id: e.pointerId, x: e.clientX, y: e.clientY }; }}
            onPointerUp={(e) => {
              const p = press.current;
              press.current = null;
              if (!p || p.id !== e.pointerId || Math.hypot(e.clientX - p.x, e.clientY - p.y) > LOCK) return;
              ran.current = true;
              window.setTimeout(() => { ran.current = false; }, 600);
              onClick();
            }}
            onClick={() => { if (ran.current) { ran.current = false; return; } onClick(); }}>
      {action.icon}
      <span>{action.label}</span>
    </button>
  );
}
