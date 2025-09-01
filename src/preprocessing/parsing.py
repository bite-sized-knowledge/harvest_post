import re
from bs4 import BeautifulSoup
from trafilatura import extract
from html import unescape

def _clean_text(text: str) -> str:
    """공백/개행 정리 및 잡스러운 라인 제거(너무 짧은 라인 등은 과도 제거 방지)."""
    if not text:
        return ""
    # HTML 엔티티 해제
    text = unescape(text)
    # \xa0 -> space
    text = text.replace("\xa0", " ")
    # 라인 단위 정리: 앞뒤 공백 제거 + 빈 라인 축약
    lines = [ln.strip() for ln in text.splitlines()]
    # 연속 빈 라인은 하나로 축약
    out, prev_blank = [], False
    for ln in lines:
        is_blank = (ln == "")
        if is_blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = is_blank
    # 다중 스페이스 축약
    text = "\n".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()

def parse_article_text_from_html(html: str) -> str:
    """
    원문 HTML 문자열에서 본문 텍스트만 추출한다.
    1) JSON 스크립트(SSR/하이드레이션)에 본문이 포함된 경우 우선 추출
    2) trafilatura로 본문 추출
    3) 실패 시 BeautifulSoup로 스크립트/스타일/내비 제거 후 텍스트화
    """
    if not html or not isinstance(html, str):
        return ""

    # 1) <script type="application/ld+json"> 등 JSON 내부의 HTML 스니펫 우선 탐색
    try:
        soup = BeautifulSoup(html, "lxml")
        scripts = soup.find_all("script")
        for sc in scripts:
            # JSON 유형 혹은 프레임워크 하이드레이션 id 패턴
            if (sc.has_attr("type") and "json" in sc["type"]) or (sc.has_attr("id") and re.match(r"__\w+__", sc["id"])):
                if sc.string:
                    # 문자열 내에 <p|div|article> 태그 패턴이 있으면 본문 후보로 간주
                    m = re.search(r'(<(p|div|article)[^>]*>.*?</\2>)', sc.string, flags=re.DOTALL|re.IGNORECASE)
                    if m:
                        frag = unescape(m.group(1))
                        frag_soup = BeautifulSoup(frag, "lxml")
                        # br/li 개행 보존
                        for br in frag_soup.find_all("br"):
                            br.replace_with("\n")
                        text = frag_soup.get_text(separator="\n")
                        text = _clean_text(text)
                        if text and len(text.split()) > 15:  # 너무 짧으면 패스
                            return text
    except Exception:
        pass

    # 2) trafilatura로 본문 추출
    try:
        text = extract(
            html,
            include_comments=False,
            include_tables=False,
            with_metadata=False,
            include_formatting=False,
            include_links=False,
            include_images=False,
            favor_recall=True,   # 회수율 우선(짧은 글 누락 방지)
        )
        text = _clean_text(text)
        if text:
            return text
    except Exception:
        pass

    # 3) BeautifulSoup fallback: 불필요 영역 제거 후 텍스트화
    try:
        soup = BeautifulSoup(html, "lxml")

        # 제거 대상 태그들
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas", "form"]):
            tag.decompose()
        # 구조적 잡영역 제거(있으면)
        for sel in ["header", "footer", "nav", "aside"]:
            for node in soup.select(sel):
                node.decompose()

        # li/br 개행 보존
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for li in soup.find_all("li"):
            txt = li.get_text(" ", strip=True)
            li.string = txt + "\n"

        text = soup.get_text(separator="\n")
        return _clean_text(text)
    except Exception:
        return ""