import { useEffect, useRef } from "react";
import { t } from "../i18n";
import { IconDown, IconSpinner } from "../icons";
import { EDGE, edgeSwipe, reducedMotion, skipNextAnimation, standalone, touchScreen } from "../motion";

const PULL = 72;          // px to pull before letting go reloads
const MAX = 120;

const mainEl = () => document.querySelector<HTMLElement>(".main");

/** A touch that belongs to something else: a field being typed in, or a box that scrolls by itself and isn't at its
 *  top (a long answer box, a conversation). */
function ownedElsewhere(target: EventTarget | null): boolean {
  for (let el = target instanceof Element ? target : null; el && el !== document.body; el = el.parentElement) {
    if (el.matches("input, textarea, select, [contenteditable='true']")) return true;
    if (el.scrollTop > 0 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) return true;
  }
  return false;
}

/** Window touch listeners for one gesture: `touchmove` can hold the page still (not passive), so it is only listened
 *  to once a gesture has started, never while the page simply scrolls. */
function track(onMove: (e: TouchEvent) => void, onEnd: (cancelled: boolean) => void) {
  const end = () => { stop(); onEnd(false); };
  const cancel = () => { stop(); onEnd(true); };
  const stop = () => {
    window.removeEventListener("touchmove", onMove);
    window.removeEventListener("touchend", end);
    window.removeEventListener("touchcancel", cancel);
  };
  window.addEventListener("touchmove", onMove, { passive: false });
  window.addEventListener("touchend", end);
  window.addEventListener("touchcancel", cancel);
  return stop;
}

/**
 * Pull the page down from its top and let go: the app reloads, fresh from the server (and its memory cache is gone).
 * Only in the installed app on a phone, which has no reload button; Safari's own pull to refresh works elsewhere.
 */
export function PullToRefresh() {
  const badge = useRef<HTMLDivElement>(null);
  const arrow = useRef<HTMLSpanElement>(null);
  const spinner = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!standalone() || !touchScreen()) return;
    let start: { x: number; y: number } | null = null;
    let pulling = false;
    let distance = 0;
    let reloading = false;

    const paint = (d: number, animate: boolean) => {
      const main = mainEl();
      const b = badge.current;
      if (!main || !b) return;
      main.style.transition = b.style.transition = animate ? "transform 0.35s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.2s" : "none";
      main.style.transform = d ? `translateY(${d}px)` : "";
      const shown = Math.min(1, d / PULL);
      b.style.opacity = String(shown);
      b.style.transform = `translate(-50%, ${Math.min(d, MAX) / 2 - 34}px) scale(${0.6 + 0.4 * shown})`;   // in the gap it opens
      if (arrow.current) arrow.current.style.transform = `rotate(${d >= PULL ? 180 : 0}deg)`;
      b.classList.toggle("armed", d >= PULL);
    };

    let stop = () => {};
    const onStart = (e: TouchEvent) => {
      stop();
      start = null;
      if (reloading || e.touches.length !== 1 || window.scrollY > 0 || ownedElsewhere(e.target)) return;
      start = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      pulling = false;
      stop = track(onMove, onEnd);
    };
    const onMove = (e: TouchEvent) => {
      if (!start) return;
      const dx = e.touches[0].clientX - start.x;
      const dy = e.touches[0].clientY - start.y;
      if (!pulling) {
        if (Math.abs(dx) > 10 || dy < -6 || window.scrollY > 0) { start = null; return; }
        if (dy < 10) return;
        pulling = true;
      }
      if (e.cancelable) e.preventDefault();
      distance = Math.max(0, MAX * (1 - Math.exp(-(dy - 10) / 200)));
      paint(distance, false);
    };
    const onEnd = (cancelled: boolean) => {
      if (!start || !pulling) { start = null; return; }
      start = null;
      pulling = false;
      if (distance >= PULL && !cancelled) {
        reloading = true;
        paint(56, true);
        if (spinner.current) spinner.current.hidden = false;
        if (arrow.current) arrow.current.hidden = true;
        window.setTimeout(() => window.location.reload(), 120);
      } else {
        paint(0, true);
      }
      distance = 0;
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    return () => { window.removeEventListener("touchstart", onStart); stop(); };
  }, []);

  return (
    <div className="ptr" ref={badge} aria-hidden="true" title={t("Recharger")}>
      <span ref={arrow} className="ptr-arrow"><IconDown size={20} /></span>
      <span ref={spinner} hidden><IconSpinner size={20} /></span>
    </div>
  );
}

