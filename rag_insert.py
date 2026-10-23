from pathlib import Path
from uuid import uuid4

from chunker import chunk_file
from openai import OpenAI
from qdrant_client import QdrantClient, models

# Configuration
SRC_DIR = Path("src")
COLLECTION = "code_chunks"
MODEL = "ironbcc/Qwen3-Embedding-4B-NVFP4"

BATCH_SIZE = 16

embedding_client = OpenAI(
    base_url="http://192.168.1.53:8001/v1",
    api_key="EMPTY",
)

# Qdrant
qdrant = QdrantClient(url="http://localhost:6333")


# scan src and chunk py files
chunks = []

for path in sorted(SRC_DIR.rglob("*.py")):
    try:
        chunks.extend(chunk_file(str(path), language="python"))
    except Exception as exc:
        print(f"Failed to chunk {path}: {exc}")


# insert chunks batched
for start in range(0, len(chunks), BATCH_SIZE):
    batch = chunks[start : start + BATCH_SIZE]

    documents = [chunk.content for chunk in batch]

    response = embedding_client.embeddings.create(
        model=MODEL,
        input=documents,
    )

    response_data = sorted(response.data, key=lambda item: item.index)

    embeddings = [item.embedding for item in response_data]

    # create collection based on vec size
    if not qdrant.collection_exists(COLLECTION):
        qdrant.create_collection(
            collection_name=COLLECTION,
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

    qdrant.upsert(collection_name=COLLECTION, points=points)
