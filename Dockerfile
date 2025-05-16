# 베이스 이미지 설정
FROM public.ecr.aws/lambda/python:3.11

# 시스템 패키지 설치
RUN yum install -y \
    unzip \
    wget \
    xorg-x11-server-Xvfb \
    libX11 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXtst \
    libnss3 \
    libXrandr \
    alsa-lib \
    atk \
    cups-libs \
    gtk3 \
    pango \
    libXss \
    libXScrnSaver \
    libXrender \
    libXfixes \
    libXinerama \
    libXft \
    libXpm \
    libXaw \
    libXmu \
    libXt \
    libSM \
    libICE \
    libXdmcp \
    libXau \
    libxcb \
    libXcomposite \
    libXdamage \
    libXfixes \
    libXrandr \
    libXrender \
    libXtst \
    libXxf86vm \
    libdrm \
    mesa-libGL \
    mesa-libGLU \
    && yum clean all

# Chromium 및 ChromeDriver 다운로드 및 설치
RUN wget https://storage.googleapis.com/chrome-for-testing-public/122.0.6261.128/linux64/chrome-linux64.zip \
    && unzip chrome-linux64.zip \
    && mv chrome-linux64 /opt/chrome \
    && wget https://storage.googleapis.com/chrome-for-testing-public/122.0.6261.128/linux64/chromedriver-linux64.zip \
    && unzip chromedriver-linux64.zip \
    && mv chromedriver-linux64/chromedriver /opt/chromedriver \
    && chmod +x /opt/chromedriver \
    && chmod +x /opt/chrome/chrome

# 환경 변수 설정
ENV PATH="/opt/chrome:/opt/chromedriver:${PATH}"
ENV CHROME_BIN="/opt/chrome/chrome"
ENV CHROMEDRIVER="/opt/chromedriver"

# Python 패키지 설치
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# 함수 코드 복사
COPY src/ ${LAMBDA_TASK_ROOT}

# Lambda 핸들러 설정
CMD [ "main.lambda_handler" ]