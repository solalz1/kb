"""Is the title a source gives a real title?

An article, a video or a paper has a headline worth keeping word for word. Many pages don't: LinkedIn, X, Threads,
Instagram or Facebook give the first words of the post, cut off, with the author's name after a « | »; a file has its
file name; some pages say « LinkedIn » or « Accueil ». Those go to Claude, who keeps a real headline (without the
site's name) and writes one otherwise (llm.enrich, llm.retitle)."""

import re

MAX_LENGTH = 110                        # past this, a "title" is a paragraph
_CUT = re.compile(r"(…|\.{3})")         # cut off mid-sentence
_URL = re.compile(r"^(https?://|www\.)\S+$", re.I)
_FILE = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|odt|rtf|txt|md|csv|json|html?|png|jpe?g|gif|webp|heic|mp[34]|m4a|mov"
                   r"|wav)$", re.I)
# "Jane Doe on X: …", "Post de Jane Doe sur LinkedIn"
_SOCIAL = re.compile(r"\b(on|sur)\s+(x|twitter|threads|instagram|linkedin|facebook|tiktok|reddit|bluesky)\b", re.I)
# a site or an author added after the headline: "… | Medium", "… | Jane Doe", "… · Substack", "… - YouTube"
# (a dash alone is common in real headlines, and in GitHub's "owner/repo — description")
_SUFFIX = re.compile(r"\s(\||·)\s[^|·]{1,60}$")
_SITE_SUFFIX = re.compile(r"\s[-–—]\s(youtube|medium|substack|linkedin|x|twitter|reddit|wikipedia|instagram|"
                          r"facebook|threads|tiktok|hacker news)$", re.I)
_GENERIC = {"linkedin", "post", "posts", "home", "accueil", "untitled", "sans titre", "x", "twitter", "instagram",
            "facebook", "threads", "reddit", "tiktok", "youtube", "medium", "substack", "article", "document", "page",
            "login", "connexion", "sign in", "sign up", "403 forbidden", "404 not found"}


def looks_like_title(title: str | None) -> bool:
    """True for a clean headline the KB keeps as it is; False when Claude should look at it (and maybe write one)."""
    t = (title or "").strip()
    if not t or len(t) > MAX_LENGTH or "\n" in t:
        return False
    if t.lower().strip(" .") in _GENERIC:
        return False
    quoted = ":" in t and t.count('"') + t.count("“") + t.count("«") >= 2      # Jane Doe: "the post…"
    return not (_CUT.search(t) or _URL.match(t) or _FILE.search(t) or _SOCIAL.search(t) or _SUFFIX.search(t)
                or _SITE_SUFFIX.search(t) or quoted)
