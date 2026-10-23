import base64
import logging
import os
from pathlib import Path
from uuid import uuid4

from chunker import chunk_file
from jinja2 import Template
from mcp_use import MCPServer
from openai import OpenAI
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="source-rag-mcp",
    version="1.0.0",
    instructions=(
        "Mcp server that provides RAG retrieval for source code. "
        "Use this to understand externally referenced code, outside of the repository "
        "you are currently working in."
    ),
)

QDRANT_COLLECTION = "pxc_source"
QDRANT_URL = os.getenv(
    "QDRANT_URL", "http://qdrant.pve-cloud-integration.svc.cluster.local:6333"
)

BATCH_SIZE = 16

qdrant_client = QdrantClient(QDRANT_URL)

RAG_RESULTS_LIMIT = 5
RAG_RESULTS_MIN_SCORE = 0.5

# assumes an external openai compatible model server like vllm
EMB_SERVER_URL = os.getenv("MODEL_SERVER_URL")
EMB_AUTH_HEADER_VALUE = os.getenv("MODEL_AUTH_HEADER_VALUE")

EMB_MODEL = os.getenv("MODEL_NAME")  # embedidng model name like Qwen/Qwen3-Embedding-8B

# init client
openai_client = OpenAI(
    base_url=EMB_SERVER_URL,
    api_key="NOT_USED",
    default_headers={"Authorization": EMB_AUTH_HEADER_VALUE},
)

# queries usually need to be formatted differntly for the embedding model to
# provide correct results. This server assumes that documents simply get passed raw
# (as is the case with most open weight embedding models, only queries require special markers)
QUERY_TMPL_B64 = os.getenv("J2_QUERY_TEMPLATE_B64")

query_template = Template(base64.b64decode(QUERY_TMPL_B64).decode("utf-8"))


class SourceResult(BaseModel):
    score: float = Field(description="Similarity score.")
    content: str = Field(description="Source code content.")


@mcp.tool()
async def find_source(
    query: str = Field(
        ...,
        description="Generated description / query for the function / classes / mechanism you are interested in.",
    )
) -> list[SourceResult]:
    """
    Queries the underlying vector database for source code snippets that
    are relevant to the provided query.

    This is your PRIMARY tool for understanding functions, classes, etc. of libraries / that
    are not in the current repository you are working on.
    """
    logger.info(f"Querying qdrant for: {query}")

    # create search vector
    rendered_query = query_template.render(query=query)
    emb_resp = openai_client.embeddings.create(input=rendered_query, model=EMB_MODEL)

    # query the database
    rag_results = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=emb_resp.data[0].embedding,
        limit=RAG_RESULTS_LIMIT,
        with_payload=True,
        with_vectors=False,
    ).points

    # return results matching min score
    return [
        SourceResult(score=point.score, content=point.payload["content"])
        for point in rag_results
        if point.score >= RAG_RESULTS_MIN_SCORE
    ]


def main():
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)


# manually test it:
# kubectl port-forward -n pve-cloud-integration service/qdrant 6333:6333
# export QDRANT_URL=http://localhost:6333
def inserter():
    chunks = []
    for path in sorted(Path.cwd().rglob("*.py")):
        try:
            chunks.extend(chunk_file(str(path), language="python"))
        except Exception as exc:
            print(f"Failed to chunk {path}: {exc}")

    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]

        documents = [chunk.content for chunk in batch]

        response = openai_client.embeddings.create(
            model=EMB_MODEL,
            input=documents,
        )

        response_data = sorted(response.data, key=lambda item: item.index)

        embeddings = [item.embedding for item in response_data]

        # create collection based on vec size
        if not qdrant_client.collection_exists(QDRANT_COLLECTION):
            qdrant_client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=len(embeddings[0]),  # vector size
                    distance=models.Distance.COSINE,
                ),
            )

        # the actual data
        points = []
        for chunk, embedding in zip(batch, embeddings):
            points.append(
                models.PointStruct(
                    # todo: proper id function here / cleanup before reinsering?
                    id=uuid4(),
                    vector=embedding,
                    payload={
                        "content": chunk.content,
                    },
                )
            )

        qdrant_client.upsert(collection_name=QDRANT_COLLECTION, points=points)
