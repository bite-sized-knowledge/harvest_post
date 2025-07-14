import os
import re
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tempfile import mkdtemp
from trafilatura import extract
from fake_useragent import UserAgent

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
        "data-vue-meta", "data-server-rendered", "ReactDOM" # ReactDOM 추가
    ]
    # BeautifulSoup 파싱 전에 원본 HTML에서 직접 검색하는 것이 효율적입니다.
    if any(keyword in html for keyword in dynamic_indicators):
        score += 3

    # 2. 자바스크립트 기반 렌더링 지표
    script_tags = soup.find_all('script')
    script_count = len(script_tags)

    # 2-1. 스크립트 과다 및 텍스트 콘텐츠 부족
    # HTML에서 태그를 제거한 순수 텍스트 길이 (공백 제거 후)
    clean_text = re.sub(r'<[^>]+>', '', html).strip()
    text_len = len(clean_text)

    # 스크립트가 많고 본문 텍스트가 매우 적으면 동적 페이지일 가능성이 높음
    # 실제 콘텐츠가 스크립트를 통해 로드될 가능성
    if script_count >= 5 and text_len < 100:
        score += 2

    # 2-2. 인라인 스크립트 또는 대량의 스크립트 콘텐츠
    # 대량의 인라인 스크립트 또는 스크립트 내부에 많은 로직이 있을 경우
    for script in script_tags:
        if script.string and len(script.string) > 200: # 스크립트 내용이 긴 경우
            score += 1
            break # 하나만 있어도 점수 부여

    # 3. 주요 콘텐츠 영역 부재 (보통 수준의 가중치)
    # 'main' 또는 'article' 태그가 없거나, 'content'와 유사한 클래스를 가진 요소가 없는 경우
    has_main_content_tag = soup.find('main') or soup.find('article')
    has_common_content_class = soup.find(class_=re.compile(r'(content|body|wrapper)', re.IGNORECASE))

    if not has_main_content_tag and not has_common_content_class and text_len < 300:
        # 본문 태그도 없고, 일반적인 콘텐츠 클래스도 없으며, 텍스트도 적은 경우
        score += 1


    # 최종 점수 기반 판단
    return score >= 3

def extract_html_via_requests(url: str, user_agent: str, timeout: int = 10) -> str:
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko,en-US;q=0.9,en;q=0.8',
        'Connection': 'close'
    }

    try:
        response = session.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return response.text
    except requests.exceptions.SSLError as e:
        print(f"[SSL WARNING] Retrying with verify=False for {url}")
        try:
            response = session.get(url, headers=headers, timeout=timeout, verify=False)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except Exception as ex:
            print(f"[SSL BYPASS ERROR] {url}: {ex}")
    except Exception as e:
        print(f"[REQUEST ERROR] Failed to fetch {url}: {e}")
    return ""

def extract_html_via_selenium(url: str, user_agent: str) -> str:
    from selenium import webdriver
    from tempfile import mkdtemp

    options = webdriver.ChromeOptions()
    service = webdriver.ChromeService("/opt/chromedriver")

    # 명확하게 chrome 바이너리 위치 지정
    options.binary_location = os.environ.get("CHROME_BIN", "/opt/chrome/chrome")
    options.add_argument("--headless")
    options.add_argument('--no-sandbox')
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280x1696")
    options.add_argument("--single-process")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-dev-tools")
    options.add_argument("--no-zygote")
    options.add_argument(f"--user-data-dir={mkdtemp()}")
    options.add_argument(f"--data-path={mkdtemp()}")
    options.add_argument(f"--disk-cache-dir={mkdtemp()}")
    options.add_argument(f"--user-agent={user_agent}")

    driver = webdriver.Chrome(options=options, service=service)

    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        return driver.page_source
    except Exception as e:
        print(f"[SELENIUM ERROR] Failed to fetch {url}: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass
    return ""

def parse_article_text_from_url(url: str) -> str:
    user_agent = generate_user_agent()
    print(f"[INFO] Fetching {url} with User-Agent")

    html = extract_html_via_requests(url, user_agent)

    if not html:
        return ""

    if is_dynamic_page(html):
        print(f"[INFO] Dynamic page detected: {url}")
        html = extract_html_via_selenium(url, user_agent)
        if not html:
            print(f"[ERROR] Failed to extract dynamic content from {url}")
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