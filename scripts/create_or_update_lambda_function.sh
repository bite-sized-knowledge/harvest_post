echo "This is the image tag to be used: dev"
if aws lambda get-function --function-name harvest-post; then
  echo "Updating Lambda function code..."
  aws lambda update-function-code --function-name harvest-post \
    --image-uri 396913717156.dkr.ecr.ap-northeast-2.amazonaws.com/harvest-post:dev
else
  echo "Creating new Lambda function 'harvest-post'..."
  aws lambda create-function --function-name harvest-post \
    --package-type Image \
    --code ImageUri=396913717156.dkr.ecr.ap-northeast-2.amazonaws.com/harvest-post:dev \
    --role arn:aws:iam::396913717156:role/lambda-execution-role \
    --region ap-northeast-2
fi