/**
 * From the left edge of the screen, a finger drags the page away to go back, as in any iPhone app. Only in the
 * installed app (Safari has its own gesture), and only when there is a page to go back to.
 */
export function EdgeSwipeBack({ onBack }: { onBack: () => void }) {
  const shade = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!edgeSwipe()) return;
    let g: { x: number; y: number; t: number; dragging: boolean; lastX: number; lastT: number; v: number } | null = null;

    const canGoBack = () => ((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0;
    const set = (dx: number, animate: boolean) => {
      const main = mainEl();
      if (!main) return;
      const ease = "0.32s cubic-bezier(0.32, 0.72, 0, 1)";
      main.style.transition = animate ? `transform ${ease}` : "none";
      main.style.transform = dx ? `translateX(${dx}px)` : "";
      main.classList.toggle("swiping", dx > 0);
      if (shade.current) {
        shade.current.style.transition = animate ? `opacity ${ease}` : "none";
        shade.current.style.opacity = String(Math.max(0, 1 - dx / window.innerWidth) * (dx > 0 ? 1 : 0));
      }
    };

    let stop = () => {};
    const onStart = (e: TouchEvent) => {
      stop();
      const touch = e.touches[0];
      g = e.touches.length === 1 && touch.clientX <= EDGE && canGoBack()
        ? { x: touch.clientX, y: touch.clientY, t: e.timeStamp, dragging: false, lastX: touch.clientX, lastT: e.timeStamp, v: 0 }
        : null;
      if (g) stop = track(onMove, onEnd);
    };
    const onMove = (e: TouchEvent) => {
      if (!g) return;
      const touch = e.touches[0];
      const dx = touch.clientX - g.x;
      const dy = touch.clientY - g.y;
      if (!g.dragging) {
        if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { g = null; return; }
        if (dx < 8) return;
        g.dragging = true;
      }
      if (e.cancelable) e.preventDefault();
      const dt = e.timeStamp - g.lastT;
      if (dt > 0) g.v = (touch.clientX - g.lastX) / dt;
      g.lastX = touch.clientX;
      g.lastT = e.timeStamp;
      set(Math.max(0, dx), false);
    };
    const onEnd = (cancelled: boolean) => {
      if (!g?.dragging) { g = null; return; }
      const dx = g.lastX - g.x;
      const w = window.innerWidth;
      const go = !cancelled && (dx > w * 0.35 || (dx > 40 && g.v > 0.35));
      g = null;
      if (!go) { set(0, true); return; }
      set(w, !reducedMotion());
      window.setTimeout(() => { skipNextAnimation(); onBack(); }, reducedMotion() ? 0 : 230);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    return () => { window.removeEventListener("touchstart", onStart); stop(); };
  }, [onBack]);

  return <div className="swipe-shade" ref={shade} aria-hidden="true" />;
}

/** After a swipe back, the page that comes in sits in place (called once the new page is on screen). */
export function settleSwipe() {
  const main = mainEl();
  if (!main?.classList.contains("swiping")) return;
  main.style.transition = "none";
  main.style.transform = "";
  main.classList.remove("swiping");
  const shade = document.querySelector<HTMLElement>(".swipe-shade");
  if (shade) { shade.style.transition = "none"; shade.style.opacity = "0"; }
}
