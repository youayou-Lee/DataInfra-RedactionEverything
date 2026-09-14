"""Issue #41：LocateAnything attention 选型预置单测。

scnet-main 实锤：远程码 text 塔默认 ``magi``，magi_attention 缺失时依赖
``is_flash_attn_2_available()`` 回落——deps 环境里的 flash_attn 包让它误报
True，选到远程码 forward 根本没实现的 ``flash_attention_2``，每次推理必抛
NotImplementedError（/detect 0 成功 / 2838 失败）。修法：模型构造前把
顶层与 text_config 显式钉在 ``sdpa``。vision_config 不动——它自带正确的
flash→sdpa 回落且在实例上已实测可跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts"))

from la_attention_compat import pin_text_attention_to_sdpa  # noqa: E402


class _Cfg:
    """最小 config 鸭子类型：只携带 _attn_implementation 与嵌套子配置。"""

    def __init__(self, attn=None, text=None, vision=None):
        if attn is not None:
            self._attn_implementation = attn
        if text is not None:
            self.text_config = text
        if vision is not None:
            self.vision_config = vision


def test_pins_flash_attention_2_text_config():
    text = _Cfg(attn="flash_attention_2")
    top = _Cfg(attn="magi", text=text, vision=_Cfg(attn="flash_attention_2"))

    changed = pin_text_attention_to_sdpa(top)

    assert text._attn_implementation == "sdpa"
    assert top._attn_implementation == "sdpa"
    # vision 塔有自己的正确回落且实测可跑，不得顺手动它
    assert top.vision_config._attn_implementation == "flash_attention_2"
    assert set(changed) == {"flash_attention_2", "magi"}


def test_pins_missing_attr_so_remote_magi_default_never_fires():
    # 远程码在 text_config 无值时回落到顶层，再兜底 'magi'——
    # 所以 None 也必须显式钉死，不能留给 'or magi' 链
    text = _Cfg()
    top = _Cfg(text=text)

    changed = pin_text_attention_to_sdpa(top)

    assert text._attn_implementation == "sdpa"
    assert top._attn_implementation == "sdpa"
    # 缺失属性按 None 记录，顶层在前、text_config 在后
    assert changed == ["None", "None"]


def test_noop_when_already_sdpa():
    text = _Cfg(attn="sdpa")
    top = _Cfg(attn="sdpa", text=text)

    changed = pin_text_attention_to_sdpa(top)

    assert changed == []


def test_survives_missing_text_config():
    top = _Cfg(attn="magi")

    changed = pin_text_attention_to_sdpa(top)

    assert top._attn_implementation == "sdpa"
    assert changed == ["magi"]


def test_handles_shared_and_cyclic_configs():
    text = _Cfg(attn="magi")
    top = _Cfg(attn="flash_attention_2", text=text)
    top.text_config.text_config = text  # 环引用：同一对象只处理一次

    changed = pin_text_attention_to_sdpa(top)

    assert text._attn_implementation == "sdpa"
    assert len(changed) == 2


@pytest.mark.parametrize("bad", [None, object()])
def test_tolerates_unexpected_config_shapes(bad):
    assert pin_text_attention_to_sdpa(bad) == []
