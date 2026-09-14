"""Pin LocateAnything remote-code attention onto a DCU-viable implementation.

Issue #41. The LocateAnything remote code defaults the text tower to ``magi``
(``text_attn_impl ... or 'magi'``); when magi_attention is missing it trusts
transformers' ``is_flash_attn_2_available()``, which returns True whenever a
flash_attn package merely *imports* — the locateanything deps venv ships one
whose kernels do not work on DCU. The bundled forward only implements ``magi``
and ``sdpa`` (modeling_qwen2.py raises NotImplementedError for everything
else), so the wrong pick is not a slowdown but a guaranteed crash on every
generate(): scnet-main logged 0 successful /detect calls out of 2838 while
/health kept reporting online.

``sdpa`` is the only implementation the bundled forward fully supports here.
The decoder layers snapshot ``config._attn_implementation`` in ``__init__`` to
choose the attention class, so the pin must land on the config object *before*
the model is constructed — fixing attributes afterwards swaps a flag the
already-built attention modules never re-read.

Deliberately stdlib-only (no torch/transformers import) so it stays unit
testable in CI and importable from any entrypoint.
"""

from __future__ import annotations

from typing import Any, Callable


def pin_text_attention_to_sdpa(config: Any, log: Callable[[str], None] | None = None) -> list[str]:
    """Pin the top-level and text_config attention implementation to ``sdpa``.

    Returns the list of replaced values (order: top first, then nested text
    configs breadth-first, shared objects pinned once). ``vision_config`` is
    left alone on purpose: the remote code gives the vision tower a correct
    flash→sdpa fallback, and it is the one path already verified running on
    DCU.
    """
    if not isinstance(config, object) or isinstance(config, (str, bytes, int, float, bool)):
        return []

    changed: list[str] = []
    seen: set[int] = set()
    stack: list[Any] = [config]
    while stack:
        cfg = stack.pop(0)
        if cfg is None or id(cfg) in seen:
            continue
        seen.add(id(cfg))
        current = getattr(cfg, "_attn_implementation", None)
        if current == "sdpa":
            continue
        try:
            cfg._attn_implementation = "sdpa"
        except AttributeError:  # frozen/slots-only objects cannot be pinned
            continue
        changed.append(str(current))
        if log is not None:
            log(f"[la-attn] pinned {cfg.__class__.__name__} attention "
                f"{current!r} -> 'sdpa' (remote forward implements magi/sdpa only)")
        for sub in ("text_config",):
            stack.append(getattr(cfg, sub, None))
    return changed
