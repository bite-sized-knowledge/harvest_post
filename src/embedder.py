import os
import asyncio
import requests
from typing import List
from langchain_text_splitters import RecursiveCharacterTextSplitter
from numpy.linalg import norm
from config import VLLM_EMBED_BASE_URL, EMBEDDING_CONFIG


class TextEmbeddings:
    """Embedding generator backed by a vLLM instance running an embedding
    model (e.g. Qwen3-Embedding-0.6B) via the OpenAI-compatible
    /v1/embeddings endpoint. Matryoshka-style dimension truncation and
    optional L2 normalization are applied client-side so the same embeddings
    pipeline works regardless of the underlying vLLM model."""

    separator = "\n\n###\n\n"  # 안정적인 구조화 구분자

    def __init__(
        self,
        model: str = None,
        chunk_size: int = None,
        chunk_overlap: int = 200
    ):
        self.base_url = VLLM_EMBED_BASE_URL.rstrip("/")
        self.model = model or EMBEDDING_CONFIG.model
        self.chunk_size = chunk_size or int(os.getenv("CHUNK_SIZE", 5000))
        self.chunk_overlap = chunk_overlap
        self.chunker = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ".", " ", ""]
        )

    def _embed_once(self, text: str, dimensions: int, normalize: bool) -> List[float]:
        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                json={
                    "model": self.model,
                    "input": text,
                },
                headers={"Authorization": "Bearer not-needed"},
                timeout=300,
            )
            response.raise_for_status()
            payload = response.json()
            embeddings = payload["data"][0]["embedding"]

            # 차원 truncate (Matryoshka 지원 모델)
            if len(embeddings) > dimensions:
                embeddings = embeddings[:dimensions]

            if normalize:
                l2 = norm(embeddings)
                if l2 > 0:
                    embeddings = [v / l2 for v in embeddings]

            return embeddings
        except Exception as e:
            print(f"[vLLMEmbedding] Failed to embed: {e}")
            raise

    async def embed_text(self, text: str, dimensions: int, normalize: bool = True) -> List[float]:
        chunks = self.chunker.split_text(text)

        if len(chunks) == 1:
            return await asyncio.to_thread(self._embed_once, text, dimensions, normalize)

        tasks = [
            asyncio.to_thread(self._embed_once, chunk, dimensions, False)
            for chunk in chunks
        ]
        embeddings = await asyncio.gather(*tasks)

        avg = [
            sum(values) / len(embeddings)
            for values in zip(*embeddings)
        ]
        if normalize:
            l2 = norm(avg)
            avg = [v / l2 for v in avg]

        return avg

    async def __call__(
        self,
        title: str,
        description: str,
        keywords: str,
        content: str,
        dimensions: int,
        normalize: bool = True
    ) -> List[float]:

        merged = (
            f"Title: {title}{self.separator}"
            f"Description: {description}{self.separator}"
            f"Keywords: {keywords}{self.separator}"
            f"Content: {content}"
        )

        return await self.embed_text(merged, dimensions, normalize)
