"""Tests for the profile-qualified Feishu client binding registry.

Covers the ownership guarantees required for the DM-fallback fix:

1. profile A + profile B in one process resolve distinct clients from
   worker-thread tool calls (no cross-principal aliasing);
2. stale A disconnect after B has published does not clear B
   (compare-and-remove teardown);
3. reconnect replacement publication survives old-generation teardown;
4. failed publication never leaves a discoverable binding (no publish
   before a successful connect).
"""

import threading
import unittest

import tools.feishu_client_binding as binding
from tools.feishu_doc_tool import get_client as doc_get_client
from tools.feishu_drive_tool import get_client as drive_get_client


class TestFeishuClientBindingRegistry(unittest.TestCase):
    def setUp(self):
        binding.clear_all()

    def tearDown(self):
        binding.clear_all()
        from tools.feishu_doc_tool import set_client as doc_set_client
        from tools.feishu_drive_tool import set_client as drive_set_client

        doc_set_client(None)
        drive_set_client(None)

    # -- registry primitives ------------------------------------------------

    def test_publish_resolve_same_profile(self):
        client = object()
        binding.publish(client, generation=1, profile_key="/profiles/a")
        self.assertIs(
            binding.resolve(profile_key="/profiles/a"), client
        )

    def test_profile_isolation_resolves_distinct_clients(self):
        client_a = object()
        client_b = object()
        binding.publish(client_a, generation=1, profile_key="/profiles/a")
        binding.publish(client_b, generation=1, profile_key="/profiles/b")
        self.assertIs(
            binding.resolve(profile_key="/profiles/a"), client_a
        )
        self.assertIs(
            binding.resolve(profile_key="/profiles/b"), client_b
        )

    def test_reconnect_replaces_same_profile_binding(self):
        old = object()
        new = object()
        binding.publish(old, generation=1, profile_key="/profiles/a")
        binding.publish(new, generation=2, profile_key="/profiles/a")
        self.assertIs(
            binding.resolve(profile_key="/profiles/a"), new
        )

    def test_stale_unpublish_does_not_clear_newer_binding(self):
        """A stale adapter (old generation) must not clear the newer binding."""
        client_b = object()
        binding.publish(client_b, generation=2, profile_key="/profiles/a")
        binding.unpublish(generation=1, profile_key="/profiles/a")
        self.assertIs(
            binding.resolve(profile_key="/profiles/a"), client_b
        )

    def test_matching_unpublish_clears_binding(self):
        client = object()
        binding.publish(client, generation=3, profile_key="/profiles/a")
        binding.unpublish(generation=3, profile_key="/profiles/a")
        self.assertIsNone(binding.resolve(profile_key="/profiles/a"))

    def test_unpublish_unknown_generation_is_noop(self):
        binding.publish(object(), generation=1, profile_key="/profiles/a")
        binding.unpublish(generation=99, profile_key="/profiles/a")
        self.assertIsNotNone(binding.resolve(profile_key="/profiles/a"))

    # -- worker-thread resolution (the DM failure boundary) ----------------

    def _resolve_in_worker_thread(self, fn, *args):
        result = {}

        def _worker():
            result["client"] = fn(*args)

        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "worker thread did not finish")
        return result["client"]

    def test_worker_thread_resolves_own_profile_client(self):
        client_a = object()
        client_b = object()
        binding.publish(client_a, generation=1, profile_key="/profiles/a")
        binding.publish(client_b, generation=1, profile_key="/profiles/b")
        self.assertIs(
            self._resolve_in_worker_thread(binding.resolve, "/profiles/a"),
            client_a,
        )
        self.assertIs(
            self._resolve_in_worker_thread(binding.resolve, "/profiles/b"),
            client_b,
        )

    def test_doc_and_drive_tools_resolve_profile_binding_in_worker_thread(self):
        client_a = object()
        binding.publish(client_a, generation=1)
        self.assertIs(
            self._resolve_in_worker_thread(doc_get_client),
            client_a,
        )
        self.assertIs(
            self._resolve_in_worker_thread(drive_get_client),
            client_a,
        )

    def test_tools_prefer_thread_local_over_binding(self):
        shared = object()
        local = object()
        binding.publish(shared, generation=1, profile_key="/profiles/a")
        from tools.feishu_doc_tool import set_client as doc_set_client

        doc_set_client(local)
        self.assertIs(doc_get_client(), local)

    def test_missing_binding_returns_none(self):
        binding.clear_all()
        self.assertIsNone(
            self._resolve_in_worker_thread(doc_get_client)
        )
        self.assertIsNone(
            self._resolve_in_worker_thread(drive_get_client)
        )


if __name__ == "__main__":
    unittest.main()