# AWS Lambda용 Python 3.11 베이스 이미지 사용
FROM public.ecr.aws/lambda/python:3.11

# 필수 시스템 패키지 설치
RUN yum install -y \
    unzip \
    wget \
    curl \
    fontconfig \
    freetype \
    libX11 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    cups-libs \
    gtk3 \
    alsa-lib \
    xorg-x11-fonts-Type1 \
    xorg-x11-fonts-misc

# Chrome 및 ChromeDriver 버전 설정
ENV CHROME_VERSION=122.0.6261.128

# Chrome 및 ChromeDriver 다운로드 및 설치
RUN curl -Lo "/tmp/chrome-linux64.zip" "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip" && \
    curl -Lo "/tmp/chromedriver-linux64.zip" "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" && \
    unzip /tmp/chrome-linux64.zip -d /opt/ && \
    unzip /tmp/chromedriver-linux64.zip -d /opt/ && \
    rm /tmp/chrome-linux64.zip /tmp/chromedriver-linux64.zip && \
    mv /opt/chrome-linux64 /opt/chrome && \
    mv /opt/chromedriver-linux64/chromedriver /opt/chromedriver && \
    chmod +x /opt/chrome/chrome /opt/chromedriver

# 환경 변수 설정
ENV CHROME_BIN=/opt/chrome/chrome
ENV CHROMEDRIVER=/opt/chromedriver

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Lambda 함수 코드 복사
COPY src/ ${LAMBDA_TASK_ROOT}

# Lambda 핸들러 설정
CMD [ "main.lambda_handler" ]