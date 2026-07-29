"""Compatibility shim for the Aftermath adapter.

The v1 adapter that used to live here (1,727 lines) targeted the retired v1 API
host and predates skills v3.0.0. It has been replaced by the :mod:`cli.af`
package, which implements the same interface against Aftermath V2.

Existing imports keep working:

.. code-block:: python

    from cli.af_proxy import AftermathProxy, AftermathMockProxy, _normalise_instrument

New code should import from :mod:`cli.af.proxy` and :mod:`cli.af.mock` directly.
"""
from __future__ import annotations

from cli.af.markets import (
    base_asset as _base_asset,
    normalise_instrument as _normalise_instrument,
)
from cli.af.mock import AftermathMockProxy
from cli.af.proxy import AccountRef, AfFill, AftermathProxy

__all__ = [
    "AftermathProxy",
    "AftermathMockProxy",
    "AfFill",
    "AccountRef",
    "_normalise_instrument",
    "_base_asset",
]
