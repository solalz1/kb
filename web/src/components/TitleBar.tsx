import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { t } from "../i18n";
import { IconBack } from "../icons";
import { reducedMotion } from "../motion";

const WIDE = "(min-width: 900px)";

/**
 * Phone: once the page's own title has scrolled away under the top of the screen, a glass bar shows it small there,
 * as an iPhone app's large title folds into its navigation bar, with the page's way back (« ‹ Veille ») when it has
 * one. A tap on the title goes back to the top of the page. Nothing on a computer (the sidebar names the page) or
 * while the title is still in view.
 */
export function TitleBar() {
  const location = useLocation();
  const bar = useRef<HTMLDivElement>(null);
  const [title, setTitle] = useState("");
  const [back, setBack] = useState("");
  const [shown, setShown] = useState(false);

  // another page: its title starts in view
  useLayoutEffect(() => { setShown(false); }, [location.key]);

  useEffect(() => {
    let frame = 0;
    const check = () => {
      frame = 0;
      const heading = document.querySelector<HTMLElement>(".main h1");
      if (!heading || !bar.current || window.matchMedia(WIDE).matches) { setShown(false); return; }
      const gone = heading.getBoundingClientRect().bottom < bar.current.getBoundingClientRect().bottom - 4;
      if (gone) {
        setTitle(heading.textContent?.trim() ?? "");
        setBack(document.querySelector<HTMLElement>(".main .back")?.textContent?.trim() ?? "");
      }
      setShown(gone);
    };
    const onScroll = () => { if (!frame) frame = window.requestAnimationFrame(check); };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    check();
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      window.cancelAnimationFrame(frame);
    };
  }, [location.key]);

  return (
    <div ref={bar} className={`title-bar${shown ? " shown" : ""}`}>
      {shown && back && (
        <button type="button" className="compact-back" onClick={() => document.querySelector<HTMLElement>(".main .back")?.click()}>
          <IconBack size={18} /><span>{back}</span>
        </button>
      )}
      {shown && title && (
        <button type="button" className="compact-title" aria-label={t("{title} : revenir en haut", { title })}
                onClick={() => window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" })}>
          {title}
        </button>
      )}
    </div>
  );
}
