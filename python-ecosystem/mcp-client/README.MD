# CodeCrow MCP Client

A refactored Python application for handling code review requests using MCP (Model Context Protocol) servers.

## Project Structure

```
├── main.py              # Main entry point
├── models.py            # Pydantic data models
├── llm_factory.py       # LLM creation factory
├── mcp_config.py        # MCP configuration builder
├── prompt_builder.py    # Prompt generation utilities
├── response_parser.py   # Response parsing utilities
├── review_service.py    # Core review processing service
├── stdin_handler.py     # Stdin request processing
├── web_server.py        # FastAPI web server
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

## Features

- **Modular Design**: Code split into logical, reusable components
- **Dual Mode Operation**: Can run as HTTP server or process single requests from stdin
- **Structured Response Parsing**: Robust JSON extraction from AI responses
- **Error Handling**: Comprehensive error handling with structured error responses
- **Configurable**: Environment variable configuration support

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables:
```bash
# Optional: Custom MCP server JAR path
export MCP_SERVER_JAR="/path/to/your/mcp-server.jar"

# Optional: Server configuration
export AI_CLIENT_HOST="0.0.0.0"
export AI_CLIENT_PORT="8000"

# Optional: OpenRouter API key (if using OpenRouter)
export OPENROUTER_API_KEY="your-api-key"
```

## Usage

### HTTP Server Mode (Default)

Start the FastAPI server:

```bash
python main.py
```

The server will be available at `http://localhost:8000` with endpoints:
- `POST /review` - Submit code review requests
- `GET /health` - Health check

### Stdin Mode

Process a single request from stdin:

```bash
echo '{"projectId": 123, "projectWorkspace": "myworkspace", ...}' | python main.py --stdin
```

## API Reference

### POST /review

Submit a code review request.

**Request Body:**
```json
{
  "projectId": 123,
  "projectWorkspace": "workspace",
  "projectRepoSlug": "repo-name",
  "aiProvider": "openrouter",
  "aiModel": "anthropic/claude-3-sonnet",
  "aiApiKey": "your-api-key",
  "pullRequestId": 456,
  "mcpServerJar": "/optional/custom/path.jar"
}
```

**Response:**
```json
{
  "result": {
    "comment": "Review summary",
    "issues": {
      "0": {
        "severity": "HIGH|MEDIUM|LOW",
        "file": "path/to/file.py",
        "line": "42",
        "reason": "Description of the issue",
        "suggestedFix": "Optional fix suggestion"
      }
    }
  },
  "error": null,
  "exception": null
}
```

## Architecture

### Core Components

1. **ReviewService**: Main service orchestrating the review process
2. **LLMFactory**: Creates appropriate LLM instances based on provider
3. **MCPConfigBuilder**: Builds MCP server configurations
4. **PromptBuilder**: Generates review prompts
5. **ResponseParser**: Extracts and validates JSON responses
6. **WebServer**: FastAPI HTTP server
7. **StdinHandler**: Command-line request processor

### Design Benefits

- **Single Responsibility**: Each class has a clear, focused purpose
- **Testability**: Components can be tested in isolation
- **Maintainability**: Easy to modify individual components
- **Reusability**: Components can be reused in different contexts
- **Error Isolation**: Failures in one component don't cascade

## Configuration

The application uses environment variables for configuration:

- `MCP_SERVER_JAR`: Path to MCP server JAR file
- `AI_CLIENT_HOST`: Server host address (default: "0.0.0.0")
- `AI_CLIENT_PORT`: Server port (default: 8000)
- `OPENROUTER_API_KEY`: API key for OpenRouter (optional)

## Error Handling

All errors are returned in a structured format with:
- Descriptive error messages
- Proper HTTP status codes
- Structured JSON responses matching the expected format
- Exception details for debugging

## Development

To extend the application:

1. **Add new LLM providers**: Extend `LLMFactory`
2. **Modify prompts**: Update `PromptBuilder`
3. **Add new endpoints**: Extend `WebServer`
4. **Custom response parsing**: Modify `ResponseParser`
5. **New configuration options**: Update `MCPConfigBuilder`