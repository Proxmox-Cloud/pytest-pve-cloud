from openai import OpenAI
from qdrant_client import QdrantClient, models

COLLECTION = "code_chunks"
MODEL = "ironbcc/Qwen3-Embedding-4B-NVFP4"

# Embedding server
embedding_client = OpenAI(
    base_url="http://192.168.1.53:8001/v1",
    api_key="EMPTY",
)

# Qdrant
qdrant = QdrantClient(url="http://localhost:6333")


def get_detailed_instruct(task_description: str, query: str) -> str:
    return f"Instruct: {task_description}\nQuery:{query}"


TASK = "Given a question about a codebase, retrieve relevant code passages that answer the question"


def search(query: str, limit: int = 5):
    # Format query for Qwen3 embedding
    formatted_query = get_detailed_instruct(TASK, query)

    # Generate query embedding using your hosted model
    response = embedding_client.embeddings.create(
        model=MODEL,
        input=formatted_query,
    )

    vector = response.data[0].embedding

    # Search existing vectors in Qdrant
    results = qdrant.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    ).points

    print(f"\nQuery: {query}")
    print(f"Formatted query: {formatted_query}")
    print(f"Results: {len(results)}\n")

    for i, point in enumerate(results, 1):
        print("=" * 80)
        print(f"Result #{i} | Score: {point.score:.4f}")
        print(f"ID: {point.id}")

        payload = point.payload or {}

        for key, value in payload.items():
            print(f"\n{key}:")
            print(value)

        print()


if __name__ == "__main__":
    while True:
        query = input("\nEnter a codebase question (or 'exit'): ").strip()

        if query.lower() in {"exit", "quit"}:
            break

        if query:
            try:
                search(query)
            except Exception as e:
                print(f"Search failed: {e}")
