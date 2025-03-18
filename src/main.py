import json
import os
import boto3

os.environ["OPENAI_API_KEY"] = boto3.client("ssm", region_name="ap-northeast-2").\
                                        get_parameter(Name='/llm/apikey', WithDecryption=True)\
                                        ["Parameter"]["Value"]

def lambda_handler(event, context):
    # Run different types of helper functions
    # Return from Lambda
    ret = []

    for data in event:
        try:
            article_id = data['article_id']
            blog_id = data['blog_id']
            url = data['url']
            title = data['title']
            thumbnail = data['thumbnail']
            content = data['content']
            published_at = data['published_at']

        except Exception as e:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": str(e)})
            }

    return {
        'statusCode': 200,
        'body' : json.dumps(ret)
    }