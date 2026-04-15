import os
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

class Connection:
    def __init__(self):
        # SQLAlchemy 연결 초기화
        self.engine = None
        self.SessionLocal = None

        self.ENVIRONMENT = os.getenv('ENVIRONMENT', 'dev')

        if self.ENVIRONMENT != 'prod':
            load_dotenv()
        
        self.RDS_DATABASE = os.getenv("DB_NAME")
        self.RDS_HOST = os.getenv("DB_HOST")
        self.RDS_USER = os.getenv("DB_USER")
        self.RDS_PASSWORD = os.getenv("DB_PASSWORD")
        self.RDS_PORT = int(os.getenv("DB_PORT"))

        # DB 연결 시작
        self._connect_to_rds()

    def connect_to_engine(self, host, user, password, database, port):
        """ SQLAlchemy 엔진 생성 """
        DATABASE_URL = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
        connect_args = {}
        # SSL 활성화 (require_secure_transport=ON 대응)
        import ssl
        ca_path = os.getenv("DB_TLS_CA")
        if ca_path:
            ssl_ctx = ssl.create_default_context(cafile=ca_path)
            ssl_ctx.check_hostname = False  # self-signed CN doesn't match Docker service name
        else:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        connect_args['ssl'] = ssl_ctx
        return create_engine(
            DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            connect_args=connect_args
        )

    def _connect_to_rds(self):
        try:
            print(f"Connecting to RDS: {self.RDS_HOST}:{self.RDS_PORT}")

            # SQLAlchemy 엔진 생성
            self.engine = self.connect_to_engine(
                host=self.RDS_HOST,
                user=self.RDS_USER,
                password=self.RDS_PASSWORD,
                database=self.RDS_DATABASE,
                port=self.RDS_PORT
            )

            # 세션 생성기 초기화
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

            print("Connected to RDS!")

        except Exception as e:
            print("Error occurred:", e)
            raise

    def execute(self, query):
        """ SELECT 쿼리를 실행하고 Pandas DataFrame으로 반환 """
        if not self.engine:
            raise Exception("No active DB connection.")
        
        # Pandas를 사용해 쿼리 실행 결과를 DataFrame으로 반환
        return pd.read_sql(query, self.engine)

    def _raw_execute(self, query, values=None):
        if not self.engine:
            raise Exception("No active DB connection.")
        
        raw_conn = self.engine.raw_connection()
        try:
            cursor = raw_conn.cursor()
            try:
                cursor.execute(query, values)
            finally:
                cursor.close()
            raw_conn.commit()
            print("Query executed successfully.")
        except Exception as e:
            print(f"Error executing query: {query}, values: {values}. Error: {e}")
            raise
        finally:
            raw_conn.close()

    def session_execute(self, query, values=None):
        if not self.SessionLocal:
            raise Exception("No active session.")
        
        session = self.SessionLocal()
        try:
            stmt = text(query) if isinstance(query, str) else query

            if isinstance(values, list) and all(isinstance(v, tuple) for v in values):
                session.execute(stmt, values)
            else:
                session.execute(stmt, params=values)
            session.commit()
            print("Query executed successfully.")
        except Exception as e:
            session.rollback()
            print(f"[ERROR] DB query failed: {str(e)}")
            raise
        finally:
            session.close()

    def close(self):
        """ 연결 종료 """
        if self.engine:
            self.engine.dispose()
            print("SQLAlchemy Engine Disposed.")