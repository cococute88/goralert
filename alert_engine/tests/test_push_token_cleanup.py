from alert_engine.engine import AlertEngine
from alert_engine.models import AlertSettings, PushDevice

from .conftest import FakeChannel, FakeDataSource, FakeFirestore, make_ratio_rule


def test_fcm_confirmed_invalid_token_is_removed_without_touching_other_devices():
    firestore = FakeFirestore(settings=AlertSettings(
        globalEnabled=True,
        pushTokens=["invalid", "valid"],
        pushDevices=[PushDevice(id="invalid-device", token="invalid"), PushDevice(id="valid-device", token="valid")],
    ))
    engine = AlertEngine(
        firestore=firestore,
        datasource=FakeDataSource(),
        channel_registry={"push": FakeChannel("push", status="failed", invalid_tokens=["invalid"]), "telegram": FakeChannel("telegram")},
    )
    rule = make_ratio_rule(rule_id="push-cleanup")

    engine.send_test_alert("u1", rule, ["push"], settings=firestore._settings)

    assert firestore.removed_push_tokens == ["invalid"]
    assert firestore._settings.pushTokens == ["valid"]
    assert [device.id for device in firestore._settings.pushDevices] == ["valid-device"]
