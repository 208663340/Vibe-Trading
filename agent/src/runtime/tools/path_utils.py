"""Compatibility alias for the shared, agent-independent path policy."""
import sys
from src.platform import path_utils
sys.modules[__name__] = path_utils
