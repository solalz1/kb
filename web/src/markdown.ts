import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: false });

// [3] -> pastille cliquable ; [3][5] -> deux pastilles. Les liens Markdown [texte](url) ne sont pas touchés.
export function renderAnswer(md: string): string {
  const withCites = md.replace(/\[(\d{1,2})\](?!\()/g, '<sup class="cite" data-n="$1" role="button" tabindex="0">$1</sup>');
  const html = marked.parse(withCites, { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["data-n", "role", "tabindex", "target"] });
}

export function renderMarkdown(md: string): string {
  const html = marked.parse(md, { async: false }) as string;
  const clean = DOMPurify.sanitize(html);
  return clean.replace(/<a /g, '<a target="_blank" rel="noopener noreferrer" ');
}
