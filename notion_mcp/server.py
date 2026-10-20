#!/usr/bin/env python3
"""
Notion MCP Server — Tools for reading, writing, and creating Notion pages.
"""
import os
import json
import asyncio
from typing import Any, Optional, List
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool, 
    ListToolsRequest, 
    CallToolRequest, 
    CallToolRequestParams,
    CallToolResult, 
    ListToolsResult,
    TextContent,
    InitializeRequest,
    InitializeResult,
    ServerCapabilities,
    ToolsCapability
)
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError

# Load .env from project root (parent of server directory)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ─── Config ──────────────────────────────────────────────────────────────
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
if not NOTION_TOKEN:
    raise RuntimeError("NOTION_TOKEN not set. Create .env with NOTION_TOKEN=secret_xxx")

notion = NotionClient(auth=NOTION_TOKEN)

# ─── MCP Server ──────────────────────────────────────────────────────────
server = Server("notion-tools")

# ─── Tool Definitions ────────────────────────────────────────────────────
TOOLS = [
    Tool(
        name="notion_search",
        description="Search Notion pages by title or content",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="notion_read_page",
        description="Read a Notion page's full content (blocks + children)",
        inputSchema={
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Notion page ID (URL fragment)"},
            },
            "required": ["page_id"],
        },
    ),
    Tool(
        name="notion_create_page",
        description="Create a new Notion page in a database or as child of a page",
        inputSchema={
            "type": "object",
            "properties": {
                "parent_type": {"type": "string", "enum": ["database_id", "page_id"], "description": "Parent type"},
                "parent_id": {"type": "string", "description": "Database ID or parent page ID"},
                "title": {"type": "string", "description": "Page title"},
                "content": {"type": "string", "description": "Initial page content (markdown-like)"},
                "properties": {"type": "object", "description": "Database properties (if parent is database)"},
            },
            "required": ["parent_type", "parent_id", "title"],
        },
    ),
    Tool(
        name="notion_update_page",
        description="Update page content (append blocks) or properties",
        inputSchema={
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Page ID to update"},
                "content": {"type": "string", "description": "Content to append (markdown-like)"},
                "properties": {"type": "object", "description": "Properties to update"},
            },
            "required": ["page_id"],
        },
    ),
    Tool(
        name="notion_get_database",
        description="Get database schema and recent entries",
        inputSchema={
            "type": "object",
            "properties": {
                "database_id": {"type": "string", "description": "Database ID"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["database_id"],
        },
    ),
]

# ─── Helpers ─────────────────────────────────────────────────────────────
def format_block(block: dict) -> str:
    """Convert a Notion block to readable text."""
    btype = block.get("type", "unknown")
    data = block.get(btype, {})
    
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do", "quote", "callout"):
        rich_text = data.get("rich_text", [])
        text = "".join([t.get("plain_text", "") for t in rich_text])
        prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "- ", "numbered_list_item": "1. ", "to_do": "[ ] ", "quote": "> ", "callout": "💡 "}.get(btype, "")
        return f"{prefix}{text}"
    elif btype == "code":
        rich_text = data.get("rich_text", [])
        code = "".join([t.get("plain_text", "") for t in rich_text])
        lang = data.get("language", "")
        return f"```{lang}\n{code}\n```"
    elif btype == "divider":
        return "---"
    elif btype == "image":
        url = data.get("external", {}).get("url") or data.get("file", {}).get("url", "")
        return f"![image]({url})"
    return f"[{btype}]"

async def get_page_blocks(page_id: str) -> list[str]:
    """Fetch all blocks from a page recursively."""
    blocks = []
    cursor = None
    while True:
        resp = notion.blocks.children.list(block_id=page_id, start_cursor=cursor)
        for block in resp["results"]:
            blocks.append(format_block(block))
            if block.get("has_children"):
                blocks.extend(await get_page_blocks(block["id"]))
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
    return blocks

def _parse_markdown_to_blocks(markdown: str) -> list[dict]:
    """Simple markdown → Notion blocks. Supports headings, paragraphs, code, lists."""
    blocks = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        
        # Headings
        if line.startswith("# "):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}})
        elif line.startswith("## "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}})
        elif line.startswith("### "):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}})
        # Code blocks
        elif line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append({
                "type": "code",
                "code": {"rich_text": [{"type": "text", "text": {"content": "\n".join(code_lines)}}], "language": lang or "plain"}
            })
        # Lists
        elif line.startswith("- ") or line.startswith("* "):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}})
        elif line[:2].isdigit() and line[1:3] == ". ":
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}})
        # Divider
        elif line.strip() == "---":
            blocks.append({"type": "divider", "divider": {}})
        # Paragraph
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]}})
        i += 1
    return blocks

