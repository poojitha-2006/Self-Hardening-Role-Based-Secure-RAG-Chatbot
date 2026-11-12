"""
app.py (Root Launcher)
======================
Convenience entry point for running the Streamlit UI from the workspace root:
  streamlit run app.py
"""

import runpy
from pathlib import Path

UI_PATH = Path(__file__).resolve().parent / "secure-rag" / "app" / "ui.py"
runpy.run_path(str(UI_PATH), run_name="__main__")

