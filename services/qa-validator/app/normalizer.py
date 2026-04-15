import html
import re
from bs4 import BeautifulSoup


WHITESPACE_RE = re.compile(r"\s+")
SCRIPT_STYLE_RE = re.compile(r"(script|style|noscript|svg|canvas)", re.I)


def html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "lxml")

    for tag in soup.find_all(SCRIPT_STYLE_RE):
        tag.decompose()

    for tag in soup.find_all(["br", "p", "li", "div", "tr", "section", "article", "h1", "h2", "h3", "h4"]):
        tag.append("\n")

    text = soup.get_text(separator=" ", strip=True)
    return normalize_text(text)


def normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\x00", " ")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def truncate_for_llm(text: str, max_chars: int = 12000) -> str:
    return normalize_text(text)[:max_chars]
