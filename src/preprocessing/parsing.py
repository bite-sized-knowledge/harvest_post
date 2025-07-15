import os
import re
import json
import requests
from bs4 import BeautifulSoup
from tempfile import mkdtemp
from trafilatura import extract
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
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

def generate_user_agent():
    if UA:
        try:
            return UA.random
        except Exception:
            pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"

def is_dynamic_page(html: str) -> bool:
    soup = BeautifulSoup(html, 'html.parser')
    score = 0

    # 1. SPA 프레임워크 감지 (높은 가중치)
    dynamic_indicators = [
        "__NEXT_DATA__", "window.__INITIAL_STATE__", "data-reactroot",
        "id=\"app\"", "id=\"root\"", "ng-app", "vue", "window.__NUXT__",
        "data-vue-meta", "data-server-rendered", "ReactDOM"
    ]
    if any(keyword in html for keyword in dynamic_indicators):
        score += 2

    # 2. 자바스크립트 기반 렌더링 지표
    script_tags = soup.find_all('script')
    script_count = len(script_tags)
    clean_text = re.sub(r'<[^>]+>', '', html).strip()
    text_len = len(clean_text)

    if text_len > 1000:
        score -= 1

    # 2-1. 스크립트 과다 및 텍스트 콘텐츠 부족
    if script_count >= 5 and text_len < 100:
        score += 2

    # 2-2. 인라인 스크립트 또는 대량의 스크립트 콘텐츠
    for script in script_tags:
        if script.string and len(script.string) > 200:
            score += 1
            break

    # 3. 주요 콘텐츠 영역 부재 (기존 로직)
    has_main_content_tag = soup.find('main') or soup.find('article')
    has_common_content_class = soup.find(class_=re.compile(r'(content|body|wrapper)', re.IGNORECASE))
    if not has_main_content_tag and not has_common_content_class and text_len < 300:
        score += 1

    # 4.빈 콘텐츠 영역 감지
    # 'contents'와 같은 클래스를 가진 영역이 있지만, 그 안에 텍스트 콘텐츠가 없는 경우
    content_area = soup.find(class_=re.compile(r'(content|body|wrapper)', re.IGNORECASE))
    if content_area and not content_area.get_text(strip=True) and script_count > 0:
        score += 2  

    return score >= 2 # 임계값을 2로 낮춤. 이 조건만 만족해도 동적 페이지로 판단하도록 유연하게 변경

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
def extract_html_via_requests(url: str, user_agent: str, timeout: int = 10) -> str:
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko,en-US;q=0.9,en;q=0.8',
        'Connection': 'close'
    }

    response = session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = 'utf-8'
    return response.text

def extract_html_via_selenium(url: str, user_agent: str) -> str:
    options = webdriver.ChromeOptions()
    options.binary_location = os.environ.get("CHROME_BIN", "/opt/chrome/chrome")
    temp_dir = mkdtemp()
    for opt in ["--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1920,1080",
                "--disable-dev-shm-usage", "--disable-dev-tools", "--no-zygote"]:
        options.add_argument(opt)
    options.add_argument(f"--user-data-dir={temp_dir}")
    options.add_argument(f"--user-agent={user_agent}")

    service = ChromeService("/opt/chromedriver")
    driver = webdriver.Chrome(options=options, service=service)

    try:
        driver.set_page_load_timeout(60)
        driver.get(url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
        return driver.page_source
    except Exception as e:
        print(f"[SELENIUM ERROR] Failed to fetch {url}: {e}")
    finally:
        driver.quit()

    return ""

def parse_article_text_from_url(url: str) -> str:
    user_agent = generate_user_agent()
    print(f"[INFO] Fetching {url}")

    html = ""
    try:
        html = extract_html_via_requests(url, user_agent)
    except Exception as e:
        print(f"[REQUEST ERROR] {url}: {e}")
        return ""

    extracted = extract_text_from_json_script(html)
    if extracted:
        print(f"[INFO] Parsed from <script> JSON content for {url}")
        return extracted


    if is_dynamic_page(html):
        print(f"[INFO] Dynamic page detected: {url}")
        html = extract_html_via_selenium(url, user_agent)
        if not html:
            return ""

    text = extract(
        html,
        include_comments=False,
        include_tables=False,
        with_metadata=False,
        include_formatting=False,
        include_links=False,
        include_images=False
    )

    return text or ""