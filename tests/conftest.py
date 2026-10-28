"""Shared test fixtures."""

from __future__ import annotations

import pytest

# A post that passes every lint rule on its own. Tests append or swap lines to trip one rule.
CLEAN_POST = """Last month a returns flow broke because two systems disagreed on what "cancelled" meant.

Nothing crashed. Orders just sat in a queue while each side waited for the other to move first.

I keep thinking about that when people talk about agents that take actions. A chatbot that writes a bad paragraph wastes a minute of your time. An agent that issues a refund twice costs real money, and somebody has to explain it to finance on a Friday evening.

So the question I ask now is less about how smart the model is. It is about what happens when the model is wrong. Can I see what it did? Can I undo it? Does it stop and ask before touching money or customer data?

Those answers come from boring engineering. Idempotent calls, clear states, logs a human can read, and a person who signs off on anything expensive or hard to reverse.

The model gets the headlines. The plumbing decides whether anyone trusts it enough to let it near production.

#AgenticAI #EnterpriseAI"""


@pytest.fixture
def clean_post() -> str:
    return CLEAN_POST
