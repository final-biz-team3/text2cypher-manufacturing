"""Test configuration and shared fixtures."""

import sys
from pathlib import Path

# Add backend to sys.path so imports like 'from core.openai_client import ...' work
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))
