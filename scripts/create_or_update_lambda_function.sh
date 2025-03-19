echo "This is the image tag to be used: $SHA"
if aws lambda get-function --function-name container-lambda; then
  echo "Updating Lambda function code..."
  aws lambda update-function-code --function-name container-lambda \
    --image-uri 396913717156.dkr.ecr.ap-northeast-2.amazonaws.com/harvest-post:$SHA
else
  echo "Creating new Lambda function 'container-lambda'..."
  aws lambda create-function --function-name container-lambda \
    --package-type Image \
    --code ImageUri=396913717156.dkr.ecr.ap-northeast-2.amazonaws.com/harvest-post:$SHA \
    --role arn:aws:iam::396913717156:role/lambda-execution-role \
    --region ap-northeast-2
fi