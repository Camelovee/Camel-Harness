import pytest

from camel_harness.agent.session import SessionLog, SessionLogEvent
from camel_harness.llm import UserMessage


def test_session_log_replays_append_and_replace_operations() -> None:
    messages = tuple(UserMessage(f"原消息 {index}") for index in range(4))
    summary = UserMessage("[Compacted History]\n\n摘要")
    events = tuple(
        SessionLogEvent.append(index, index, message) for index, message in enumerate(messages)
    ) + (SessionLogEvent.replace(4, 0, 2, summary),)

    projected = SessionLog("session-1", events).derive_messages()

    assert projected == (summary, messages[3])


def test_session_log_rejects_sequence_gaps() -> None:
    event = SessionLogEvent.append(1, 0, UserMessage("消息"))

    with pytest.raises(ValueError, match="连续递增"):
        SessionLog("session-1", (event,))


def test_session_log_returns_immutable_message_snapshot() -> None:
    message = UserMessage("消息")
    log = SessionLog("session-1", (SessionLogEvent.append(0, 0, message),))

    projected = log.derive_messages()

    assert isinstance(projected, tuple)
    assert projected[0].content == "消息"


def test_session_log_keeps_summary_before_recent_messages_across_replacements() -> None:
    log = SessionLog("session-1")
    originals = tuple(UserMessage(f"消息 {index}") for index in range(12))
    for index, message in enumerate(originals[:8]):
        log.append_message(index, message)
    first_summary = UserMessage("第一次摘要")
    log.replace_context(0, 5, first_summary)
    for index, message in enumerate(originals[8:], start=8):
        log.append_message(index, message)
    second_summary = UserMessage("第二次摘要")

    log.replace_context(6, 10, second_summary)

    assert log.derive_messages() == (second_summary, originals[10], originals[11])
