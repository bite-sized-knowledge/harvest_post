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

class QdrantVectorStore:
    def __init__(
        self,
        collection_name: str,
        vector_dim: int,
        distance: Distance = Distance.COSINE
    ):
        self.collection_name = collection_name
        self.vector_dim = vector_dim

        url = f"{os.getenv("QDRANT_HOST", "")}:{os.getenv("QDRANT_PORT", "")}"
        if os.getenv("ENVIRONMENT", "prod") == "prod":
            url = os.getenv("QDRANT_ENDPOINT")

        self.client = QdrantClient(
            url=url,
            api_key=os.getenv("QDRANT_API", None),
            prefer_grpc=False
        )

        self._init_collection(distance)

    def _init_collection(self, distance: Distance):
        if not self.client.collection_exists(self.collection_name):
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
    ) -> List[Dict]:
        """
        Args:
            query_vector: 검색할 기준 벡터
            top_k: 반환할 유사 아이템 수
            filters: {"category": "AI"} 와 같은 payload 필터

        Returns:
            List of dicts with keys: artcle_id, score, payload
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

        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter,
        )

        similars = [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in results
        ]

        return [
            sim["article_id"] for sim in similars["payload"]
        ]