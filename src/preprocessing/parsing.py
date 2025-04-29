import requests
from requests.adapters import HTTPAdapter
from trafilatura import extract

# 커스텀 세션 구성
session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

def parse_article_text_from_url(url):
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        downloaded = response.text
    except requests.RequestException as e:
        print(f"[ERROR] Failed to download URL {url}: {e}")
        return ""

    # 본문 추출
    text = extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        with_metadata=False,
        include_formatting=False,
        include_links=False,
        include_images=False
    )
    return text or ""