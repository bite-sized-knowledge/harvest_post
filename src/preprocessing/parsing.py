import re
import json
import requests
from bs4 import BeautifulSoup
from trafilatura import extract
from trafilatura.settings import use_config
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
import backoff
from html import unescape

try:
    UA = UserAgent()
except Exception:
    UA = None

# 세션 구성 (커넥션 풀 포함)
session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

cfg = use_config()
cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")  # signal 사용 안함

def transform_url(url):
    # only for Naver D2 Blog
    id = url.split("/")[-1]
    ret = f"https://d2.naver.com/api/v1/contents/{id}"
    return ret


def generate_user_agent():
    if UA:
        try:
            return UA.random
        except Exception:
            pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"


def extract_text_from_json_script(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script")

    for script in scripts:
        if script.has_attr("type") and "json" in script["type"]:
            try:
                data = json.loads(script.string)
                # 재귀적으로 <p>, <div>, <article> 태그가 있는 HTML 문자열을 찾는다
                def find_html(obj):
                    if isinstance(obj, dict):
                        for v in obj.values():
                            result = find_html(v)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_html(item)
                            if result:
                                return result
                    elif isinstance(obj, str):
                        if re.search(r'<(p|div|article)[\s>]', obj):
                            return obj
                    return None

                html_content = find_html(data)
                if html_content:
                    text = BeautifulSoup(unescape(html_content), "lxml").get_text(separator="\n")
                    return text.strip()
            except Exception:
                continue

        elif script.has_attr("id") and script.string and re.match(r'__\w+__', script["id"]):
            try:
                data = json.loads(script.string)
                html_candidate = json.dumps(data)
                match = re.search(r'(<p>.*?</p>)', html_candidate)
                if match:
                    raw_html = unescape(match.group(1))
                    text = BeautifulSoup(raw_html, "lxml").get_text(separator="\n")
                    return text.strip()
            except Exception:
                continue
    return ""


@backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=3)
def extract_html_via_requests(url: str, blog_id: int, user_agent: str, timeout: int = 10) -> str:
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko,en-US;q=0.9,en;q=0.8',
        'Connection': 'close'
    }

    response = session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = 'utf-8'

    if blog_id == 6:
        ret = json.loads(response.text)
        return ret['postHtml']

    return response.text

def parse_article_text_from_url(url: str, blog_id: int) -> str:
    user_agent = generate_user_agent()

    if blog_id == 6: #Naver D2
        url = transform_url(url)
    print(f"[INFO] Fetching {url}")

    html = ""
    try:
        html = extract_html_via_requests(url, blog_id, user_agent)
    except Exception as e:
        print(f"[REQUEST ERROR] {url}: {e}")
        return ""

    extracted = extract_text_from_json_script(html)
    if extracted:
        print(f"[INFO] Parsed from <script> JSON content for {url}")
        return extracted

    text = extract(
        html,
        include_comments=False,
        include_tables=False,
        with_metadata=False,
        include_formatting=False,
        include_links=False,
        include_images=False,
        config=cfg
    )

    print(text)

    return text or ""