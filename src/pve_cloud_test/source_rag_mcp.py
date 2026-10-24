import base64
import hashlib
import logging
import os
import sys
from pathlib import Path

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
EMB_SERVER_URL = os.getenv("EMB_MODEL_SERVER_URL")
EMB_AUTH_HEADER_VALUE = os.getenv("EMB_MODEL_AUTH_HEADER_VALUE")

EMB_MODEL = os.getenv(
    "EMB_MODEL_NAME"
)  # embedidng model name like Qwen/Qwen3-Embedding-8B

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

CHUNK_TEMPLATE = """Project: {{ project }}
File: {{ file_path }}
Type: {{ node_type }}
Lines: {{ start_line }}-{{ end_line }}
---
{{ content }}"""

chunk_template = Template(CHUNK_TEMPLATE)


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
    if len(sys.argv) < 2:
        print("Usage: inserter <project_name>")
        sys.exit(1)

    project_name = sys.argv[1]
    logger.info(f"Processing project: {project_name}")

    # collect chunks and files
    all_chunks_by_file: dict[str, list] = {}
    detected_files: set[str] = set()

    for path in sorted(Path.cwd().rglob("*.py")):
        file_path_str = str(path)
        detected_files.add(file_path_str)
        try:
            chunks = chunk_file(file_path_str, language="python")
            all_chunks_by_file[file_path_str] = chunks
        except Exception as exc:
            print(f"Failed to chunk {path}: {exc}")

    if not all_chunks_by_file:
        logger.warning("No chunks were generated")
        return

    # init the collection
    if not qdrant_client.collection_exists(QDRANT_COLLECTION):
        sample_file_chunks = next(iter(all_chunks_by_file.values()))
        if sample_file_chunks:
            sample_emb = (
                openai_client.embeddings.create(
                    model=EMB_MODEL, input=[sample_file_chunks[0].content]
                )
                .data[0]
                .embedding
            )
            qdrant_client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=len(sample_emb),
                    distance=models.Distance.COSINE,
                ),
                # index columns
                payload_schema={
                    "file_path": models.PayloadSchemaType.KEYWORD,
                    "project": models.PayloadSchemaType.KEYWORD,
                },
            )

    for file_path_str, chunks in all_chunks_by_file.items():
        if not chunks:
            continue

        # cleanup old snippets
        qdrant_client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="file_path",
                            match=models.MatchValue(value=file_path_str),
                        ),
                        models.FieldCondition(
                            key="project",
                            match=models.MatchValue(value=project_name),
                        ),
                    ]
                )
            ),
        )

        # insert fresh chunked
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start : start + BATCH_SIZE]

            response = openai_client.embeddings.create(
                model=EMB_MODEL,
                input=[
                    chunk_template.render(
                        project=project_name,
                        file_path=file_path_str,
                        node_type=chunk.node_type,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                    )
                    for chunk in batch
                ],
            )

            response_data = sorted(response.data, key=lambda item: item.index)
            embeddings = [item.embedding for item in response_data]

            points = []
            for chunk, embedding in zip(batch, embeddings):
                chunk_id = hashlib.sha256(
                    f"{file_path_str}:{chunk.start_line}:{chunk.end_line}".encode()
                ).hexdigest()[:64]
                # render for db insert again
                # todo: duplicated rendering merge with rendering above and maybe
                # extend zip
                rendered = chunk_template.render(
                    project=project_name,
                    file_path=file_path_str,
                    node_type=chunk.node_type,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                )
                points.append(
                    models.PointStruct(
                        id=chunk_id,
                        vector=embedding,
                        payload={
                            "content": rendered,
                            "file_path": file_path_str,
                            "project": project_name,
                        },
                    )
                )

            qdrant_client.upsert(collection_name=QDRANT_COLLECTION, points=points)

    # iterate over vector db and fetch all file names for project
    # => we want to prune files that got deleted
    existing_file_paths: set[str] = set()
    offset: str | None = None

    while True:
        scroll_kwargs = {
            "collection_name": QDRANT_COLLECTION,
            "scroll_filter": models.Filter(
                must=[
                    models.FieldCondition(
                        key="project",
                        match=models.MatchValue(value=project_name),
                    ),
                ]
            ),
            "with_payload": True,
            "with_vectors": False,
            "limit": 100000,
        }
        if offset:
            scroll_kwargs["offset"] = offset

        points, next_offset = qdrant_client.scroll(**scroll_kwargs)

        for point in points:
            if "file_path" in point.payload:
                existing_file_paths.add(point.payload["file_path"])

        if next_offset is None:
            break
        offset = next_offset

    # prune deleted files
    files_to_prune = existing_file_paths - detected_files
    if files_to_prune:
        logger.info(f"Pruning {len(files_to_prune)} files from project {project_name}")
        for file_path_str in files_to_prune:
            qdrant_client.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="file_path",
                                match=models.MatchValue(value=file_path_str),
                            ),
                            models.FieldCondition(
                                key="project",
                                match=models.MatchValue(value=project_name),
                            ),
                        ]
                    )
                ),
            )
        logger.info(f"Pruned {len(files_to_prune)} files")
