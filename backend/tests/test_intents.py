import asyncio

from customer_service.intents.service import (
    IntentDecision,
    IntentHistoryMessage,
    IntentService,
)
from customer_service.knowledge.chat import ChatMessage
from script.evaluate_intents import (
    DEFAULT_CASES_PATH,
    IntentEvaluationCase,
    IntentEvaluationResult,
    load_cases,
    summarize_results,
)


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
    assert decision.category == "withdrawal"
    assert decision.intent == "status_query"
    assert decision.entities == {"order_id": "WD-10001"}
    assert decision.missing_fields == ()
    assert decision.source == "rule"
    assert classifier.messages == []


def test_rules_extract_deposit_txid_without_model_call() -> None:
    classifier = FakeClassifier("not used")
    service = IntentService(classifier)

    decision = asyncio.run(service.recognize("帮我查询 tx-10001"))

    assert decision.route == "business_query"
    assert decision.category == "deposit"
    assert decision.intent == "status_query"
    assert decision.entities == {"txid": "TX-10001"}
    assert decision.missing_fields == ()
    assert decision.source == "rule"
    assert classifier.messages == []


def test_rules_request_missing_withdrawal_order() -> None:
    decision = asyncio.run(
        IntentService(None).recognize("提现处理到什么进度了？")
    )

    assert decision.route == "business_query"
    assert decision.intent == "missing_arrival"
    assert decision.missing_fields == ("order_id",)
    assert decision.source == "rule"


def test_model_classifies_category_and_extracts_entities_with_history() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","category":"identity_verification",'
        '"intent":"verification_failure",'
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

    assert decision.category == "identity_verification"
    assert decision.intent == "verification_failure"
    assert decision.entities == {
        "verification_type": "个人认证",
        "failure_reason": "证件模糊",
    }
    assert decision.source == "model"
    assert "个人认证提示证件照片模糊" in classifier.messages[1]["content"]
    assert classifier.purpose == "intent"


def test_low_confidence_result_falls_back_to_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","category":"deposit",'
        '"intent":"missing_arrival","confidence":0.2,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("这个怎么处理"))

    assert decision.route == "unknown"
    assert decision.confidence == 0
    assert decision.source == "fallback"


def test_invalid_or_unexpected_fields_fall_back_to_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","category":"deposit",'
        '"intent":"missing_arrival","confidence":0.9,'
        '"entities":{"password":"secret"},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("业务问题"))

    assert decision.route == "unknown"
    assert decision.entities == {}
    assert decision.source == "fallback"


