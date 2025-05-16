import os
import re
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from trafilatura import extract
from fake_useragent import UserAgent

# 세션 구성 (커넥션 풀 포함)
session = requests.Session()
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('http://', adapter)
session.mount('https://', adapter)

def generate_user_agent():
    try:
        return UserAgent().random
    except Exception:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"

def is_dynamic_page(html: str) -> bool:
    soup = BeautifulSoup(html, 'html.parser')

    # 1. 구조적 힌트
    if not (soup.find('main') or soup.find('article') or soup.find(class_='content')):
        return True

    # 2. 자바스크립트 과도한 렌더링 감지
    script_count = len(soup.find_all('script'))
    text_len = len(re.sub(r'<[^>]+>', '', html))
    if script_count > 5 and text_len < 300:
        return True

    # 3. SPA 프레임워크 감지
    dynamic_indicators = [
        "__NEXT_DATA__", "window.__INITIAL_STATE__", "data-reactroot",
        "id=\"app\"", "id=\"root\"", "ng-app", "vue", "window.__NUXT__",
        "data-vue-meta", "data-server-rendered"
    ]
    return any(keyword in html for keyword in dynamic_indicators)

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
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"--user-agent={user_agent}")
    chrome_options.add_argument("--window-size=1280,800")
    chrome_options.binary_location = os.environ.get("CHROME_BIN", "/opt/chrome/chrome")

    driver_path = os.environ.get("CHROMEDRIVER", "/opt/chromedriver")
    service = Service(executable_path=driver_path)

    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
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