# ─── Tool Implementations ────────────────────────────────────────────────
async def _search(args: dict) -> list[TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)
    resp = notion.search(query=query, page_size=limit)
    results = []
    for page in resp["results"]:
        if page["object"] == "page":
            title = "Untitled"
            for prop in page.get("properties", {}).values():
                if prop["type"] == "title":
                    title = "".join([t["plain_text"] for t in prop["title"]])
                    break
            results.append(f"- **{title}** (`{page['id']}`)")
    return [TextContent(type="text", text="\n".join(results) or "No pages found")]

async def _read_page(args: dict) -> list[TextContent]:
    page_id = args["page_id"]
    page = notion.pages.retrieve(page_id=page_id)
    
    # Get title
    title = "Untitled"
    for prop in page.get("properties", {}).values():
        if prop["type"] == "title":
            title = "".join([t["plain_text"] for t in prop["title"]])
            break
    
    # Get blocks
    blocks = await get_page_blocks(page_id)
    content = "\n\n".join(blocks)
    
    return [TextContent(type="text", text=f"# {title}\n\n{content}")]

async def _create_page(args: dict) -> list[TextContent]:
    parent_type = args["parent_type"]
    parent_id = args["parent_id"]
    title = args["title"]
    content = args.get("content", "")
    properties = args.get("properties", {})
    
    parent = {parent_type: parent_id}
    
    # Build properties
    page_properties = {"title": {"title": [{"type": "text", "text": {"content": title}}]}}
    page_properties.update(properties)
    
    # Create page
    page = notion.pages.create(parent=parent, properties=page_properties)
    page_id = page["id"]
    
    # Add content blocks if provided
    if content:
        blocks = _parse_markdown_to_blocks(content)
        if blocks:
            notion.blocks.children.append(block_id=page_id, children=blocks)
    
    return [TextContent(type="text", text=f"Created page: {page['url']} (ID: {page_id})")]

async def _update_page(args: dict) -> list[TextContent]:
    page_id = args["page_id"]
    content = args.get("content")
    properties = args.get("properties", {})
    
    results = []
    
    # Update properties
    if properties:
        notion.pages.update(page_id=page_id, properties=properties)
        results.append("Properties updated")
    
    # Append content blocks
    if content:
        blocks = _parse_markdown_to_blocks(content)
        if blocks:
            notion.blocks.children.append(block_id=page_id, children=blocks)
            results.append(f"Appended {len(blocks)} blocks")
    
    return [TextContent(type="text", text="\n".join(results) or "No changes made")]

async def _get_database(args: dict) -> list[TextContent]:
    database_id = args["database_id"]
    limit = args.get("limit", 10)
    
    db = notion.databases.retrieve(database_id=database_id)
    
    # Schema
    schema_lines = ["## Database Schema"]
    for name, prop in db.get("properties", {}).items():
        schema_lines.append(f"- **{name}** ({prop['type']})")
    
    # Recent entries
    resp = notion.databases.query(database_id=database_id, page_size=limit)
    schema_lines.append("\n## Recent Entries")
    for page in resp["results"]:
        title = "Untitled"
        for prop in page.get("properties", {}).values():
            if prop["type"] == "title":
                title = "".join([t["plain_text"] for t in prop["title"]])
                break
        schema_lines.append(f"- **{title}** (`{page['id']}`)")
    
    return [TextContent(type="text", text="\n".join(schema_lines))]

TOOL_HANDLERS = {
    "notion_search": _search,
    "notion_read_page": _read_page,
    "notion_create_page": _create_page,
    "notion_update_page": _update_page,
    "notion_get_database": _get_database,
}

# ─── Request Handlers ────────────────────────────────────────────────────

async def handle_list_tools(ctx, request: ListToolsRequest) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)

async def handle_call_tool(ctx, request: CallToolRequestParams) -> CallToolResult:
    tool_name = request.name
    args = request.arguments or {}
    
    # Debug logging
    print(f"DEBUG: Called tool {tool_name} with args: {args}", flush=True)
    import sys
    sys.stdout.flush()
    
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {tool_name}")],
            is_error=True
        )
    
    try:
        result = await handler(args)
        return CallToolResult(content=result, is_error=False)
    except APIResponseError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Notion API error: {e.code} - {e.message}")],
            is_error=True
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")],
            is_error=True
        )

server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)

# ─── Main ────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, 
            write_stream, 
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())