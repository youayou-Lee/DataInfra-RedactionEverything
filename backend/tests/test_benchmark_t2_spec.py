# backend/tests/test_benchmark_t2_spec.py
import json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import spec  # noqa: E402


def _preset_names():
    d = json.loads((REPO / "backend" / "config" / "preset_entity_types.json").read_text(encoding="utf-8"))
    return {v["name"] for v in d.values()}


def test_type_maps_target_preset():
    names = _preset_names()
    for ds, mapping in spec.TYPE_MAPS.items():
        for src_t, our_t in mapping.items():
            assert our_t in names, f"{ds}: {src_t}->{our_t} 目标不在 preset"


def test_buckets_structure():
    for bucket, meta in spec.BUCKETS.items():
        assert meta["kind"] in ("public", "synthetic", "hardcase")
        assert meta["source"] in ("cluener", "leven", "resume", "generator", "ingest")
        assert isinstance(meta["size"], int) and meta["size"] > 0
        assert meta["input_modality"] == "text"


def test_bucket_sources_match_type_maps():
    for bucket, meta in spec.BUCKETS.items():
        if meta["source"] in ("cluener", "leven", "resume"):
            assert meta["source"] in spec.TYPE_MAPS
