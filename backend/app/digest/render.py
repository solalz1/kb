"""Digest rendering: Markdown (app export, Claude connector, e-mail) and a small HTML e-mail."""

from __future__ import annotations

import html
import logging
import re
import smtplib
from datetime import date
from email.message import EmailMessage

from ..config import get_settings

log = logging.getLogger(__name__)

SECTIONS = [
    ("essentiel", "L'essentiel", "les 2 à 4 nouvelles tech les plus importantes, utiles à tout ingénieur, même hors de ses sujets"),
    ("industrie", "Industrie et produits", "entreprises, lancements de produits, levées de fonds, régulation, marché"),
    ("modeles", "Modèles et labs", "nouveaux modèles, annonces des laboratoires, résultats d'évaluation"),
    ("voix", "Tes ingénieurs", "ce qu'ont publié ou dit les personnes qu'il suit, quand c'est substantiel"),
    ("recherche", "Recherche", "papiers et résultats de recherche"),
    ("ingenierie", "Ingénierie et outils", "dépôts, outils, techniques concrètes, retours d'expérience"),
]
SECTION_IDS = [s[0] for s in SECTIONS]
SECTION_LABELS = {s[0]: s[1] for s in SECTIONS}
DIFFICULTY = {1: "accessible", 2: "intermédiaire", 3: "ambitieux"}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def french_date(d: date, weekday: bool = True) -> str:
    text = f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    return f"{DAYS[d.weekday()]} {text}" if weekday else text


def title_for(digest: dict) -> str:
    start, end = digest["period_start"], digest["period_end"]
    if digest["kind"] == "weekly":
        return f"Semaine du {start.day} {MONTHS[start.month - 1]} au {french_date(end, weekday=False)}"
    return f"Digest du {french_date(start)}"


def _md(text: str | None) -> str:
    return (text or "").replace("\n", " ").strip()


def _url(url: str) -> str:
    """Parentheses and spaces would end a Markdown link early."""
    return (url or "").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def entry_markdown(e: dict) -> str:
    meta = " · ".join(x for x in [e.get("source"), e.get("author")] if x)
    line = f"- **[{_md(e['title'])}]({_url(e['url'])})** : {_md(e.get('summary'))}"
    if e.get("why"):
        line += f" *Pour toi : {_md(e['why'])}*"
    return line + (f" ({meta})" if meta else "")


def project_markdown(p: dict, refs: dict[str, dict] | None = None, n: int | None = None) -> str:
    head = f"### {f'{n}. ' if n else ''}{p['title']}"
    tags = " · ".join(x for x in [p.get("effort"), DIFFICULTY.get(p.get("difficulty"), ""), p.get("kind")] if x)
    lines = [head, f"*{tags}*" if tags else "", _md(p.get("pitch"))]
    if p.get("why_now"):
        links = [f"[{_md(refs[r]['title'])}]({_url(refs[r]['url'])})" for r in p.get("refs") or [] if refs and r in refs]
        lines.append(f"- **Pourquoi maintenant** : {_md(p['why_now'])}" + (f" ({', '.join(links)})" if links else ""))
    if p.get("learn"):
        lines.append(f"- **Ce que tu apprends** : {_md(p['learn'])}")
    if p.get("plan"):
        lines.append("- **Plan** :\n" + "\n".join(f"  {i}. {_md(s)}" for i, s in enumerate(p["plan"], 1)))
    if p.get("deliverable"):
        lines.append(f"- **Livrable** : {_md(p['deliverable'])}")
    return "\n".join(x for x in lines if x)


def markdown(digest: dict) -> str:
    data = digest.get("data") or {}
    entries = data.get("entries") or []
    refs = {e["key"]: e for e in entries}
    parts = [f"# {title_for(digest)}"]
    if digest.get("headline"):
        parts.append(f"*{_md(digest['headline'])}*")
    if data.get("trends"):
        parts.append("## La semaine en bref\n" + "\n".join(f"- {_md(t['text'])}" for t in data["trends"]))
    for sid, label, _ in SECTIONS:
        rows = [e for e in entries if e.get("section") == sid]
        if rows:
            parts.append(f"## {label}\n" + "\n".join(entry_markdown(e) for e in rows))
    if data.get("projects"):
        parts.append("## Projets pour cette semaine\n\n" + "\n\n".join(
            project_markdown(p, refs, i) for i, p in enumerate(data["projects"], 1)))
    if not entries and not data.get("projects"):
        parts.append("Rien de marquant dans tes sources sur cette période.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', text)   # already escaped
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)


def to_html(md: str) -> str:
    """Just enough Markdown for our own digests: headings, paragraphs, bullet and numbered lists, links, emphasis."""
    out, open_list = [], None
    for raw in md.splitlines():
        line = raw.rstrip()
        bullet = re.match(r"^\s*- (.*)", line)
        number = re.match(r"^\s+\d+\. (.*)", line)
        if open_list and not (bullet or number):
            out.append(f"</{open_list}>")
            open_list = None
        if not line.strip():
            continue
        if m := re.match(r"^(#{1,3}) (.*)", line):
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif bullet or number:
            tag = "ol" if number else "ul"
            if open_list != tag:
                if open_list:
                    out.append(f"</{open_list}>")
                out.append(f"<{tag}>")
                open_list = tag
            out.append(f"<li>{_inline((bullet or number).group(1))}</li>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if open_list:
        out.append(f"</{open_list}>")
    body = "\n".join(out)
    return ('<div style="font-family:Georgia,serif;font-size:16px;line-height:1.55;color:#1b2430;max-width:640px">'
            f"{body}</div>")


def email_enabled() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.digest_email_to)


def send_email(digest: dict) -> bool:
    s = get_settings()
    if not email_enabled():
        return False
    md = digest.get("content") or markdown(digest)
    link = s.public_base_url.rstrip("/") + f"/digest/{digest['id']}" if s.public_base_url else None
    if link:
        md += f"\n\n[Ouvrir dans l'app]({_url(link)})"
    msg = EmailMessage()
    msg["Subject"] = " ".join(f"{title_for(digest)} · {digest.get('headline') or 'ta veille tech'}".split())[:180]
    msg["From"] = s.digest_email_from or s.smtp_user or s.digest_email_to
    msg["To"] = s.digest_email_to
    msg.set_content(md)
    msg.add_alternative(to_html(md), subtype="html")
    if s.smtp_port == 465:
        smtp_cm = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30)
    else:
        smtp_cm = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
    with smtp_cm as smtp:
        if s.smtp_port != 465:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    return True
