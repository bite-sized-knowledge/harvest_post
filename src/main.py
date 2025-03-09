import json

def lambda_handler(event, context):
    # Run different types of helper functions
    # Return from Lambda
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'This is the data returned by the container running within Lambda!'
        })
    }