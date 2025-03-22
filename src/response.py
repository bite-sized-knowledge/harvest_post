import json
from http import HTTPStatus

class HTTPResponse:
    def __init__(self, status: HTTPStatus, msg: str = None):
        self.status = status
        self.msg = msg

    def get_response(self):
        response = {
            "statusCode": self.status.value, 
            "msg": self.msg if self.msg else self.status.name
        }

        return response