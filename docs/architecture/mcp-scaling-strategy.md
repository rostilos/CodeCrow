# MCP Server Architecture for SaaS Scale

## Executive Summary

For a SaaS serving **dozens of teams and hundreds of developers**, the current architecture of spawning a new JVM process per request is **inefficient**. This document analyzes the options and provides recommendations.

## Current Architecture

```
Pipeline Agent ─HTTP─▶ MCP Client (Python) ─spawns─▶ Java JVM (STDIO) ─dies after─▶
                         │                              │
                         │ Per-request process spawn    │
                         │ ~500ms-2s JVM startup        │
                         │ 100-300MB memory each        │
                         └──────────────────────────────┘
```

### Problems at Scale

| Metric | 10 concurrent | 50 concurrent | 100 concurrent |
|--------|---------------|---------------|----------------|
| JVM startup time | 5-20s total | 25-100s total | 50-200s total |
| Memory usage | 1-3 GB | 5-15 GB | 10-30 GB |
| OS process overhead | Minimal | Noticeable | Significant |

## Option 1: Process Pooling (Recommended) ✅

**Keep STDIO transport but pool MCP server processes.**

```
Pipeline Agent ─HTTP─▶ MCP Client ─▶ Process Pool ─▶ Pre-warmed JVM 1 (reused)
                                     Manager           Pre-warmed JVM 2 (reused)
                                                       Pre-warmed JVM 3 (reused)
                                                       ...
```

### Benefits
- **Zero JVM startup latency** after warmup
- **Shared memory footprint** - 5 processes serve 100+ requests
- **Compatible with existing MCP protocol** - no protocol changes
- **Works with mcp_use library** - minimal Python changes
- **Gradual rollout** - can be enabled per-environment

### Implementation
See `utils/mcp_pool.py` - Process pool manager that:
- Pre-warms N JVM processes at startup
- Routes requests to available processes
- Recycles processes after N requests or time limit
- Handles process crashes gracefully

### Configuration
```bash
MCP_POOL_SIZE=5              # Number of pre-warmed processes
MCP_POOL_MAX_REQUESTS=100    # Recycle after N requests
MCP_POOL_MAX_AGE=3600        # Recycle after 1 hour
```

### Memory Math
- Without pooling (100 concurrent): 100 × 200MB = **20 GB**
- With pooling (5 processes): 5 × 200MB = **1 GB**

---

## Option 2: HTTP Transport

**Convert MCP servers to HTTP services.**

```
Pipeline Agent ─HTTP─▶ MCP Client ─HTTP─▶ VCS MCP Service (long-running)
                                          │
                                          └──▶ Platform MCP Service (long-running)
```

### Benefits
- Standard HTTP load balancing
- Native Docker health checks
- Easy horizontal scaling
- Can use connection pooling to VCS APIs

### Drawbacks
- **Breaking change** to MCP protocol contract
- Requires rewriting Python MCP client
- Not compatible with mcp_use library (expects STDIO)
- More complex deployment

### When to Choose HTTP
- If you abandon the `mcp_use` library entirely
- If you need to scale MCP servers independently
- If you want standard REST API observability

---

## Option 3: SSE Transport

**Use MCP's native SSE (Server-Sent Events) transport.**

The MCP SDK supports SSE transport which allows long-running server connections.

### Benefits
- Part of official MCP specification
- Maintains MCP protocol compatibility
- Supports streaming responses

### Drawbacks
- Requires MCP SDK changes on both sides
- More complex than process pooling
- Limited ecosystem support in Python

---

## Recommendation

### Phase 1: Process Pooling (Immediate)

1. **Enable process pooling** in Python MCP client
2. **Configure pool size** based on expected load
3. **Monitor metrics** to tune pool parameters

This provides **80% of the benefit with 20% of the effort**.

### Phase 2: Consider HTTP (Future)

If you later need:
- Independent scaling of MCP servers
- Advanced load balancing
- Cross-datacenter deployment

Then evaluate HTTP transport.

---

## Implementation Checklist

### Process Pooling
- [x] Create `McpProcessPool` class
- [ ] Integrate with `ReviewService`
- [ ] Add pool metrics endpoint
- [ ] Configure Docker healthchecks
- [ ] Load test with pool enabled

### Docker Compose Changes (if choosing HTTP later)
```yaml
services:
  vcs-mcp-server:
    build: ../java-ecosystem/mcp-servers/bitbucket-mcp
    command: ["java", "-jar", "app.jar", "--http"]
    ports:
      - "8765:8765"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/health"]
    deploy:
      replicas: 3  # Scale horizontally
```

---

## Metrics to Watch

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Pool utilization | < 80% | > 90% |
| Process recycles/hour | < 10 | > 50 |
| Request latency p99 | < 100ms | > 500ms |
| Process errors/hour | 0 | > 5 |

---

## Conclusion

**Use process pooling for now.** It's the most pragmatic solution that:
1. Requires minimal code changes
2. Provides massive performance improvement
3. Maintains compatibility with existing architecture
4. Can be deployed incrementally

The HTTP approach is valid but represents a larger architectural shift that should only be undertaken if process pooling proves insufficient at your scale.
