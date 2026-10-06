import { useEffect, useRef } from "react";
import type { ItemDetail } from "../api";

declare global {
  interface Window {
    twttr?: { widgets: { createTweet: (id: string, el: HTMLElement, opts: Record<string, unknown>) => Promise<unknown> } };
  }
}

let twitterScript: Promise<void> | null = null;
function loadTwitter(): Promise<void> {
  if (window.twttr?.widgets) return Promise.resolve();
  twitterScript ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://platform.twitter.com/widgets.js";
    s.async = true;
    s.onload = () => {
      const wait = () => (window.twttr?.widgets ? resolve() : setTimeout(wait, 50));
      wait();
    };
    s.onerror = () => reject(new Error("widgets.js indisponible"));
    document.head.appendChild(s);
  });
  return twitterScript;
}

function TweetEmbed({ id }: { id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = "";
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    loadTwitter()
      .then(() => window.twttr!.widgets.createTweet(id, el, { theme: dark ? "dark" : "light", dnt: true, align: "center" }))
      .catch(() => { /* l'embed est un bonus : la fiche reste lisible sans */ });
  }, [id]);
  return <div ref={ref} />;
}

export function Embed({ item }: { item: ItemDetail }) {
  const m = item.metadata || {};
  if (item.kind === "tweet" && m.tweet_id) return <div className="embed"><TweetEmbed id={m.tweet_id} /></div>;
  if (item.kind === "youtube" && m.video_id) {
    return (
      <div className="embed">
        <iframe src={`https://www.youtube-nocookie.com/embed/${m.video_id}`} title={item.title ?? "Vidéo YouTube"}
                allow="accelerometer; encrypted-media; picture-in-picture" allowFullScreen loading="lazy" />
      </div>
    );
  }
  if (item.file_url && item.kind === "image") return <div className="embed"><img src={item.file_url} alt={item.title ?? ""} /></div>;
  if (item.file_url && item.kind === "video") return <div className="embed"><video src={item.file_url} controls preload="metadata" /></div>;
  if (item.file_url && item.kind === "audio") return <div className="embed"><audio src={item.file_url} controls preload="metadata" /></div>;
  return null;
}
