# AWS Lambda용 Python 3.11 베이스 이미지 사용
FROM umihico/aws-lambda-selenium-python:latest

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Lambda 함수 코드 복사
COPY src/ ${LAMBDA_TASK_ROOT}

# Lambda 핸들러 설정
CMD [ "main.lambda_handler" ]