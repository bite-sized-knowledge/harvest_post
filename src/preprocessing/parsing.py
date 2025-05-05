import requests
from requests.adapters import HTTPAdapter
from trafilatura import extract
from fake_useragent import UserAgent
import ssl

# 세션 구성 (커넥션 풀 포함)
session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

def parse_article_text_from_url(url):
    try:
        user_agent = UserAgent().random
    except Exception:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"

    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko,en-US;q=0.9,en;q=0.8',
        'Connection': 'close'
    }

    try:
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.SSLError as ssl_error:
        print(f"[SSL WARNING] Retrying with verify=False for URL {url}: {ssl_error}")
        try:
            response = session.get(url, headers=headers, timeout=10, verify=False)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"[ERROR] Failed to download URL {url} with SSL bypass: {e}")
            return ""
    except requests.RequestException as e:
        print(f"[ERROR] Failed to download URL {url}: {e}")
        return ""

    response.encoding = 'utf-8'
    downloaded = response.text

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