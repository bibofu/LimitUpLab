"""Presentation rules and exact field-token checks; never rewrite market facts."""

PUBLIC_LANGUAGE = """面向个人用户写作：正文、标题、表头、缺口与风险说明都用自然中文，不能照抄内部字段名、字段赋值表达式或枚举代码，也不要在中文后括号附英文键名。
例如break_count=0写成“盘中未炸板”，break_count=2写成“盘中炸板2次”；next_open_pct写成“次日开盘涨幅”，first_limit_time写成“首次封板时间”，turnover_rate写成“换手率”，data_missing写成具体缺少的资料。
只能翻译已有事实的含义，保留数值、单位、日期、有效样本和缺失状态：0不等于缺失；次日开盘涨幅不等于次日收盘涨幅，也不能把次日信息说成首板当天已知。不要仅换成中文键值串，要组织成易读的句子。
英文键名只用于内部工具参数、结构化协议与证据引用；工具计算及table.columns.field仍使用真实原始字段，table.columns.label使用用户可读名称。来源链接、股票代码与正常缩写如ETF、PE可以保留。"""

# Common fields are guarded even when mentioned without a successful tool result.
BASE_FIELDS = frozenset("""break_count next_open_pct next_high_pct next_close_pct
first_limit_time last_limit_time seal_count closed_limit board_height turnover_rate
float_market_cap position_label data_missing trade_date range_days second_break_count
second_open_pct second_limit_time second_turnover_rate second_board_shape
same_industry_limit_up_count market_failed_limit_up_rate three_day_return_pct
five_day_return_pct source_truncated returned_candidate_count universe_count
time_cohort prediction_source premarket_final historical_backtest
symbol score rating confidence amount industry concept""".split())


def field_names(data):
    """Discover exact compound keys in structured evidence, never from prose values."""
    names = set(BASE_FIELDS)
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and "_" in key:
                    names.add(key)
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(data)
    return names


def identifier_char(char):
    return char.isascii() and (char.isalnum() or char == "_")


def field_mentions(text, names=BASE_FIELDS):
    """Lex exact identifiers, allowing source URLs; no regex or text substitution."""
    found, index = set(), 0
    while index < len(text):
        if text.startswith(("https://", "http://"), index):
            while index < len(text) and not text[index].isspace() and text[index] not in '<>)]”"':
                index += 1
        elif identifier_char(text[index]):
            end = index + 1
            while end < len(text) and identifier_char(text[end]):
                end += 1
            token = text[index:end]
            if token in names:
                found.add(token)
            index = end
        else:
            index += 1
    return found


def validate_public_text(text, names=BASE_FIELDS):
    mentions = field_mentions(text, names)
    if mentions:
        raise ValueError("Use natural Chinese meanings, not internal fields: " + ", ".join(sorted(mentions)))


class PublicTextStream:
    """Hold unfinished identifiers so split field tokens never flash in the UI."""
    def __init__(self, names=BASE_FIELDS):
        self.names = names
        self.text = ""
        self.sent = 0
        self.blocked = False

    def feed(self, delta):
        self.text += delta
        if self.blocked:
            return ""
        if field_mentions(self.text, self.names):
            self.blocked = True
            return ""
        end = len(self.text)
        while end > self.sent and identifier_char(self.text[end - 1]):
            end -= 1
        result = self.text[self.sent:end]
        self.sent = end
        return result

    def finish(self):
        validate_public_text(self.text, self.names)
        result = self.text[self.sent:]
        self.sent = len(self.text)
        return result
