import json
import boto3
import os

class TextEmbeddings:
    accept = "application/json"
    content_type = "application/json"

    def __init__(self,
                 model_id: str = "amazon.titan-embed-text-v2:0",
                 region: str = "ap-northeast-2"):
        self.bedrock = boto3.client(
            service_name="bedrock-runtime",
            region_name=region
        )
        self.model_id = model_id


    def _embed_once(self, text: str, dimensions: int, normalize: bool):

        """
        Returns Titan Embeddings
        Args:
            text (str): text to embed
            dimensions (int): Number of output dimensions.
            normalize (bool): Whether to return the normalized embedding or not.
        Return:
            List[float]: Embedding
            
        """

        body = json.dumps({
            "inputText": text,
            "dimensions": dimensions,
            "normalize": normalize
        })
        response = self.bedrock.invoke_model(
            body=body,
            modelId=self.model_id,
            accept=self.accept,
            contentType=self.content_type
        )
        resp_body = json.loads(response["body"].read())
        return resp_body["embedding"]

    def __call__(self, text: str, dimensions: int, normalize: bool = True):
        """
        길이 6 000자를 초과하면 청크별 임베딩 후 평균을 반환
        """
        
        CHUNK_SIZE = int(os.getenv("CHUNK_SIZE"))

        if len(text) <= CHUNK_SIZE:
            return self._embed_once(text, dimensions, normalize)

        # 1) 청크 분할
        chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

        # 2) 각 청크 임베딩
        embeddings = [
            self._embed_once(chunk, dimensions, normalize)
            for chunk in chunks
        ]

        # 3) element-wise 평균
        num_chunks = len(embeddings)
        avg_embedding = [
            sum(values) / num_chunks               # values = (e1[i], e2[i], …)
            for values in zip(*embeddings)
        ]
        return avg_embedding

    def embed_article(
        self,
        title: str,
        keywords: str,
        content: str,
        dimensions: int,
        normalize: bool = True
    ):
    
        # 1) 필드별 임베딩
        emb_title    = self.__call__(title,    dimensions, normalize)
        emb_keywords = self.__call__(keywords, dimensions, normalize)
        emb_content  = self.__call__(content,  dimensions, normalize)

        # 2) element-wise 평균
        avg_embedding = [
            (t + k + c) / 3
            for t, k, c in zip(emb_title, emb_keywords, emb_content)
        ]
        return avg_embedding