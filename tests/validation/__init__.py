"""Tests of the validation layer and of the Critic agent.

They run with the rest of the suite:

    python -m unittest discover tests

Standard library only, and the simulated provider wherever an agent is involved:
no test depends on the network, on an API key or on an LLM. That is a design
requirement, not a convenience: the deterministic layer has to be reproducible
on any machine of the group.

    test_text, test_checks_item, test_checks_document   the checks, one by one
    test_engine, test_envelope, test_adapters,
    test_report_and_cli                                 the core around them
    test_host                                           the adapters to GigaBrain's data
    test_critic                                         the Critic in the conversation and in the meeting
"""
