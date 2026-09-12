"""Research output boundary shared by ReAct validation and fallback."""

import re


def unsafe_answer(text):
    # Historical institution buy/sell facts do not match user-directed instructions.
    return bool(re.search(r"(?:建议|应该|可以|务必|立即|推荐)(?:你|您)?(?:现在|明天|逢低|择机)?(?:买入|卖出|加仓|减仓|建仓)|(?:目标价|建议仓位)\s*[:：]?\s*\d|保证.{0,8}(?:盈利|收益|上涨)", text))
