import os
import json
import boto3
import asyncio
from typing import List
from langchain.text_splitter import RecursiveCharacterTextSplitter
from numpy.linalg import norm

class TextEmbeddings:
    accept = "application/json"
    content_type = "application/json"
    separator = "\n\n###\n\n"  # 안정적인 구조화 구분자

    def __init__(
        self,
        model_id: str = "amazon.titan-embed-text-v2:0",
        region: str = "ap-northeast-2",
        chunk_size: int = None,
        chunk_overlap: int = 200
    ):
        self.bedrock = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.chunk_size = chunk_size or int(os.getenv("CHUNK_SIZE", 5000))
        self.chunk_overlap = chunk_overlap
        self.chunker = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ".", " ", ""]
        )

    def _embed_once(self, text: str, dimensions: int, normalize: bool) -> List[float]:
        body = json.dumps({
            "inputText": text,
            "dimensions": dimensions,
            "normalize": normalize
        })
        try:
            response = self.bedrock.invoke_model(
                body=body,
                modelId=self.model_id,
                accept=self.accept,
                contentType=self.content_type
            )
            resp_body = json.loads(response["body"].read())
            return resp_body["embedding"]
        except Exception as e:
            logger.error(f"[TitanEmbedding] Failed to embed: {e}")
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

    async def __call__(self,
                       title: str,
                       description: str,
                       keywords: str,
                       content: str,
                       dimensions: int,
                       normalize: bool = True) -> List[float]:

        merged = (
            f"Title: {title}{self.separator}"
            f"Description: {description}{self.separator}"
            f"Keywords: {keywords}{self.separator}"
            f"Content: {content}"
        )

        return await self.embed_text(merged, dimensions, normalize)