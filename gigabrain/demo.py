"""Demo: runs ready-made conversations with the simulated provider.

Fills a data folder so the dashboard can be explored without an API key. The
conversations cover the main paths of the architecture:

1. a new request involving two topics (creates two experts)
2. a new version of an existing requirement (R1 v2)
3. a requirement that depends on another (R2 depends_on R1)
4. a requirement that supersedes another (R3 supersedes R1, so R2 goes under review)
5. a conversation in "in_meeting" mode (creates no expert, records a pending item)
6. a project document given to an expert
"""

from __future__ import annotations

from .council import Council, feed_expert, open_context
from .providers import SimulatedProvider

CONVERSATIONS = [
    ("after_meeting", [
        "I want to notify the customer when the payment is confirmed",
        "The customer gets an email within 1 minute after the payment is confirmed",
        "approved",
    ]),
    ("after_meeting", [
        "Update R1 to also notify declined payments",
        "approved",
    ]),
    ("after_meeting", [
        "Monthly report of confirmed payments, which depends on R1",
        "The finance team needs the confirmed total per day",
        "approved",
    ]),
    ("after_meeting", [
        "Replace R1 with an in-app notification, without email",
        "approved",
    ]),
    ("in_meeting", [
        "The customer wants to apply a discount coupon in the cart",
        "Each coupon can be used once per customer and has an expiration date",
        "approved",
    ]),
]

DOCUMENT = """\
# Payment rules (minutes of the 09/12 meeting)
- Instant payments are confirmed right away; bank slips take up to 2 business days.
- A declined payment can be retried up to 3 times.
- Only the finance team can issue refunds.
"""


def run(data_folder: str, show=print) -> None:
    for mode, messages in CONVERSATIONS:
        ctx = open_context(data_folder, SimulatedProvider(), mode=mode, show_agents=True)
        council = Council(ctx)
        show(f"\n=== Conversation {council.start()} (mode {mode}) ===")
        for message in messages:
            show(f"PO> {message}")
            answer = council.talk(message)
            show(f"Twin> {answer['message_to_po']}\n")
        ctx.db.close()

    ctx = open_context(data_folder, SimulatedProvider(), show_agents=True)
    show("\n=== Document given to the payments expert ===")
    feed_expert(ctx, "payments", "payment-rules.md", DOCUMENT)
    ctx.db.close()
