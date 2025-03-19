import json
import os
from db_conn import Connection

def lambda_handler(event, context):
    conn = Connection()

    return {
        'statusCode': 200,
        'body' : conn.ENVIRONMENT
    }