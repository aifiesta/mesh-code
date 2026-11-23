from meshharness.core import verify


def test_refusals_and_unbacked_claims_are_named_and_everything_else_passes():
    assert verify.verify_reply("I don't have the tools to do that.", tool_calls_this_turn=0,
                               user_asked_action=True)
    assert verify.verify_reply("I've created the file for you.", tool_calls_this_turn=0,
                               user_asked_action=True) == "the model claimed work it never performed"
    assert verify.verify_reply("I've created the file for you.", tool_calls_this_turn=2,
                               user_asked_action=True) is None
    assert verify.verify_reply("The function returns a list.", tool_calls_this_turn=0,
                               user_asked_action=False) is None
    assert verify.asks_for_action("please add a health endpoint") and not verify.asks_for_action("what is 2+2?")
