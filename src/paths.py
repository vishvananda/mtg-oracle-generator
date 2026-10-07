"""Artifact locations shared by the standalone training tools."""
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get('CARD_INTENT_ARTIFACTS', str(PROJECT/'artifacts'))).resolve()
