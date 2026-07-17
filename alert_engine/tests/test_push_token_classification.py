from alert_engine.channels.push import _is_confirmed_unregistered


class UnregisteredError(Exception):
    code = "UNREGISTERED"


class InvalidArgumentError(Exception):
    code = "INVALID_ARGUMENT"


class SenderIdMismatchError(Exception):
    code = "SENDER_ID_MISMATCH"


class ThirdPartyAuthError(Exception):
    code = "THIRD_PARTY_AUTH_ERROR"


def test_only_fcm_confirmed_unregistered_token_is_eligible_for_cleanup():
    assert _is_confirmed_unregistered(UnregisteredError())
    assert not _is_confirmed_unregistered(InvalidArgumentError())
    assert not _is_confirmed_unregistered(SenderIdMismatchError())
    assert not _is_confirmed_unregistered(ThirdPartyAuthError())
    assert not _is_confirmed_unregistered(None)
