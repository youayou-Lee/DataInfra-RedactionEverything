"""HaS 实体 confidence=None 时语义共指传播不应崩溃（云实例实测 traceback）。"""

from app.models.entity_schemas import Entity


def test_propagate_confidence_none():
    from app.services.hybrid_ner_service import HybridNERService

    svc = HybridNERService.__new__(HybridNERService)
    source = Entity(
        id="e1", text="广西防城港佳润房地产开发有限责任公司", type="INSTITUTION_NAME",
        start=0, end=16, page=1, confidence=None, source="has",
    )
    text = "广西防城港佳润房地产开发有限责任公司与佳润公司签约，佳润公司又盖章。"
    propagated = svc._propagate_confirmed_semantic_mentions([source], text)
    # 不崩溃即通过；confidence 回退为有限数值
    for p in propagated:
        assert p.confidence is not None and p.confidence <= 0.9


def test_propagate_confidence_zero():
    """confidence=0.0 是实测分数，不应被回退成 0.9。"""
    from app.services.hybrid_ner_service import HybridNERService

    svc = HybridNERService.__new__(HybridNERService)
    source = Entity(
        id="e1", text="广西防城港佳润房地产开发有限责任公司", type="INSTITUTION_NAME",
        start=0, end=16, page=1, confidence=0.0, source="has",
    )
    text = "广西防城港佳润房地产开发有限责任公司与佳润公司签约，佳润公司又盖章。"
    propagated = svc._propagate_confirmed_semantic_mentions([source], text)
    for p in propagated:
        assert p.confidence == 0.0
