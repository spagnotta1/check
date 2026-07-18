"""finance_sync — automated financial institution synchronization layer.

Architecture
------------
- ``canonical``   : provider-agnostic data models every downstream consumer uses
- ``adapters``    : one adapter class per institution (Adapter Pattern); the only
                    place provider-specific logic may live
- ``sandbox``     : deterministic simulated provider backends used when no live
                    API credentials are configured
- ``repository``  : persists canonical data into SQLite (dedupe, upserts)
- ``engine``      : institution-agnostic SyncEngine orchestrating all adapters
- ``scheduler``   : background auto-sync (default every 12 hours)
- ``service``     : connection lifecycle (connect / disconnect / token storage)
- ``routes``      : Flask blueprint exposing pages + JSON API
"""

__all__ = ["canonical", "adapters", "engine", "repository", "scheduler", "service"]
