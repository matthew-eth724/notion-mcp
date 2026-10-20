# Notion MCP Server

Tools for reading, writing, and creating Notion pages via MCP.

## Setup

1. **Create Notion Integration:**
   - Go to https://www.notion.so/my-integrations
   - Click "New integration" → name it → copy "Internal Integration Token"
   - Share pages/databases with your integration (⋮ → Add connections)

2. **Configure:**
   ```bash
   cp .env.example .env
   # Edit .env with your token
   ```

3. **Install:**
   ```bash
   pip install -e .
   # or: uv pip install -e .
   ```

4. **Test:**
   ```bash
   python -m notion_mcp
   # Should start and wait for MCP connection
   ```

## Register with KiloCode/OpenCode

Add to `~/.config/opencode/opencode.json`:
```json
{
  "mcp": {
    "notion": {
      "type": "local",
      "command": ["python", "-m", "notion_mcp"],
      "cwd": "/data/home/notion-mcp",
      "enabled": true
    }
  }
}
```

Then restart KiloCode. Tools available:
- `notion_search` — search pages
- `notion_read_page` — read full page content
- `notion_create_page` — create new page (in database or as child)
- `notion_update_page` — append content or update properties
- `notion_get_database` — view database schema & entries

## Example Usage

```
> Search for "project roadmap"
> Read page abc-123
> Create page in database xyz-789 with title "New Task" and content "# Hello\n\nContent here"
> Update page abc-123 with content "## Added section\n\nMore details"
```
