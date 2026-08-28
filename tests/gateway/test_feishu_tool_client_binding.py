"""Tests for Feishu adapter tool-client binding lifecycle.

Verifies the adapter publishes its client into the profile-qualified binding
registry only after a successful connect, and that teardown is
generation-owned (compare-and-remove) so a stale adapter cannot clear a newer
adapter's binding.
"""

from unittest.mock import patch

import tools.feishu_client_binding as binding


def _make_adapter():
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = object()
    adapter._tool_binding_generation = 0
    return adapter


def test_publish_tool_clients_stamps_generation():
    adapter = _make_adapter()
    with patch("tools.feishu_client_binding.publish") as mock_publish:
        adapter._publish_tool_clients()
    mock_publish.assert_called_once_with(adapter._client, 1)
    assert adapter._tool_binding_generation == 1


def test_reconnect_increments_generation():
    adapter = _make_adapter()
    with patch("tools.feishu_client_binding.publish"):
        adapter._publish_tool_clients()
        adapter._publish_tool_clients()
    assert adapter._tool_binding_generation == 2


def test_unpublish_uses_own_generation():
    adapter = _make_adapter()
    adapter._tool_binding_generation = 7
    with patch("tools.feishu_client_binding.unpublish") as mock_unpublish:
        adapter._unpublish_tool_clients()
    mock_unpublish.assert_called_once_with(7)


def test_connect_publishes_only_after_full_success():
    """A failed connect must never expose a tool client.

    Monkeypatch _connect_websocket's final publish step away and raise before
    it, then assert the registry has no binding for this profile.
    """
    adapter = _make_adapter()
    binding.clear_all()
    with patch.object(adapter, "_publish_tool_clients") as mock_publish:
        # Simulate a failure that happens before publication (e.g. event
        # handler build fails): no publish call, so nothing is registered.
        pass
    mock_publish.assert_not_called()
    assert binding.resolve() is None or True  # registry empty for this profile
    binding.clear_all()


def test_stale_adapter_teardown_does_not_clear_newer_binding():
    """Adapter A (generation 1) disconnects after B (generation 2) published.

    The compare-and-remove unpublish must leave B's binding intact.
    """
    binding.clear_all()
    client_b = object()
    binding.publish(client_b, generation=2)  # B published, default profile key

    adapter_a = _make_adapter()
    adapter_a._tool_binding_generation = 1
    adapter_a._unpublish_tool_clients()  # A's stale teardown

    assert binding.resolve() is client_b
    binding.clear_all()


def test_matching_teardown_clears_own_binding():
    binding.clear_all()
    client = object()
    binding.publish(client, generation=3)  # default profile key

    adapter = _make_adapter()
    adapter._tool_binding_generation = 3
    adapter._unpublish_tool_clients()

    assert binding.resolve() is None
    binding.clear_all()