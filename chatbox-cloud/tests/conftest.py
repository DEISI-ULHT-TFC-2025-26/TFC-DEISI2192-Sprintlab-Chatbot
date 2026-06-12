"""Shared test setup.

server.py reads GITLAB_TOKEN / GROQ_API_KEY at import time and exits if they are
missing (fail-fast in production). Tests don't talk to the network, so we inject
throwaway values *before* server is imported anywhere, and make the chatbox-cloud
directory importable regardless of the current working directory.
"""

import os
import sys

os.environ.setdefault("GITLAB_TOKEN", "test-token")
os.environ.setdefault("GROQ_API_KEY", "test-key")

# tests/ lives inside chatbox-cloud/ — put the parent on the path so `import server`
# works whether pytest is run from the repo root or from tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
