# 🌽 Harvest Post Lambda 

![Image](https://github.com/user-attachments/assets/5ede42f6-263d-4a21-836d-a386ec09bb3c)

> `Bite-Knowledge` 프로젝트에서 데이터 수집 후 텍스트 전처리를 담당하는 AWS Lambda 저장소

## Build

```bash
docker build -t lambda-local .

docker run -p 9000:8080 \
  --env-file .env \
  -e AWS_ACCESS_KEY_ID=$(aws_access_key_id) \
  -e AWS_SECRET_ACCESS_KEY=$(aws_secret_access_key) \
  lambda-local
```
