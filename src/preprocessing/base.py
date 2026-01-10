import re
import warnings
from .unicode_escape import decode_unicode_escapes
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# 불필요한 공백 문자들
INVISIBLE_SPACES = [
    '\u00A0', '\u1680', '\u180E',
    '\u2000', '\u2001', '\u2002', '\u2003', '\u2004', '\u2005',
    '\u2006', '\u2007', '\u2008', '\u2009', '\u200A',
    '\u202F', '\u205F', '\u3000', '\uFEFF',
    '\u200B', '\u200C', '\u200D'
]

# 이모지 패턴
EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F"
    r"\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F700-\U0001F77F"
    r"\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0"
    r"\U0001F900-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002600-\U000026FF]",
    re.UNICODE
)

# 제거할 노이즈 패턴들
NOISE_PATTERNS = [
    r'https?://\S+',  # URLs
    r'www\.\S+',  # www URLs
    r'\S+@\S+\.\S+',  # emails
    r'```[\s\S]*?```',  # code blocks (triple backticks)
    r'`[^`]+`',  # inline code
    r'<[^>]+>',  # remaining HTML tags
    r'\[.*?\]\(.*?\)',  # markdown links
    r'!\[.*?\]\(.*?\)',  # markdown images
    r'#{1,6}\s',  # markdown headers
    r'\*{1,2}[^*]+\*{1,2}',  # bold/italic markers (keep text)
    r'Share this:.*',  # social sharing text
    r'Follow us on.*',  # follow prompts
    r'Subscribe to.*',  # subscribe prompts
    r'Copyright ©.*',  # copyright notices
    r'All rights reserved.*',  # rights notices
    r'Tags:.*',  # tag sections
    r'Categories:.*',  # category sections
    r'Related Posts.*',  # related posts
    r'Comments \(\d+\)',  # comment counts
    r'Leave a [Rr]eply.*',  # comment prompts
    r'\d+ min read',  # read time
    r'Reading time:.*',  # read time alt
    r'Views: \d+',  # view counts
    r'Likes?: \d+',  # like counts
]

# 제거할 HTML 태그들
REMOVE_TAGS = [
    "style", "meta", "link", "head", "noscript", "iframe",
    "form", "footer", "header", "nav", "aside", "img",
    "pre", "code", "script", "svg", "canvas", "video", "audio"
]

# 댓글 시스템 패턴
COMMENT_PATTERNS = [
    re.compile(r'\bdisqus\b', re.I),
    re.compile(r'\bgisqus\b', re.I),
    re.compile(r'\butterances\b', re.I),
    re.compile(r'\bcomments?\b', re.I),
]


def clean_html(html: str) -> str:
    if html is None:
        return ""

    html = re.sub(r'[""]', '"', html)
    html = re.sub(r"['']", "'", html)
    html = decode_unicode_escapes(html)

    for char in INVISIBLE_SPACES:
        html = html.replace(char, ' ')

    html = EMOJI_PATTERN.sub("", html)

    soup = BeautifulSoup(html, "lxml")

    for tag in soup(REMOVE_TAGS):
        tag.decompose()

    for div in soup.find_all(["div", "section"]):
        div_str = str(div.get("class", "")) + str(div.get("id", ""))
        if any(pattern.search(div_str) for pattern in COMMENT_PATTERNS):
            div.decompose()

    return soup.get_text(separator=" ", strip=True)


class BlogPostProcessor:
    # 짧은 라인이라도 보존해야 할 패턴들
    PRESERVE_SHORT_PATTERNS = [
        re.compile(r'^\d+\.'),           # 번호 목록 (1., 2., ...)
        re.compile(r'^[가-힣]{2,}:'),    # 한글 레이블 (예: 결론:)
        re.compile(r'^[A-Z][a-z]+:'),    # 영문 레이블 (예: Note:)
        re.compile(r'^\*\s'),            # 불릿 포인트
        re.compile(r'^-\s'),             # 대시 목록
        re.compile(r'^•\s'),             # 불릿 기호
    ]

    MIN_LINE_LENGTH = 10

    def __init__(self, min_line_length: int = 10):
        self.noise_patterns = [re.compile(p, re.I) for p in NOISE_PATTERNS]
        self.min_line_length = min_line_length

    def _should_preserve_line(self, line: str) -> bool:
        """짧은 라인 중 보존해야 하는지 판단"""
        if len(line) > self.min_line_length:
            return True

        # 특정 패턴은 짧아도 보존
        for pattern in self.PRESERVE_SHORT_PATTERNS:
            if pattern.match(line):
                return True

        return False

    def process(self, text: str) -> str:
        if not text:
            return ""

        text = clean_html(text)

        for pattern in self.noise_patterns:
            text = pattern.sub('', text)

        text = re.sub(r'\n{2,}', '\n', text)
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\s+([.,!?:;])', r'\1', text)
        text = re.sub(r'([.,!?])\1+', r'\1', text)

        text = text.replace('"', "'").replace('"', "'").replace('"', "'")
        text = text.replace("'", "'").replace("'", "'")

        text = re.sub(r'[^\w\s가-힣.,!?:;\'\-()（）]', '', text)

        text = re.split(r"--\d+share", text, flags=re.I)[-1]
        text = re.split(r'published in', text, flags=re.I)[0]
        text = re.split(r'share this article', text, flags=re.I)[0]
        text = re.split(r'about the author', text, flags=re.I)[0]

        lines = [line.strip() for line in text.split('\n') if line.strip()]
        lines = [line for line in lines if self._should_preserve_line(line)]
        text = ' '.join(lines)

        text = re.sub(r'\s+', ' ', text).strip()

        return text

