from puller.config.schema import LatestRuleConfig, RuleConfig, SemverRuleConfig, TagRuleConfig
from puller.rules.base import ResolvedRef, Rule, RuleError
from puller.rules.semver_rule import SemverRule
from puller.rules.tag_rule import TagRule

__all__ = ["ResolvedRef", "Rule", "RuleError", "SemverRule", "TagRule", "build_rule"]


def build_rule(config: RuleConfig) -> Rule:
    if isinstance(config, SemverRuleConfig):
        return SemverRule(
            prefix=config.prefix,
            pattern=config.pattern,
            include_prerelease=config.include_prerelease,
        )
    if isinstance(config, TagRuleConfig):
        return TagRule(tag=config.tag)
    if isinstance(config, LatestRuleConfig):
        return TagRule(tag="latest")
    raise TypeError(f"unsupported rule config type: {type(config)!r}")
