"""Profile-qualified Feishu client bindings for doc/drive tools.

The ``feishu_doc`` / ``feishu_drive`` tools need a ``lark_oapi`` client
outside the document-comment handler (which injects a thread-local client
via ``set_client``). In DM / group-chat turns the agent runs on a gateway
worker thread with no comment context, so the tools fall back to a
process-wide binding published by the Feishu adapter.

This module is the single bounded owner of that fallback, so the Feishu
adapter godfile does not grow another mechanism.

Two ownership guarantees make the fallback safe in a multiplex gateway
(several profile-owned Feishu adapters in one process):

1. **Profile-qualified keys** — bindings are keyed by the canonical profile
   identity (the active profile home, which the gateway propagates into the
   agent worker thread via the ``HERMES_HOME`` contextvar override). A turn
   owned by profile A therefore resolves only profile A's client and can
   never read or mutate a document with profile B's tenant credentials.
2. **Generation-owned publication/teardown** — every ``publish`` is stamped
   with a generation allocated from a per-profile, process-wide monotonic
   counter owned by this registry (NOT the publishing adapter's local
   count), so two adapter instances for the same profile can never publish
   colliding generations. ``unpublish`` is compare-and-remove: it only
   deletes a binding whose generation matches, so a stale adapter's
   disconnect cannot erase a newer adapter's live binding, and a failed
   connect attempt never leaves a discoverable client behind (publication
   happens only after the connection succeeds).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from hermes_constants import get_hermes_home

# profile_key -> (generation, client)
_bindings: Dict[str, Tuple[int, Any]] = {}
# profile_key -> last generation allocated for that profile. Monotonic and
# process-wide so replacement adapter instances cannot collide with a stale
# instance's generations (a stale teardown must never match a live binding).
_generations: Dict[str, int] = {}
_lock = threading.Lock()


def active_profile_key() -> str:
    """Canonical identity of the profile owning the current turn/thread.

    ``get_hermes_home()`` respects the ``HERMES_HOME`` contextvar override
    installed by the multiplex gateway's ``_profile_runtime_scope``, so in a
    single-profile process this is the process home, and in a multiplexer it
    is the profile home of the turn currently being executed. The gateway
    propagates that contextvar into the agent worker thread via
    ``copy_context()`` (``_run_in_executor_with_context`` /
    ``propagate_context_to_thread``), which is what makes a tool call
    resolve the owning profile's binding.
    """
    return str(get_hermes_home())


def publish(client: Any, profile_key: Optional[str] = None) -> int:
    """Register (or replace) the client binding for a profile.

    The generation is allocated from this registry's per-profile monotonic
    counter rather than taken from the caller, so a replacement adapter
    instance for the same profile always supersedes a stale instance's
    binding (a later generation replaces an earlier one), and the stale
    instance's compare-and-remove teardown can never match the new binding.

    Returns the allocated generation; the publishing adapter must retain it
    for its own ``unpublish``.
    """
    key = profile_key or active_profile_key()
    with _lock:
        generation = _generations.get(key, 0) + 1
        _generations[key] = generation
        _bindings[key] = (generation, client)
    return generation


def unpublish(generation: int, profile_key: Optional[str] = None) -> None:
    """Remove the binding for a profile only if its generation matches.

    Compare-and-remove semantics: a stale adapter tearing down with an old
    generation cannot clear the binding published by a newer adapter for the
    same profile.
    """
    key = profile_key or active_profile_key()
    with _lock:
        current = _bindings.get(key)
        if current is not None and current[0] == generation:
            del _bindings[key]


def resolve(profile_key: Optional[str] = None) -> Any:
    """Return the client bound to the active profile, or ``None``."""
    key = profile_key or active_profile_key()
    with _lock:
        current = _bindings.get(key)
        return current[1] if current is not None else None


def clear_all() -> None:
    """Drop every binding (test helper)."""
    with _lock:
        _bindings.clear()
        _generations.clear()
