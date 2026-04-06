import os
import asyncio
from typing import List
from langchain_text_splitters import RecursiveCharacterTextSplitter
from numpy.linalg import norm
from config import EMBEDDING_CONFIG


class TextEmbeddings:
    """In-process embedding using sentence-transformers on CPU.

    The 0.6B Qwen3-Embedding model loads in ~2s and produces embeddings
    in <1s per call on a modern CPU. This avoids a separate vLLM-embed
    container (which would need GPU VRAM the 9B chat model needs).
    """

    separator = "\n\n###\n\n"

    def __init__(
        self,
        model: str = None,
        chunk_size: int = None,
        chunk_overlap: int = 200
    ):
        from sentence_transformers import SentenceTransformer

        self.model_name = model or EMBEDDING_CONFIG.model
        self.chunk_size = chunk_size or int(os.getenv("CHUNK_SIZE", 5000))
        self.chunk_overlap = chunk_overlap
        self.chunker = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ".", " ", ""]
        )
        # Lazy-load on first use so container startup isn't blocked.
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self.model_name,
                device="cpu",
                trust_remote_code=True,
            )
        return self._model

    def _embed_once(self, text: str, dimensions: int, normalize: bool) -> List[float]:
        model = self._get_model()
        embedding = model.encode(text, normalize_embeddings=False).tolist()

        # Matryoshka-style dimension truncation.
        if len(embedding) > dimensions:
            embedding = embedding[:dimensions]

        if normalize:
            l2 = norm(embedding)
            if l2 > 0:
                embedding = [v / l2 for v in embedding]

        return embedding

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
