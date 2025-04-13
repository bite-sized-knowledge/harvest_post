import re
from .unicode_escape import decode_unicode_escapes
from bs4 import BeautifulSoup

def clean_html(html: str) -> str:
    """
    입력된 HTML에서 본문 텍스트만 추출하고 필요한 전처리를 수행
    """
    if html is None:
        return ""

    html = html.lower() 
    html = re.sub(r"'", "", html)
    html = re.sub(r'[“”]', '"', html)
    html = re.sub(r'[\u200B-\u200D\uFEFF]', '', html)
    html = decode_unicode_escapes(html)
    invisible_spaces = [
        '\u00A0',  # non-breaking space
        '\u1680',  # ogham space mark
        '\u180E',  # mongolian vowel separator (deprecated but still exists)
        '\u2000', '\u2001', '\u2002', '\u2003', '\u2004', '\u2005',
        '\u2006', '\u2007', '\u2008', '\u2009', '\u200A',  # various en/em/thin spaces
        '\u202F',  # narrow no-break space
        '\u205F',  # medium mathematical space
        '\u3000',  # ideographic space (한자권 공백)
        '\uFEFF'   # zero width no-break space (BOM)
    ]
    for char in invisible_spaces:
        html = html.replace(char, ' ')

    emoji_pattern = re.compile(
        r"[\U0001F600-\U0001F64F"  # 이모지
        r"\U0001F300-\U0001F5FF"  # 기호 및 픽토그램
        r"\U0001F680-\U0001F6FF"  # 운송 및 기계
        r"\U0001F700-\U0001F77F]"  # 추가 범위
        , re.UNICODE
    )
    html = emoji_pattern.sub("", html)

    # 1. HTML 파싱
    soup = BeautifulSoup(html, "lxml")

    # 2. 불필요한 태그 제거
    components = ["style", "meta", "link", "head", "noscript", "iframe", "form", "footer", "header", "nav", "aside","img", "pre"]
    for tag in soup(components):
        tag.decompose()
    
    # 3. 댓글 섹션 제거 (Disqus, Utterances 등)
    comment_patterns = [
        re.compile(r'\bdisqus\b', re.I),
        re.compile(r'\bgisqus\b', re.I),
        re.compile(r'\butterances\b', re.I)
    ]
    for div in soup.find_all("div"):
        if any(pattern.search(str(div)) for pattern in comment_patterns):
            div.decompose()

    return soup.get_text(strip=True)

class BlogPostProcessor:
    def process(self, text):
        text = clean_html(text)
        text = re.sub(r'\n{2,}', '', text)
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\s+([.,!?])', r'\1', text)
        text = re.sub(r'\'', '', text)
        text = text.replace('”', '"').replace("‘", "'").replace("’", "'").replace("“", '"')
        text = re.sub(r'"', '\'', text)
        text = re.sub(r'[^\S\n]{2,}', ' ', text)  
        text = re.sub(r'([.,!?])\1+', r'\1', text)

        return text

