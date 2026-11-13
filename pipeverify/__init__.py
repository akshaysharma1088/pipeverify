"""pipeverify: check that a pipeline change left the data right, not just the job green."""

__version__ = "0.3.0"

from .checks import Finding, Thresholds  # noqa: E402
from .core import RunResult, TableResult, run_config, verify  # noqa: E402

__all__ = ["verify", "run_config", "Thresholds", "Finding", "TableResult", "RunResult", "__version__"]