def test_non_withdrawal_business_route_is_forced_to_rag() -> None:
    classifier = FakeClassifier(
        '{"route":"business_query","category":"account_security",'
        '"intent":"compromised",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("我的账户被盗了"))

    assert decision.route == "knowledge_rag"
    assert decision.category == "account_security"
    assert decision.intent == "compromised"


def test_concrete_issue_is_preferred_over_human_request() -> None:
    classifier = FakeClassifier(
        '{"route":"human_request","category":"identity_verification",'
        '"intent":"verification_failure",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("找人工，我的实名认证一直失败")
    )

    assert decision.route == "knowledge_rag"


def test_human_only_request_is_normalized_to_other_category() -> None:
    classifier = FakeClassifier(
        '{"route":"human_request","category":"general_platform",'
        '"intent":"platform_operation",'
        '"confidence":0.9,"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("我要找人工客服"))

    assert decision.route == "human_request"
    assert decision.category == "other"
    assert decision.intent == "human_only"
    assert classifier.messages == []


def test_unconfigured_model_routes_identity_failure_with_rules() -> None:
    decision = asyncio.run(IntentService(None).recognize("实名认证失败"))

    assert decision.route == "knowledge_rag"
    assert decision.category == "identity_verification"
    assert decision.intent == "verification_failure"


def test_generic_withdrawal_failure_uses_model_instead_of_business_rule() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","category":"withdrawal",'
        '"intent":"failure_reason","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("提现失败一般是什么原因？")
    )

    assert decision.route == "knowledge_rag"
    assert decision.category == "withdrawal"
    assert classifier.messages == []


def test_human_request_with_withdrawal_issue_uses_model() -> None:
    classifier = FakeClassifier(
        '{"route":"knowledge_rag","category":"withdrawal",'
        '"intent":"failure_reason","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(
        IntentService(classifier).recognize("帮我转人工处理提现失败的问题")
    )

    assert decision.route == "knowledge_rag"
    assert decision.category == "withdrawal"
    assert classifier.messages == []


def test_category_rules_cover_common_high_confidence_queries() -> None:
    cases = [
        ("这个币暂停充值了", "deposit"),
        ("发现陌生登录，请马上告诉我怎么冻结账户", "account_security"),
        ("手机丢了，谷歌验证器怎么解绑", "account_security"),
        ("个人身份认证总是失败，提示证件照片模糊", "identity_verification"),
        ("为什么我的现货限价单一直没有成交？", "spot_trading"),
        ("如何导出我的账户数据", "general_platform"),
    ]

    for query, expected_category in cases:
        decision = asyncio.run(IntentService(None).recognize(query))

        assert decision.route == "knowledge_rag"
        assert decision.category == expected_category


def test_identity_rules_distinguish_failure_and_general_questions() -> None:
    failure = asyncio.run(IntentService(None).recognize("人脸识别一直过不了"))
    general = asyncio.run(IntentService(None).recognize("企业认证需要准备哪些资料"))
    expired_document = asyncio.run(
        IntentService(None).recognize("身份证过期会导致认证失败吗")
    )
    name_change = asyncio.run(IntentService(None).recognize("认证后还能修改姓名吗"))

    assert failure.route == "knowledge_rag"
    assert failure.category == "identity_verification"
    assert failure.intent == "verification_failure"
    assert expired_document.route == "knowledge_rag"
    assert expired_document.category == "identity_verification"
    assert expired_document.intent == "verification_failure"
    assert general.route == "knowledge_rag"
    assert general.category == "identity_verification"
    assert general.intent == "rule"
    assert name_change.route == "knowledge_rag"
    assert name_change.category == "identity_verification"
    assert name_change.intent == "rule"


def test_withdrawal_rules_cover_common_policy_questions_without_model() -> None:
    cases = [
        ("USDT 提现支持哪些网络？", "rule"),
        ("每天最多可以提现吗？", "rule"),
        ("已经提交的提现可以取消吗", "rule"),
        ("我的提現怎么还在处理中", "missing_arrival"),
    ]

    for query, expected_intent in cases:
        decision = asyncio.run(IntentService(None).recognize(query))

        assert decision.route == "knowledge_rag"
        assert decision.category == "withdrawal"
        assert decision.intent == expected_intent


def test_spot_trading_rules_route_to_knowledge_without_model() -> None:
    decision = asyncio.run(
        IntentService(None).recognize("下了限价单以后余额为什么被冻结")
    )

    assert decision.route == "knowledge_rag"
    assert decision.category == "spot_trading"
    assert decision.intent == "trading_question"


def test_out_of_scope_rules_skip_model_for_obvious_non_exchange_queries() -> None:
    cooking = asyncio.run(IntentService(None).recognize("红烧肉怎么做？"))
    stock = asyncio.run(IntentService(None).recognize("帮我分析一下这只股票能买吗"))

    assert cooking.route == "out_of_scope"
    assert cooking.category == "other"
    assert cooking.intent == "out_of_scope"
    assert stock.route == "out_of_scope"
    assert stock.category == "other"
    assert stock.intent == "out_of_scope"


def test_human_request_with_identity_issue_uses_rules() -> None:
    decision = asyncio.run(IntentService(None).recognize("找人工，我的实名认证一直失败"))

    assert decision.route == "knowledge_rag"
    assert decision.category == "identity_verification"
    assert decision.intent == "verification_failure"


def test_model_withdrawal_business_guess_for_generic_question_is_forced_to_rag() -> None:
    classifier = FakeClassifier(
        '{"route":"business_query","category":"withdrawal",'
        '"intent":"failure_reason","confidence":0.9,'
        '"entities":{},"missing_fields":["order_id"]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("提现为什么失败"))

    assert decision.route == "knowledge_rag"
    assert decision.category == "withdrawal"


def test_ambiguous_failure_without_history_remains_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"unknown","category":"other","intent":"unknown","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("还是失败"))

    assert decision.route == "unknown"


def test_generic_page_error_remains_unknown() -> None:
    classifier = FakeClassifier(
        '{"route":"unknown","category":"other","intent":"unknown","confidence":0.9,'
        '"entities":{},"missing_fields":[]}'
    )

    decision = asyncio.run(IntentService(classifier).recognize("页面报错了怎么办"))

    assert decision.route == "unknown"


def test_default_intent_evaluation_cases_are_valid() -> None:
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 74
    cases_by_id = {case.case_id: case for case in cases}
    assert cases_by_id["withdrawal_order_status"].expected_entities == {
        "order_id": "WD-10001"
    }
    assert cases_by_id["withdrawal_missing_order"].expected_missing_fields == (
        "order_id",
    )
    assert cases_by_id["frontend_deposit_txid"].expected_entities == {
        "txid": "TX-10001"
    }
    assert {case.expected_category for case in cases} >= {
        "withdrawal",
        "identity_verification",
        "account_security",
    }
    assert {case.expected_intent for case in cases} >= {
        "status_query",
        "missing_arrival",
        "failure_reason",
        "fee_rule",
        "memo_tag_issue",
        "verification_failure",
        "compromised",
        "two_factor_issue",
        "trading_question",
        "platform_operation",
        "human_only",
        "out_of_scope",
        "unknown",
    }


def test_intent_evaluation_summary_counts_mismatch_types() -> None:
    cases = [
        IntentEvaluationCase(
            case_id="ok",
            query="查询 WD-10001",
            expected_route="business_query",
            expected_category="withdrawal",
            expected_intent="status_query",
            expected_entities={"order_id": "WD-10001"},
            expected_missing_fields=(),
        ),
        IntentEvaluationCase(
            case_id="wrong_category",
            query="充值一直没到账",
            expected_route="knowledge_rag",
            expected_category="deposit",
            expected_intent="missing_arrival",
            expected_entities={"txid": "TX-10001"},
        ),
        IntentEvaluationCase(
            case_id="wrong_all",
            query="红烧肉怎么做",
            expected_route="out_of_scope",
            expected_category="other",
            expected_intent="out_of_scope",
            expected_missing_fields=("order_id",),
        ),
    ]
    results = [
        IntentEvaluationResult(
            case=cases[0],
            decision=asyncio.run(IntentService(None).recognize(cases[0].query)),
        ),
        IntentEvaluationResult(
            case=cases[1],
            decision=IntentDecision(
                route="knowledge_rag",
                category="withdrawal",
                intent="missing_arrival",
                confidence=1.0,
                entities={},
                missing_fields=(),
            ),
        ),
        IntentEvaluationResult(
            case=cases[2],
            decision=IntentDecision(
                route="unknown",
                category="other",
                intent="unknown",
                confidence=0,
                entities={},
                missing_fields=(),
                source="fallback",
            ),
        ),
    ]

    summary = summarize_results(results)

    assert summary.total == 3
    assert summary.correct_full == 1
    assert summary.entity_cases == 2
    assert summary.correct_entities == 1
    assert summary.missing_field_cases == 2
    assert summary.correct_missing_fields == 1
    assert summary.source_counts == {"rule": 1, "model": 1, "fallback": 1}
    assert summary.mismatch_counts == {
        "category+entities": 1,
        "route+intent+missing_fields": 1,
    }
