import requests
from requests.adapters import HTTPAdapter
from trafilatura import extract
from fake_useragent import UserAgent
import ssl

# 커스텀 세션 구성
session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

# SSL 검증 우회 (전체 환경에 적용됨)
ssl._create_default_https_context = ssl._create_unverified_context

def parse_article_text_from_url(url):
    # 동적으로 User-Agent 생성
    user_agent = UserAgent()
    headers = {
        'User-Agent': user_agent.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Connection': 'close'
    }

    try:
        response = session.get(url, headers=headers, timeout=10, verify=False)
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