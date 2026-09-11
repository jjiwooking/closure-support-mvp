"""
기능1 가이드 챗봇의 RAG 검색 계층. Gemini 임베딩 API로 벡터를 만들고
Chroma(인메모리)에 넣어 질문과 가장 관련 있는 업무 근거만 추려낸다.

데이터가 아직 작아(업무 몇 개 수준) 매 프로세스 시작마다 새로 임베딩해도
비용이 크지 않으므로 퍼시스턴트 저장소 대신 인메모리 클라이언트를 쓴다.
같은 텍스트를 다시 임베딩하지 않도록 프로세스 내 캐시(lru_cache)와
컬렉션에 이미 있는 id는 건너뛰는 방식을 함께 쓴다.

임베딩 API가 설정되지 않았거나 호출이 실패하면 retrieve()가 None을 반환한다.
호출부는 이 경우 전체 근거를 그대로 LLM에 넘기는 방식으로 폴백해야 한다
(등록된 근거를 놓치고 "확인 필요"로 잘못 답하는 것을 막기 위함).
"""
from functools import lru_cache

import chromadb

from llm_client import generate_embedding

_COLLECTION_NAME = "guide_blocks"
_client = chromadb.Client()


def _collection():
    return _client.get_or_create_collection(name=_COLLECTION_NAME, embedding_function=None)


@lru_cache(maxsize=256)
def _embed_cached(text: str):
    """반환: 임베딩 튜플 또는 실패 시 None. lru_cache가 dict/list를 못 받으므로
    text(str) 하나만 키로 쓰고, 결과는 해시 가능하도록 tuple로 저장한다."""
    result = generate_embedding(text)
    if not result["ok"]:
        return None
    return tuple(result["values"])


def retrieve(question: str, items: list[dict], top_k: int = 3):
    """items: [{"id": str, "text": str}, ...].
    반환: 관련도 높은 순 id 리스트, 또는 임베딩 실패/미설정 시 None(전체 폴백 신호)."""
    if not items:
        return None

    query_vec = _embed_cached(question)
    if query_vec is None:
        return None

    collection = _collection()
    ids = [it["id"] for it in items]
    try:
        existing = set(collection.get(ids=ids)["ids"])
    except Exception:
        existing = set()

    to_add_ids, to_add_embeddings, to_add_docs = [], [], []
    for it in items:
        if it["id"] in existing:
            continue
        vec = _embed_cached(it["text"])
        if vec is None:
            return None
        to_add_ids.append(it["id"])
        to_add_embeddings.append(list(vec))
        to_add_docs.append(it["text"])

    if to_add_ids:
        collection.upsert(ids=to_add_ids, embeddings=to_add_embeddings, documents=to_add_docs)

    result = collection.query(query_embeddings=[list(query_vec)], n_results=min(top_k, len(items)))
    hit_ids = result.get("ids") or [[]]
    return hit_ids[0] if hit_ids[0] else None
