"""Aftermath Finance V2 perpetuals integration.

This package is the ONLY place in the repository that talks to the Aftermath
API. Strategies, the engine and the order manager reach it exclusively through
``cli.af.proxy.AftermathProxy`` (or its interface-identical mock twin), which
exposes the same call shape the venue-agnostic engine already used.

That single-seam design is deliberate: it is what makes every strategy in the
tree work against Aftermath without being individually rewritten, and it is what
makes the safety behaviours in ``safety.py`` and the transaction gate in
``tx.py`` impossible for a strategy to bypass.

Module map
----------
``config``   one host constant + all environment configuration
``ids``      branded identifier types and the native BigInt ``"123n"`` wire format
``api``      HTTP transport: POST-first, retry/backoff, preview tagged unions
``gas``      user-selectable gas modes (sponsored | self | dynamic)
``markets``  market discovery, instrument normalisation, fixed-point scaling
``safety``   margin health, position sizing, circuit breakers, kill switch
``tx``       build -> preview-gate -> INSPECT -> (sign) -> reconcile
``proxy``    the adapter itself
``mock``     interface-identical offline twin
"""

from cli.af.config import AF_API_BASE_URL, AfConfig, load_config

__all__ = ["AF_API_BASE_URL", "AfConfig", "load_config"]
