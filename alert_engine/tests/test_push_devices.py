from alert_engine.firestore_client import _remove_invalid_push_data
from alert_engine.models import AlertSettings


def _device(device_id: str, token: str):
    return {
        "id": device_id,
        "token": token,
        "label": "Android · Chrome",
        "platform": "android",
        "browser": "chrome",
        "deviceType": "mobile",
        "registeredAt": "2026-07-10T00:00:00.000Z",
        "lastSeenAt": "2026-07-18T00:00:00.000Z",
        "tokenUpdatedAt": "2026-07-10T00:00:00.000Z",
    }


def test_legacy_only_settings_continue_to_deliver():
    settings = AlertSettings.from_dict({"pushTokens": ["legacy-a", "legacy-b"]})
    assert settings.delivery_tokens() == ["legacy-a", "legacy-b"]


def test_devices_are_preferred_then_legacy_tokens_are_deduplicated():
    settings = AlertSettings.from_dict({
        "pushDevices": [_device("device-a", "shared"), _device("device-b", "device-b")],
        "pushTokens": ["legacy-a", "shared", "legacy-a"],
    })
    assert settings.delivery_tokens() == ["shared", "device-b", "legacy-a"]


def test_invalid_device_rows_are_ignored_without_breaking_legacy_tokens():
    settings = AlertSettings.from_dict({
        "pushDevices": [{"id": "missing-token"}, None, _device("valid", "device-token")],
        "pushTokens": ["legacy-token"],
    })
    assert [device.id for device in settings.pushDevices] == ["valid"]
    assert settings.delivery_tokens() == ["device-token", "legacy-token"]


def test_confirmed_invalid_token_is_removed_from_both_structures_only():
    next_tokens, next_devices, removed = _remove_invalid_push_data({
        "pushTokens": ["invalid", "legacy-valid"],
        "pushDevices": [_device("invalid-device", "invalid"), _device("valid-device", "device-valid")],
    }, {"invalid"})
    assert next_tokens == ["legacy-valid"]
    assert [device["id"] for device in next_devices] == ["valid-device"]
    assert removed == 1


def test_transient_or_unrelated_token_does_not_change_registration_data():
    data = {
        "pushTokens": ["legacy-valid"],
        "pushDevices": [_device("valid-device", "device-valid")],
    }
    next_tokens, next_devices, removed = _remove_invalid_push_data(data, {"not-registered-here"})
    assert next_tokens == data["pushTokens"]
    assert next_devices == data["pushDevices"]
    assert removed == 0
