import { useEffect, useRef } from "react";
import { cache } from "../cache";
import { t } from "../i18n";
import { IconDown, IconSpinner } from "../icons";
import { EDGE, edgeSwipe, haptic, pageBelow, reducedMotion, skipNextAnimation, standalone, swipeBackAllowed, touchScreen, update } from "../motion";

const PULL = 72;          // px to pull before letting go refreshes
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
 * Pull the page down from its top and let go: what the page shows is asked for again, in place, the spinner turning
 * until it is back (nothing blanks or reloads, as in an iPhone app). Only in the installed app on a phone; Safari's
 * own pull to refresh works elsewhere.
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
    let armed = false;

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
      if (edgeSwipe() && e.touches[0].clientX <= EDGE) return;       // the left edge belongs to the swipe back
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
      if (distance >= PULL && !armed) haptic();
      armed = distance >= PULL;
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
        // a new version is waiting (sw.js): this pull takes it
        if (update.ready) { window.setTimeout(() => window.location.reload(), 120); return; }
        const started = Date.now();
        cache.refreshShown().finally(() => {
          // the spinner turns at least a moment, so that a quick answer still reads as "refreshed"
          window.setTimeout(() => {
            reloading = false;
            if (spinner.current) spinner.current.hidden = true;
            if (arrow.current) arrow.current.hidden = false;
            paint(0, true);
          }, Math.max(0, 450 - (Date.now() - started)));
        });
      } else {
        paint(0, true);
      }
      distance = 0;
      armed = false;
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
 * From the left edge of the screen, a finger drags the page away to go back, as in any iPhone app: the page below
 * (a still copy kept when this one was opened, motion.ts) slides back in under it, dimmed, a third of the way behind.
 * Only in the installed app (Safari has its own gesture), and only when there is a page to go back to.
 */
export function EdgeSwipeBack({ onBack }: { onBack: () => void }) {
  const shade = useRef<HTMLDivElement>(null);
  const under = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!edgeSwipe()) return;
    let g: { x: number; y: number; t: number; dragging: boolean; lastX: number; lastT: number; v: number } | null = null;

    const index = () => (window.history.state as { idx?: number } | null)?.idx ?? 0;
    const showBelow = () => {
      const box = under.current;
      const copy = pageBelow(index() - 1);
      if (!box || !copy) return;
      const node = copy.node.cloneNode(true) as HTMLElement;
      node.style.transform = `translateY(${-copy.scrollY}px)`;
      box.replaceChildren(node);
      box.hidden = false;
    };
    const set = (dx: number, animate: boolean) => {
      const main = mainEl();
      if (!main) return;
      const ease = "0.32s cubic-bezier(0.32, 0.72, 0, 1)";
      const w = window.innerWidth;
      main.style.transition = animate ? `transform ${ease}` : "none";
      main.style.transform = dx ? `translateX(${dx}px)` : "";
      main.classList.toggle("swiping", dx > 0);
      if (shade.current) {
        shade.current.style.transition = animate ? `opacity ${ease}` : "none";
        shade.current.style.opacity = String(Math.max(0, 1 - dx / w) * (dx > 0 ? 1 : 0));
      }
      if (under.current && !under.current.hidden) {
        under.current.style.transition = animate ? `transform ${ease}` : "none";
        under.current.style.transform = `translateX(${-0.3 * Math.max(0, w - dx)}px)`;
      }
      // the small title at the top belongs to the page going away: it fades with it
      const bar = document.querySelector<HTMLElement>(".title-bar");
      if (bar) {
        bar.style.transition = animate ? `opacity ${ease}` : "none";
        bar.style.opacity = dx > 0 ? String(Math.max(0, 1 - (dx / w) * 1.6)) : "";
      }
    };

    let stop = () => {};
    const onStart = (e: TouchEvent) => {
      stop();
      const touch = e.touches[0];
      // one swipe at a time: a second one while the first is still taking the page back would go back twice
      g = !leaving && e.touches.length === 1 && touch.clientX <= EDGE && swipeBackAllowed()
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
        showBelow();
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
      const go = !cancelled && swipeBackAllowed() && (dx > w * 0.35 || (dx > 40 && g.v > 0.35));
      g = null;
      const back = () => { set(0, true); window.setTimeout(hideBelow, 340); };
      if (!go) { back(); return; }
      leaving = true;
      set(w, !reducedMotion());
      window.setTimeout(() => {
        skipNextAnimation();
        onBack();
        // the history had nothing to go back to after all: the page comes back instead of staying off screen
        window.setTimeout(() => { if (leaving) { leaving = false; back(); } }, 600);
      }, reducedMotion() ? 0 : 230);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    return () => { window.removeEventListener("touchstart", onStart); stop(); };
  }, [onBack]);

  return <>
    <div className="swipe-under" ref={under} aria-hidden="true" hidden />
    <div className="swipe-shade" ref={shade} aria-hidden="true" />
  </>;
}

let leaving = false;   // a swipe let go far enough: the page is on its way back

/** The copy of the page below goes once the real one is back (or the swipe was let go). */
function hideBelow() {
  const box = document.querySelector<HTMLElement>(".swipe-under");
  if (!box || mainEl()?.classList.contains("swiping")) return;
  box.hidden = true;
  box.replaceChildren();
  box.style.transform = "";
}

/** After a swipe back, the page that comes in sits in place (called once the new page is on screen). */
export function settleSwipe() {
  const main = mainEl();
  if (!main?.classList.contains("swiping")) return;
  leaving = false;
  const bar = document.querySelector<HTMLElement>(".title-bar");
  if (bar) { bar.style.transition = "none"; bar.style.opacity = ""; }
  main.style.transition = "none";
  main.style.transform = "";
  main.classList.remove("swiping");
  const shade = document.querySelector<HTMLElement>(".swipe-shade");
  if (shade) { shade.style.transition = "none"; shade.style.opacity = "0"; }
  hideBelow();
}
