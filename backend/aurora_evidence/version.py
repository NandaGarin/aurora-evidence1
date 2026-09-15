"""Version constants recorded in every RunInfo.versions map."""

from __future__ import annotations

from typing import Final

#: Contract version this build implements and accepts.
SCHEMA_VERSION: Final[str] = "1.0.0"

#: Version of the Dev-2 retrieval pipeline itself. Bump when ranking features,
#: dedup rules or normalization change, because cached results and any
#: evaluation snapshot are only comparable within one pipeline version.
PIPELINE_VERSION: Final[str] = "1.0.0"

SERVICE_NAME: Final[str] = "aurora-evidence"
