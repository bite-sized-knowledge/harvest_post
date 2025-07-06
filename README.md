# 🌽 Harvest Post Lambda 

![Image](https://github.com/user-attachments/assets/4572e08a-76d7-4ac6-9b8c-1c8717efdd30)

> `Bite-Knowledge` 프로젝트에서 데이터 수집 후 텍스트 전처리를 담당하는 AWS Lambda 저장소

## Build

```bash
docker build --platform linux/x86_64 -t lambda-local .

docker run -p 9000:8080 \
  --env-file .env \
  -e AWS_ACCESS_KEY_ID=$(aws_access_key_id) \
  -e AWS_SECRET_ACCESS_KEY=$(aws_secret_access_key) \
  lambda-local
```