"""
Runner for google-workspace-mcp server.
Fixes the upstream bug in google_workspace_mcp.__main__ where asyncio.run()
is called on a synchronous FastMCP.run() method.
"""
import os
import sys

# Default capabilities if not explicitly configured in environment
if "GOOGLE_WORKSPACE_ENABLED_CAPABILITIES" not in os.environ:
    os.environ["GOOGLE_WORKSPACE_ENABLED_CAPABILITIES"] = '["drive", "sheets", "docs", "gmail", "calendar"]'

try:
    from google_workspace_mcp.app import mcp
    import google_workspace_mcp.__main__  # noqa: F401
    mcp.run()
except KeyboardInterrupt:
    sys.exit(0)
