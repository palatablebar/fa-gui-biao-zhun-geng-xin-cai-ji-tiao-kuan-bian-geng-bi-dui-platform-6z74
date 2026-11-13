"""判级规则包注册表：切分 / 对齐 / 判级三段口径整体版本化。

设计要点：
- 每个规则包是一份**不可变**配置（RuleContext），算法函数只吃上下文、不读全局设置，
  因此历史结论可以逐位复现；升级口径 = 注册一个新版本，而不是改旧版本的参数。
- 每份 DiffRun 落库时快照 ``rule_config`` 与 ``rule_bundle_version``，
  即使后续注册表继续演进，旧批次仍然绑定「当次切分、对齐和判级规则」。
- v1.0.0 的参数与升级前逐字一致（match=0.55 / change=0.86 / 一般阈值 0.08），
  保证升级前已经落库的结论口径不变；v2.0.0 是升级后的新口径。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 当前生效的规则包版本：新建比对默认使用；重算可以指定任意已注册版本。
CURRENT_RULE_BUNDLE_VERSION = "2.0.0"

# 升级前的历史口径版本（seed / 升级前批次固定绑定）。
LEGACY_RULE_BUNDLE_VERSION = "1.0.0"


@dataclass(frozen=True)
class SplitterRules:
    """切分口径。v1/v2 正则一致，版本化是为了让三段规则同时可追溯。"""

    article_pattern: str = r"第\s*([一二三四五六七八九十百千零〇两0-9]{1,8})\s*条"
    sub_item_pattern: str = r"^\s*[（(]\s*([一二三四五六七八九十]{1,4})\s*[）)]"
    prefix_symbols: str = "　 \t:：、.。"


@dataclass(frozen=True)
class AlignerRules:
    """对齐口径：配对阈值 / 变更阈值 / 条号破平权重 / 混合相似度权重。"""

    match_threshold: float = 0.55
    change_threshold: float = 0.86
    number_bonus: float = 0.02
    tfidf_weight: float = 0.7
    simhash_weight: float = 0.3


@dataclass(frozen=True)
class ClassifierRules:
    """判级口径。

    general_edit_ratio_threshold：修改条款进入「一般」的编辑幅度下限。
    escalate_mandatory_wording：措辞级修改（未触发关键词组/数值变化）但新文本仍含
        强制性义务词时，是否升级为「一般」（v2 新增，避免义务措辞调整被静默吞掉）。
    keyword_groups / industry_keywords：词典快照（默认引用算法模块的现行词典）。
    """

    general_edit_ratio_threshold: float = 0.08
    escalate_mandatory_wording: bool = False
    keyword_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    industry_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleContext:
    """一次比对使用的完整规则上下文（落库快照即它的 dict 形式）。"""

    bundle_version: str
    splitter: SplitterRules
    aligner: AlignerRules
    classifier: ClassifierRules
    change_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _default_classifier_keywords() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    # 延迟导入，避免规则模块与算法模块循环依赖。
    from src.services.change_classifier import INDUSTRY_KEYWORDS, KEYWORD_GROUPS

    return dict(KEYWORD_GROUPS), dict(INDUSTRY_KEYWORDS)


def _build_registry() -> dict[str, RuleContext]:
    keyword_groups, industry_keywords = _default_classifier_keywords()

    v1 = RuleContext(
        bundle_version=LEGACY_RULE_BUNDLE_VERSION,
        splitter=SplitterRules(),
        aligner=AlignerRules(match_threshold=0.55, change_threshold=0.86, number_bonus=0.02),
        classifier=ClassifierRules(
            general_edit_ratio_threshold=0.08,
            escalate_mandatory_wording=False,
            keyword_groups=keyword_groups,
            industry_keywords=industry_keywords,
        ),
        change_summary="升级前历史口径：配对阈值 0.55、变更阈值 0.86、一般变更编辑幅度阈值 0.08。",
    )

    v2 = RuleContext(
        bundle_version=CURRENT_RULE_BUNDLE_VERSION,
        splitter=SplitterRules(),
        aligner=AlignerRules(match_threshold=0.75, change_threshold=0.80, number_bonus=0.02),
        classifier=ClassifierRules(
            general_edit_ratio_threshold=0.05,
            escalate_mandatory_wording=True,
            keyword_groups=keyword_groups,
            industry_keywords=industry_keywords,
        ),
        change_summary=(
            "新口径：配对阈值收紧到 0.75、变更阈值 0.80；一般变更编辑幅度阈值收紧到 0.05；"
            "强制性义务措辞修改即使幅度很小也升级为一般变更。"
        ),
    )

    return {v1.bundle_version: v1, v2.bundle_version: v2}


_REGISTRY = _build_registry()


def registered_versions() -> list[str]:
    """按版本号排序返回全部已注册规则包。"""
    return sorted(_REGISTRY, key=lambda value: tuple(int(part) for part in value.split(".")))


def get_rule_context(version: str) -> RuleContext:
    """取规则包；未注册版本直接报错（不允许静默回退到别的口径）。"""
    context = _REGISTRY.get(version)
    if context is None:
        raise KeyError(f"未注册的规则包版本: {version}，可选 {registered_versions()}")
    return context


def context_from_config(rule_config: dict) -> RuleContext:
    """从 DiffRun 快照的 rule_config 重建上下文（历史复算走这里，不依赖注册表演进）。"""
    return RuleContext(
        bundle_version=rule_config["bundle_version"],
        splitter=SplitterRules(**rule_config["splitter"]),
        aligner=AlignerRules(**rule_config["aligner"]),
        classifier=ClassifierRules(**rule_config["classifier"]),
        change_summary=rule_config.get("change_summary", ""),
    )
