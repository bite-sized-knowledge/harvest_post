from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from typing import List, Dict, Optional, Union
import numpy as np
import uuid
import os

from logger import logger


class QdrantVectorStore:
    def __init__(
        self,
        collection_name: str,
        vector_dim: int,
        distance: Distance = Distance.COSINE
    ):
        self.collection_name = collection_name
        self.vector_dim = vector_dim

        url = f"{os.getenv('QDRANT_HOST', '')}:{os.getenv('QDRANT_PORT', '')}"
        if os.getenv("ENVIRONMENT", "prod") == "prod":
            url = os.getenv("QDRANT_ENDPOINT")

        api_key = os.getenv("QDRANT_API_KEY", None)
        if not api_key:
            logger.warning("QDRANT_API_KEY is not set — Qdrant requests will be unauthenticated")

        self.client = QdrantClient(
            url=url,
            api_key=api_key,
            prefer_grpc=False
        )

        self._init_collection(distance, url=url, has_api_key=bool(api_key))

    def _init_collection(self, distance: Distance, *, url: str, has_api_key: bool):
        try:
            exists = self.client.collection_exists(self.collection_name)
        except Exception as e:
            logger.error("Qdrant connection failed — pipeline will not be able to store embeddings",
                         url=url, error=str(e), has_api_key=has_api_key)
            raise
        logger.info("Qdrant connection verified", url=url)
        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_dim,
                    distance=distance
                )
            )

    def upsert_points(self, points: List[Dict]):
        """
        Args:
            points: [
                {
                    "id": Union[str, str],
                    "vector": List[float],
                    "payload": Dict[str, Any]
                },
                ...
            ]
        """
        qdrant_points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, p["id"])),
                vector=p["vector"],
                payload=p.get("payload", {})
            )
            for p in points
        ]
        self.client.upsert(collection_name=self.collection_name, points=qdrant_points)

    def search_similar(
        self,
        query_vector: Union[List[float], np.ndarray],
        top_k: int = 5,
        filters: Optional[Dict[str, Union[str, int]]] = None,
    ) -> List[str]:
        """
        Args:
            query_vector: 검색할 기준 벡터
            top_k: 반환할 유사 아이템 수
            filters: {"category": "AI"} 와 같은 payload 필터

        Returns:
            List of article_ids
        """
        if isinstance(query_vector, np.ndarray):
            query_vector = query_vector.tolist()

        query_filter = None
        if filters:
            query_filter = Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in filters.items()
                ]
            )

        # qdrant-client 1.16+ uses query_points instead of search
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            query_filter=query_filter,
        )

        return [
            hit.payload.get("article_id")
            for hit in results.points
            if hit.payload
        ]