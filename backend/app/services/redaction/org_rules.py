"""组织类实体的化名规则 — Issue #55 / #56。

两类职责：
1. 子类型分池：按原文后缀把律所/法院/检察院/医院/学校等组织路由到
   对应词池，避免全部吸进 INSTITUTION_NAME 公司池后变成「某公司」。
2. 公共机构白名单：国家机关与司法行政类公共机构（司法部、人民法院、
   律师事务中心等）在化名模式下默认保留原文，不参与匿名化。

白名单为「后缀 + 关键词」双通道匹配，未枚举到的机关按后缀兜底；
确需替换的白名单主体可在实体面板手动指定 custom_map 覆盖（不走本规则）。
"""
from __future__ import annotations

from app.models.type_mapping import TYPE_REGISTRY

# 化名模式下默认保留原文的公共机构后缀。
# 范围=国家部委级机关 + 司法行政类公共机构；地方机关（公安局/法院/司法
# 局等）不在白名单——preview2.0.0 已验收行为是照常匿名化（某人民法院1）。
PUBLIC_INSTITUTION_SUFFIXES: tuple[str, ...] = (
    "律师事务中心",
    "法律援助中心",
    "政务服务中心",
    "公证处",
)

# 关键词通道：出现即视为公共机构（覆盖不以固定后缀结尾的名称）
PUBLIC_INSTITUTION_KEYWORDS: tuple[str, ...] = (
    "国务院",
    "最高人民法院",
    "最高人民检察院",
    "政法委",
)

# 「部」单独作后缀歧义大（如「某某贸易部」），仅限常见部委名组合按机关保留
MINISTRY_PREFIXES: tuple[str, ...] = (
    "司法",
    "公安",
    "国家安全",
    "外交",
    "国防",
    "教育",
    "科技",
    "工业和信息化",
    "民政",
    "财政",
    "人力资源",
    "自然资源",
    "生态环境",
    "住房",
    "交通",
    "水利",
    "农业",
    "商务",
    "文化",
    "卫生",
    "退役",
    "应急",
    "审计",
)

# 后缀歧义保护：命中「部/厅/局/署」但含经营主体字样的名称不按机关保留
_AMBIGUOUS_SUFFIX_EXCLUDE_MARKERS: tuple[str, ...] = (
    "公司", "企业", "集团", "事务所", "银行", "贸易",
)

# 组织子类型 → 词池键：按原文后缀路由（Issue #55）
# 法院/检察院不在此列：它们照常匿名化，由 _pool_key_for 的机关关键词精化
# 路由到 GOVERNMENT_AGENCY 池（preview2.0.0 已验收行为，勿劫走）。
ORG_POOL_RULES: tuple[tuple[str, str], ...] = (
    ("律师事务所", "LAW_FIRM"),
    ("医院", "HOSPITAL"),
    ("卫生院", "HOSPITAL"),
    ("大学", "SCHOOL"),
    ("学院", "SCHOOL"),
    ("学校", "SCHOOL"),
    ("中学", "SCHOOL"),
    ("小学", "SCHOOL"),
)

# organization_like 类型集合（含派生/别名键），供替换层判断是否适用组织规则
_ORG_LIKE_BASE = {
    tid for tid, meta in TYPE_REGISTRY.items() if "organization_like" in (meta.get("groups") or [])
}
ORG_LIKE_TYPES = _ORG_LIKE_BASE | {
    "GOVERNMENT_AGENCY",
    "LEGAL_LAW_FIRM",
    "LEGAL_COURT",
    "COMPANY_NAME",
    "INSTITUTION_NAME",
}


def is_org_like(type_key: str) -> bool:
    return type_key in ORG_LIKE_TYPES


def org_pool_key_for(text: str) -> str | None:
    """按原文后缀返回组织子类型词池键；非组织或无规则命中返回 None。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    for suffix, pool_key in ORG_POOL_RULES:
        if stripped.endswith(suffix):
            return pool_key
    return None


def is_public_institution(text: str) -> bool:
    """判断组织名称是否为默认保留原文的公共机构（Issue #56）。"""
    stripped = (text or "").strip()
    if not stripped:
        return False
    # 规范化：去国号前缀，如「中华人民共和国司法部」→「司法部」
    normalized = stripped.removeprefix("中华人民共和国")
    ambiguous_hit = normalized.endswith("部") and any(
        m in normalized for m in _AMBIGUOUS_SUFFIX_EXCLUDE_MARKERS
    )
    if not ambiguous_hit:
        if any(normalized.endswith(suffix) for suffix in PUBLIC_INSTITUTION_SUFFIXES):
            return True
        if any(keyword in normalized for keyword in PUBLIC_INSTITUTION_KEYWORDS):
            return True
        if any(normalized.endswith(prefix + "部") for prefix in MINISTRY_PREFIXES):
            return True
    return False
