# AWS Lambda용 Python 3.11 베이스 이미지 사용
FROM public.ecr.aws/lambda/python:3.11 AS stage

ENV CHROMIUM_VERSION=1002910
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

COPY install-browser.sh /tmp/
RUN /usr/bin/bash /tmp/install-browser.sh

FROM public.ecr.aws/lambda/python:3.11 AS base

COPY chrome-deps.txt /tmp/
RUN yum install -y $(cat /tmp/chrome-deps.txt)

# 환경 변수 설정
ENV CHROME_BIN=/opt/chrome/chrome
ENV CHROMEDRIVER=/opt/chromedriver

COPY --from=stage /opt/chrome /opt/chrome
COPY --from=stage /opt/chromedriver /opt/chromedriver

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Lambda 함수 코드 복사
COPY src/ ${LAMBDA_TASK_ROOT}

# Lambda 핸들러 설정
CMD [ "main.lambda_handler" ]