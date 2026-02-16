# Sample Repository for RAG Testing

This is a minimal multi-language codebase designed to test RAG retrieval patterns.

## Structure

```
sample_repo/
├── src/
│   ├── models/
│   │   ├── user.py           # User model with validation
│   │   └── order.py          # Order model with user reference
│   ├── services/
│   │   ├── user_service.py   # User CRUD operations
│   │   ├── auth_service.py   # Authentication (uses user_service)
│   │   └── order_service.py  # Order processing (uses both)
│   ├── api/
│   │   ├── user_controller.ts    # TypeScript API endpoints
│   │   └── auth_controller.java  # Java API endpoints
│   └── utils/
│       ├── validators.py     # Validation helpers
│       └── helpers.java      # Java utility functions
└── config/
    └── settings.py           # Application configuration
```

## Dependency Graph

```
auth_controller.java ──► auth_service.py ──► user_service.py ──► user.py
                                          └──► validators.py
                                          
order_service.py ──► user_service.py
                └──► order.py ──► user.py

user_controller.ts ──► user_service.py (via API)
```

## Test Scenarios

1. **Change user.py**: Should find user_service, auth_service, order_service, order.py
2. **Change auth_service.py**: Should find auth_controller, user_service
3. **Change validators.py**: Should find services that use validation
4. **Query "authentication"**: Should prioritize auth_service, auth_controller
5. **Query "order processing"**: Should find order_service, order.py
