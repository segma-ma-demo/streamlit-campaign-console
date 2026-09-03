from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit_app


streamlit_app.render_channel_page("APP")
