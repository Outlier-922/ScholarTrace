"""Source identity and optional semantic retrieval behavior, without downloads."""
import numpy as np
from types import SimpleNamespace


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(offsets=[(index, index + 1) for index in range(len(text))])


def test_dense_can_recover_nonlexical_evidence_and_preserve_parse_identity(monkeypatch):
    from backend.app import qa
    document = {"id": "doc_a", "title": "A", "version_label": "v1"}
    structure = {"parse_run_id": "parse_original", "blocks": [
        {"id": "block_a", "text": "The system calculates vector representations of text.", "page": 2, "bbox": [0.1, 0.2, 0.8, 0.3], "type": "paragraph"},
        {"id": "ref_a", "text": "[1] Semantic methods, 2024.", "page": 3, "bbox": [0.1, 0.2, 0.8, 0.3], "type": "reference"},
    ]}
    monkeypatch.setattr(qa.store, "get_structure", lambda identity: structure)

    def semantic_search(question, sources, **kwargs):
        assert len(sources) == 1  # Bibliography must be removed before both retrieval paths.
        return [(sources[0]["id"], 0.8)], {"used": True}

    monkeypatch.setattr(qa.semantic, "search", semantic_search)
    details = {}
    evidence = qa._retrieve("如何获得句子的语义编码？", [document], 3, details)
    assert len(evidence) == 1
    assert evidence[0]["parse_run_id"] == "parse_original"
    assert evidence[0]["block_id"] == "block_a"
    assert evidence[0]["dense_score"] == 0.8
    assert details["used"]


def test_embedding_cache_is_bound_to_run_and_purged_with_document(client, monkeypatch):
    from backend.app import semantic
    calls = []

    class Model:
        def embed(self, texts, **kwargs):
            calls.append(list(texts))
            for text in texts:
                vector = np.zeros(384, dtype=np.float32)
                vector[0 if "hardware" in text else 1] = 1
                yield vector

        def query_embed(self, question):
            vector = np.zeros(384, dtype=np.float32)
            vector[0] = 1
            yield vector

    monkeypatch.setattr(semantic, "runtime_status", lambda: {"supported": True, "enabled": True, "ready": True, "model": semantic.MODEL})
    monkeypatch.setattr(semantic, "_load_model", lambda: Model())
    monkeypatch.setattr(semantic, "_tokenizer", lambda: CharacterTokenizer())
    monkeypatch.setattr(semantic.store, "get_document", lambda identity: {"id": identity})
    sources = [{"id": "ev_test", "document_id": "test_semantic_doc", "parse_run_id": "run_1", "block_id": "block_1", "text": "This hardware training setup uses a verifiable configuration."}]
    assert semantic.search("hardware", sources)[1]["used"]
    assert semantic.search("hardware", sources)[1]["used"]
    assert len(calls) == 1
    sources[0]["parse_run_id"] = "run_2"
    semantic.search("hardware", sources)
    assert len(calls) == 2
    semantic.purge_document("test_semantic_doc")
    semantic.search("hardware", sources)
    assert len(calls) == 3


def test_large_unindexed_scope_falls_back_without_partial_semantic_ranking(client, monkeypatch):
    from backend.app import semantic
    monkeypatch.setattr(semantic, "runtime_status", lambda: {"supported": True, "enabled": True, "ready": True})
    monkeypatch.setenv("SCHOLARTRACE_MAX_INLINE_EMBED_CHUNKS", "1")
    monkeypatch.setattr(semantic, "_tokenizer", lambda: CharacterTokenizer())
    sources = [{"id": f"ev_limit{i}", "document_id": "limit_doc", "parse_run_id": "run_limit", "block_id": f"b{i}", "text": "A long academic paragraph requiring a new semantic index entry."} for i in range(3)]
    ranking, details = semantic.search("semantic", sources)
    assert ranking == []
    assert not details["used"]
    assert details["missing_chunks"] == 3


def test_token_windows_cover_long_chinese_tail_without_model_truncation():
    from backend.app import semantic
    text = "科研证据" * 80 + "最后一个关键数值为零点九八。"
    source = {"id": "ev_tail", "document_id": "tail_doc", "parse_run_id": "run_tail", "block_id": "b_tail", "text": text}
    chunks = semantic._chunks([source], CharacterTokenizer())
    assert all(len(item["chunk_text"]) <= 120 for item in chunks)
    assert any("最后一个关键数值为零点九八" in item["chunk_text"] for item in chunks)


def test_cache_write_is_compensated_when_source_is_deleted_between_databases(client, monkeypatch):
    from backend.app import semantic
    checks = []

    class Model:
        def embed(self, texts, **kwargs):
            for _ in texts:
                yield np.ones(384, dtype=np.float32)

        def query_embed(self, question):
            yield np.ones(384, dtype=np.float32)

    def source_exists(identity):
        checks.append(identity)
        return {"id": identity} if len(checks) == 1 else None

    monkeypatch.setattr(semantic, "runtime_status", lambda: {"supported": True, "enabled": True, "ready": True})
    monkeypatch.setattr(semantic, "_tokenizer", lambda: CharacterTokenizer())
    monkeypatch.setattr(semantic, "_load_model", lambda: Model())
    monkeypatch.setattr(semantic.store, "get_document", source_exists)
    semantic.search("evidence", [{"id": "ev_deleted", "document_id": "deleted_during_index", "parse_run_id": "r_deleted", "block_id": "b_deleted", "text": "Source is removed after the initial existence check."}])
    assert len(checks) >= 2
    with semantic._connection() as connection:
        assert connection.execute("SELECT count(*) FROM vectors WHERE document_id=?", ("deleted_during_index",)).fetchone()[0] == 0
