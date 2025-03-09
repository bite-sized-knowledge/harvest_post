#

# Setup Steps

## Copy the GitHub repository

Copy the repository into a new one you will own using the following link:
[Copy to a new repository from the repository template](https://github.com/new?template_name=containerlambdacicd-blog&template_owner=javiercarreraruiz)

## Clone your new GitHub repository

Assuming you called your new repo _containerlambdacicd-blog-mycopy_, change the _yourgithubname_ string to your GitHub username (or Org) run this on your machine:

```
git clone https://github.com/yourgithubname/containerlambdacicd-blog-mycopy.git
cd containerlambdacicd-blog-mycopy
```

## Create the artifacts bucket

```
export ARTIFACTS_BUCKET_NAME=containerlambdacicd-blog-artifacts
aws s3 mb s3://$ARTIFACTS_BUCKET_NAME
```

## Create the ECR repository

```
aws ecr create-repository --repository-name lambda-from-container-image
```

## Create an AWS CodeConnections connection

- Go to: https://console.aws.amazon.com/codesuite/settings/connections
- Choose your AWS region if it has changed
- Click on "Create Connection"
- Follow the instructions to create the connection to GitHub. The name is not important but you can call it, for instance, _containerlambda-gh-connection_
- Choose your AWS region if it has changed and click on Connect
- Go back to https://console.aws.amazon.com/codesuite/settings/connections and copy the ARN of the new connection as you will assign it to an environment variable later

## Replace placeholders in template files

**IMPORTANT**: You need to adapt the next environment variables AND you need to have the _envsubst_ command in your machine.
More info about _envsubst_ here: [envsubst\(1\) - Linux manual page](https://man7.org/linux/man-pages/man1/envsubst.1.html)

**_Remember you have to edit these values:_**

```
export ACCOUNT_ID=YOUR_AWS_12_DIGIT_ACCOUNT_NUMBER
export REGION_ID=SOMETHING_LIKE_eu-west-1
export ARTIFACTS_BUCKET_NAME=THE_BUCKET_YOU_CREATED_PREVIOUSLY
export GITHUB_CONNECTION_ARN=THE_AWS_CODECONNECTIONS_CONNECTION_ARN
export GITHUB_USERNAME=yourgithubname
export GITHUB_REPONAME=containerlambdacicd-blog-mycopy
```

```
envsubst '${ACCOUNT_ID} ${REGION_ID}' < buildspec.yml.template > buildspec.yml
envsubst '${ARTIFACTS_BUCKET_NAME} ${GITHUB_CONNECTION_ARN} ${GITHUB_REPONAME} ${GITHUB_USERNAME} ${REGION_ID}' < pipeline.json.template > pipeline.json
envsubst '${ACCOUNT_ID} ${ARTIFACTS_BUCKET_NAME} ${GITHUB_CONNECTION_ARN} ${GITHUB_REPONAME} ${GITHUB_USERNAME} ${REGION_ID}' < project-config.json.template > project-config.json
envsubst '${ACCOUNT_ID} ${ARTIFACTS_BUCKET_NAME} ${GITHUB_CONNECTION_ARN} ${GITHUB_REPONAME} ${GITHUB_USERNAME} ${REGION_ID}' < create-roles.sh.template > create-roles.sh
envsubst '${ACCOUNT_ID} ${ARTIFACTS_BUCKET_NAME} ${GITHUB_CONNECTION_ARN} ${GITHUB_REPONAME} ${GITHUB_USERNAME} ${REGION_ID}' < scripts/create_or_update_lambda_function.sh.template > scripts/create_or_update_lambda_function.sh
```

## Push the changes to your GitHub repository

```
git add .
git commit -m "generate new files from templates"
git push origin main
```

## Create IAM roles

Create the roles assumed by the CodeBuild and CodePipeline services and the Lambda function:

```
chmod +x create-roles.sh
./create-roles.sh
```

## Create the CodeBuild project and the CodePipeline pipeline

```
aws codebuild create-project --cli-input-json file://project-config.json

aws codepipeline create-pipeline --cli-input-json file://pipeline.json
```

# Cleanup Steps

```
# Delete CodeStar Connections connection
aws codestar-connections delete-connection --connection-arn $GITHUB_CONNECTION_ARN

# Delete CodePipeline
aws codepipeline delete-pipeline --name MyLambdaPipeline

# Delete CodeBuild project
aws codebuild delete-project --name MyLambdaBuildProject

# Delete the ECR repository (after deleting the images)
aws ecr batch-delete-image --repository-name lambda-from-container-image --image-ids "$(aws ecr list-images --repository-name lambda-from-container-image --query 'imageIds[*]' --output json | jq -c '.[]')"
aws ecr delete-repository --repository-name lambda-from-container-image --force

# Delete Lambda function
aws lambda delete-function --function-name container-lambda

# Force delete Lambda role (detach all managed policies and delete role)
for policy_arn in $(aws iam list-attached-role-policies --role-name lambda-execution-role --query 'AttachedPolicies[].PolicyArn' --output text); do
  aws iam detach-role-policy --role-name lambda-execution-role --policy-arn $policy_arn
done
aws iam delete-role --role-name lambda-execution-role

# Force delete CodePipeline role (delete inline policies, and delete role)
for policy_name in $(aws iam list-role-policies --role-name containerlambdacicd-codepipeline-role --query 'PolicyNames[]' --output text); do
  aws iam delete-role-policy --role-name containerlambdacicd-codepipeline-role --policy-name $policy_name
done
aws iam delete-role --role-name containerlambdacicd-codepipeline-role

# Force delete CodeBuild role (delete inline policies, and delete role)
for policy_name in $(aws iam list-role-policies --role-name containerlambdacicd-codebuild-role --query 'PolicyNames[]' --output text); do
  aws iam delete-role-policy --role-name containerlambdacicd-codebuild-role --policy-name $policy_name
done
aws iam delete-role --role-name containerlambdacicd-codebuild-role

# Delete all objects in the artifacts bucket
aws s3 rm s3://$ARTIFACTS_BUCKET_NAME --recursive

# Delete the artifacts bucket
aws s3api delete-bucket --bucket $ARTIFACTS_BUCKET_NAME
```
