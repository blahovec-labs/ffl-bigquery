"""Pytest configuration.

Local-machine TLS workaround (dev-environment only, never shipped)
--------------------------------------------------------------------
On the maintainer's Windows machine, Norton intercepts outbound HTTPS and re-signs
it with a locally-generated certificate. Python's stdlib `ssl`/OpenSSL cert-chain
validation doesn't trust that certificate, so live `requests` calls fail with
`CERTIFICATE_VERIFY_FAILED` even though the OS itself is configured to trust it.

`uv run --native-tls ...` does NOT fix this: that flag only affects uv's own
downloads of packages/pythons, not TLS validation inside the Python process that
runs the tests. The actual fix is `truststore.inject_into_ssl()`, which swaps in
an `SSLContext` that defers to the OS-native trust store (the one Norton's
certificate is actually installed into) instead of OpenSSL's bundled one.

We only want that patch active for the handful of `network`-marked tests that make
real HTTP calls -- the offline suite injects fake sessions and never touches real
SSL, so it must keep passing on a clean machine with no `truststore` installed and
no TLS interception at all. `truststore` is a dev extra, not a runtime dependency
of the shipped package, so the import below is best-effort: a contributor without
it (or without this problem) just gets a no-op here.
"""
from __future__ import annotations

import pytest

try:
    import truststore
except ImportError:  # dev extra not installed -- nothing to inject, nothing to fix
    truststore = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _inject_truststore_for_network_tests(request: pytest.FixtureRequest) -> None:
    """Patch SSL to use the OS trust store, but only for `network`-marked tests."""
    if request.node.get_closest_marker("network") is None:
        return
    if truststore is None:
        return
    truststore.inject_into_ssl()
