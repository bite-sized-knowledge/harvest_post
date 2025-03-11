import json
import os
import ast
# Importing internal libraries

def lambda_handler(event, context):
    # Run different types of helper functions
    # Return from Lambda
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'This is basic Settings'
        })
    }