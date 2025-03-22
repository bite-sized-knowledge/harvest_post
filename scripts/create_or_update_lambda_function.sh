#!/bin/bash

# Secret Info
AWS_ACCOUNT_ID=$1
ECR_REGION=$2
ECR_REPO=$3
LAMBDA_FUNCTION_NAME=$4
IAM_ROLE_ARN=$5
SHA=$6

# Essenctial info check
if [ -z "$AWS_ACCOUNT_ID" ] || [ -z "$ECR_REGION" ] || [ -z "$ECR_REPO" ] || [ -z "$LAMBDA_FUNCTION_NAME" ] || [ -z "$IAM_ROLE_ARN" ] || [ -z "$SHA" ]; then
  echo "Error: Missing arguments."
  echo "Usage: $0 <AWS_ACCOUNT_ID> <ECR_REGION> <ECR_REPO> <LAMBDA_FUNCTION_NAME> <IAM_ROLE_ARN> <SHA>"
  exit 1
fi

# Lambda 함수가 존재하는지 확인 후 업데이트 또는 생성
if aws lambda get-function --function-name $LAMBDA_FUNCTION_NAME > /dev/null 2>&1; then
  echo "Updating Lambda function code..."
  aws lambda update-function-code --function-name $LAMBDA_FUNCTION_NAME \
    --image-uri $AWS_ACCOUNT_ID.dkr.ecr.$ECR_REGION.amazonaws.com/$ECR_REPO:$SHA
else
  echo "Creating new Lambda function '$LAMBDA_FUNCTION_NAME'..."
  aws lambda create-function --function-name $LAMBDA_FUNCTION_NAME \
    --package-type Image \
    --code ImageUri=$AWS_ACCOUNT_ID.dkr.ecr.$ECR_REGION.amazonaws.com/$ECR_REPO:$SHA \
    --role $IAM_ROLE_ARN \
    --region $ECR_REGION
fi