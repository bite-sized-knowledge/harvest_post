import os
import pymysql
import pandas as pd
from dotenv import load_dotenv

class Connection:
    def __init__(self):
        # Connection Initialize 

        self.ENVIRONMENT = os.getenv('ENVIRONMENT', 'dev')

        if self.ENVIRONMENT != 'prod':
            load_dotenv()

        # DB 설정값 로드 (공통)
        self.RDS_DATABASE = os.getenv("DB_NAME")
        self.RDS_HOST = os.getenv("DB_HOST")
        self.RDS_USER = os.getenv("DB_USER")
        self.RDS_PASSWORD = os.getenv("DB_PASSWORD")
        self.RDS_PORT = int(os.getenv("DB_PORT"))

        # 연결 초기화
        self.connection = None

        # DB 연결 시작
        self._connect_to_rds()

    def _connect_to_rds(self):
        try:
            print(f"Connecting to RDS at {self.RDS_HOST}:{self.RDS_PORT}")

            # MySQL 연결 (직접 연결)
            self.connection = pymysql.connect(
                host=self.RDS_HOST,
                user=self.RDS_USER,
                password=self.RDS_PASSWORD,
                database=self.RDS_DATABASE,
                port=self.RDS_PORT,
                cursorclass=pymysql.cursors.DictCursor,
            )

            print("Connected to RDS!")

        except Exception as e:
            print("Error occurred:", e)
            raise

    def execute(self, query):
        if not self.connection:
            raise Exception("No active DB connection.")
        return pd.read_sql(query, self.connection)

    def _raw_execute(self, query, values:set=None):
        if not self.connection:
            raise Exception("No active DB connection.")
        with self.connection.cursor() as cursor:
            cursor.execute(query, values)
            self.connection.commit()

    def close(self):
        """ 연결 종료 """
        if self.connection:
            self.connection.close()
            print("RDB Connection Closed.")
