import asyncio

from customer_service.intents.service import (
    IntentHistoryMessage,
    IntentService,
)
from customer_service.knowledge.chat import ChatMessage
from script.evaluate_intents import DEFAULT_CASES_PATH, load_cases


class FakeClassifier:
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[ChatMessage] = []
        self.purpose = ""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: str = "chat",
    ) -> str:
        self.messages = messages
        self.purpose = purpose
        return self.response


def test_rules_extract_withdrawal_order_without_model_call() -> None:
    classifier = FakeClassifier("not used")
    service = IntentService(classifier)

    decision = asyncio.run(service.recognize("帮我查询 wd-10001"))

    assert decision.route == "business_query"
    assert decision.topic == "withdrawal"
    assert decision.entities == {"order_id": "WD-10001"}
    assert decision.missing_fields == ()
    assert classifier.messages == []


def test_rules_extract_deposit_txid_without_model_call() -> None:
    classifier = FakeClassifier("not used")
    service = IntentService(classifier)

    decision = asyncio.run(service.recognize("帮我查询 tx-10001"))

    assert decision.route == "business_query"
    assert decision.topic == "deposit"
    assert decision.entities == {"txid": "TX-10001"}
    assert decision.missing_fields == ()
    assert classifier.messages == []


def test_rules_request_missing_withdrawal_order() -> None:
    decision = asyncio.run(
        IntentService(None).recognize("提现处理到什么进度了？")
    )

    assert decision.route == "business_query"
    assert decision.missing_fields == ("order_id",)


def test_model_classifies_topic_and_extracts_entities_with_history() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","topic":"identity_verification",'
        '"confidence":0.92,"entities":{"verification_type":"个人认证",'
        '"failure_reason":"证件模糊"},"missing_fields":[]}'
    )
    service = IntentService(classifier)

    decision = asyncio.run(
        service.recognize(
            "还是失败",
            history=[
                IntentHistoryMessage(
                    role="user",
                    content="个人认证提示证件照片模糊",
                )
            ],
        )
    )

    assert decision.topic == "identity_verification"
    assert decision.entities == {
        "verification_type": "个人认证",
        "failure_reason": "证件模糊",
    }
    assert "个人认证提示证件照片模糊" in classifier.messages[1]["content"]
    assert classifier.purpose == "intent"


def test_low_confidence_result_falls_back_to_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","topic":"deposit","confidence":0.2,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("这个怎么处理"))

    assert decision.route == "unknown"
    assert decision.confidence == 0


def test_invalid_or_unexpected_fields_fall_back_to_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","topic":"deposit","confidence":0.9,'
        '"entities":{"password":"secret"},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("业务问题"))

    assert decision.route == "unknown"
    assert decision.entities == {}


def test_non_withdrawal_business_route_is_forced_to_rag() -> None:
    classifier = FakeClassifier(
        '{"route":"business_query","topic":"account_security",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("我的账户被盗了"))

    assert decision.route == "knowledge_rag"
    assert decision.topic == "account_security"


def test_concrete_issue_is_preferred_over_human_request() -> None:
    classifier = FakeClassifier(
        '{"route":"human_request","topic":"identity_verification",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("找人工，我的实名认证一直失败")
    )

    assert decision.route == "knowledge_rag"


def test_human_only_request_is_normalized_to_other_topic() -> None:
    classifier = FakeClassifier(
        '{"route":"human_request","topic":"general_platform",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("我要找人工客服"))

    assert decision.route == "human_request"
    assert decision.topic == "other"
    assert classifier.messages == []


def test_unconfigured_model_returns_unknown_for_non_rule_query() -> None:
    decision = asyncio.run(IntentService(None).recognize("实名认证失败"))

    assert decision.route == "unknown"


def test_generic_withdrawal_failure_uses_model_instead_of_business_rule() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","topic":"withdrawal","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("提现失败一般是什么原因？")
    )

    assert decision.route == "knowledge_rag"
    assert decision.topic == "withdrawal"
    assert classifier.messages == []


def test_human_request_with_withdrawal_issue_uses_model() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","topic":"withdrawal","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("帮我转人工处理提现失败的问题")
    )

    assert decision.route == "knowledge_rag"
    assert decision.topic == "withdrawal"
    assert classifier.messages == []


def test_topic_rules_cover_common_high_confidence_queries() -> None:
    cases = [
        ("这个币暂停充值了", "deposit"),
        ("发现陌生登录，请马上告诉我怎么冻结账户", "account_security"),
        ("手机丢了，谷歌验证器怎么解绑", "account_security"),
        ("如何导出我的账户数据", "general_platform"),
    ]

    for query, expected_topic in cases:
        decision = asyncio.run(IntentService(None).recognize(query))

        assert decision.route == "knowledge_rag"
        assert decision.topic == expected_topic


def test_model_withdrawal_business_guess_for_generic_question_is_forced_to_rag() -> None:
    classifier = FakeClassifier(
        '{"route":"business_query","topic":"withdrawal","confidence":0.9,'
        '"entities":{},"missing_fields":["order_id"]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("提现为什么失败"))

    assert decision.route == "knowledge_rag"
    assert decision.topic == "withdrawal"


def test_ambiguous_failure_without_history_remains_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"unknown","topic":"other","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("还是失败"))

    assert decision.route == "unknown"


def test_generic_page_error_remains_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"unknown","topic":"other","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("页面报错了怎么办"))

    assert decision.route == "unknown"


def test_default_intent_evaluation_cases_are_valid() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 60
    assert {case.expected_topic for case in cases} >= {
        "withdrawal",
        "identity_verification",
        "account_security",
    }
