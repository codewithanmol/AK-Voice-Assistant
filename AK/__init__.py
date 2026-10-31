"""AK — a free, advanced, local-first voice assistant.
Created by Anmol Tyagi.
Optional external task-engine integration for enhanced capabilities.
"""

__version__ = "2.0.0"
__author__ = "Anmol Tyagi"

from .config import load_config, abspath, get_data_dir
from .voice import Speaker, Listener
from .brain import Brain
from .skills import Skills
from .engine import TaskEngine
from .local_agent import LocalAgent
from .automations import Automations
