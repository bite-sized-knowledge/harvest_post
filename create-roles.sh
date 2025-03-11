aws iam create-role \
--role-name havest-post-codebuild-role \
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
--role-name havest-post-codebuild-role \
--policy-name havest-post-codebuild-inline-policy \
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
            "Resource": "arn:aws:codebuild:ap-northeast-2:396913717156:report-group/havest-post-prj-*"
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
                "arn:aws:logs:ap-northeast-2:396913717156:log-group:/aws/codebuild/havest-post-prj",
                "arn:aws:logs:ap-northeast-2:396913717156:log-group:/aws/codebuild/havest-post-prj:*"
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
                "arn:aws:s3:::havest-post",
                "arn:aws:s3:::havest-post/*"
            ]
        }
    ]
}'


aws iam create-role \
--role-name havest-post-codepipeline-role \
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
--role-name havest-post-codepipeline-role \
--policy-name havest-post-codepipeline-inline-policy \
--policy-document '{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "codebuild:StartBuild",
                "codebuild:BatchGetBuilds"
            ],
            "Resource": "arn:aws:codebuild:ap-northeast-2:396913717156:project/havest-post-prj"
        },
        {
            "Effect": "Allow",
            "Action": [
                "codestar-connections:UseConnection"
            ],
            "Resource": "arn:aws:codeconnections:ap-northeast-2:396913717156:connection/a331978c-d000-4938-b764-a72003b6dcc4"
        },
        {
            "Effect": "Allow",
            "Action": "s3:PutObject",
            "Resource": "arn:aws:s3:::havest-post/*"
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
