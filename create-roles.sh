aws iam create-role \
--role-name containerlambdacicd-codebuild-role \
--assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "codebuild.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}'


aws iam put-role-policy \
--role-name containerlambdacicd-codebuild-role \
--policy-name containerlambdacicd-codebuild-inline-policy \
--policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "ecr:*",
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "codebuild:CreateReportGroup",
                "codebuild:CreateReport",
                "codebuild:UpdateReport",
                "codebuild:BatchPutCodeCoverages",
                "codebuild:BatchPutTestCases"
            ],
            "Resource": "arn:aws:codebuild:ap-northeast-2:396913717156:report-group/bite-lambda-*"
        },
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": "arn:aws:iam::396913717156:role/lambda-execution-role"
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": [
                "arn:aws:logs:ap-northeast-2:396913717156:log-group:/aws/codebuild/bite-lambda",
                "arn:aws:logs:ap-northeast-2:396913717156:log-group:/aws/codebuild/bite-lambda:*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": "lambda:*",
            "Resource": "arn:aws:lambda:ap-northeast-2:396913717156:function:container-lambda"
        },
        {
            "Effect": "Allow",
            "Action": "s3:*",
            "Resource": [
                "arn:aws:s3:::harvest-post",
                "arn:aws:s3:::harvest-post/*"
            ]
        }
    ]
}'


aws iam create-role \
--role-name containerlambdacicd-codepipeline-role \
--assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": [
                    "codepipeline.amazonaws.com"
                ]
            },
            "Action": "sts:AssumeRole"
        }
    ]
}'


aws iam put-role-policy \
--role-name containerlambdacicd-codepipeline-role \
--policy-name containerlambdacicd-codepipeline-inline-policy \
--policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "codebuild:StartBuild",
                "codebuild:BatchGetBuilds"
            ],
            "Resource": "arn:aws:codebuild:ap-northeast-2:396913717156:project/bite-lambda"
        },
        {
            "Effect": "Allow",
            "Action": [
                "codestar-connections:UseConnection"
            ],
            "Resource": "arn:aws:codeconnections:ap-northeast-2:396913717156:connection/74b245db-6c86-4185-b0b4-e40ce21a6870"
        },
        {
            "Effect": "Allow",
            "Action": "s3:PutObject",
            "Resource": "arn:aws:s3:::harvest-post/*"
        }
    ]
}'



# Create the Lambda execution role and attach AWSLambdaBasicExecutionRole policy
aws iam create-role \
--role-name lambda-execution-role \
--assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}'

# Attach the AWSLambdaBasicExecutionRole managed policy to the Lambda execution role
aws iam attach-role-policy \
--role-name lambda-execution-role \
--policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
