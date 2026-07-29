"""SDK patches for the hyperliquid-python-sdk.

Isolates monkey-patching logic so it can be version-checked and tracked.
These patches work around known SDK bugs and should be removed when the
upstream SDK fixes are released.
"""
from __future__ import annotations

import logging

log = logging.getLogger("sdk_patches")

_spot_meta_patched = False


def patch_spot_meta_indexing():
    """Patch hyperliquid SDK Info.__init__ to handle out-of-bounds token indices on testnet.

    Bug: When testnet spot metadata references token indices beyond the tokens
    list length, the SDK raises IndexError during Info.__init__. This patch
    pads the tokens list with placeholder entries so all indices are in-bounds.

    Safe to call multiple times — applies the patch only once.
    """
    global _spot_meta_patched
    if _spot_meta_patched:
        return
    _spot_meta_patched = True

    try:
        import hyperliquid.info as info_mod
    except ImportError:
        log.debug("hyperliquid SDK not installed, skipping patch")
        return

    _orig_init = info_mod.Info.__init__

    def _patched_init(self, *args, **kwargs):
        try:
            _orig_init(self, *args, **kwargs)
        except IndexError:
            log.warning("SDK spot_meta token index out of bounds — applying safe fallback")
            spot_meta = kwargs.get("spot_meta")
            if spot_meta is None:
                from hyperliquid.api import API
                api = API(args[0] if args else kwargs.get("base_url"), kwargs.get("timeout"))
                spot_meta = api.post("/info", {"type": "spotMeta"})

            tokens = spot_meta["tokens"]
            max_idx = max(
                (idx for si in spot_meta["universe"] for idx in si["tokens"]),
                default=0,
            )
            while len(tokens) <= max_idx:
                tokens.append({"name": f"UNKNOWN-{len(tokens)}", "szDecimals": 0,
                               "weiDecimals": 0, "index": len(tokens),
                               "tokenId": "0x0", "isCanonical": False})

            # spot_meta is the 4th positional param (index 3) in Info.__init__.
            # It may already be in *args from the caller (e.g. Exchange.__init__),
            # so we must replace it positionally to avoid "multiple values" TypeError.
            args_list = list(args)
            SPOT_META_POS = 3  # (base_url, skip_ws, meta, spot_meta, ...)
            if len(args_list) > SPOT_META_POS:
                args_list[SPOT_META_POS] = spot_meta
                _orig_init(self, *args_list, **kwargs)
            else:
                kwargs["spot_meta"] = spot_meta
                _orig_init(self, *args, **kwargs)

    info_mod.Info.__init__ = _patched_init
    log.debug("Applied spot_meta indexing patch to hyperliquid SDK")
