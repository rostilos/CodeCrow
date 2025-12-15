# CodeCrow Integration Test Scopes

This document provides a comprehensive overview of all integration tests organized by functional scope.

## Summary

| Test Class | Tests | Scope | Tags |
|------------|-------|-------|------|
| `UserAuthFlowIT` | 14 | Authentication & Authorization | `auth`, `fast` |
| `ProjectCrudIT` | 12 | Project Management | `project`, `fast` |
| `AIConnectionCrudIT` | 11 | AI Provider Connections | `ai`, `fast` |
| `WorkspaceCrudIT` | 10 | Workspace Management | `workspace` |
| **Total** | **47** | | |

---

## 1. Authentication & Authorization Scope

**File:** `auth/UserAuthFlowIT.java`  
**Tags:** `@Tag("auth")`, `@Tag("fast")`  
**Purpose:** Tests user authentication flows, authorization rules, and workspace role-based access control.

### Test Cases

| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 1 | `shouldRegisterNewUser` | Register a new user with valid credentials | Returns 200/201, user created |
| 2 | `shouldRejectDuplicateEmail` | Attempt registration with existing email | Returns 400/500, rejected |
| 3 | `shouldLoginWithValidCredentials` | Login with correct username/password | Returns 200, JWT token returned |
| 4 | `shouldRejectInvalidPassword` | Login with wrong password | Returns 400/401, rejected |
| 5 | `shouldRejectNonExistentUser` | Login with non-existent username | Returns 400/401, rejected |
| 6 | `shouldAccessProtectedEndpoint` | Access protected API with valid token | Returns 200, access granted |
| 7 | `shouldRejectWithoutToken` | Access protected API without auth header | Returns 401, rejected |
| 8 | `shouldRejectInvalidToken` | Access protected API with invalid JWT | Returns 401, rejected |
| 9 | `shouldValidatePasswordRequirements` | Register with weak password | Returns 400/500, validation failed |
| 10 | `shouldEnforceWorkspaceRoleAccess` | Access workspace without membership | Returns 403/500, access denied |
| 11 | `shouldAllowWorkspaceMemberAccess` | Access workspace as member | Returns 200, access granted |
| 12 | `shouldPreventRoleEscalation` | Member attempts admin-only operation | Returns 403/500, prevented |
| 13 | `shouldSupportMultipleWorkspaceMemberships` | User with multiple workspace memberships | Returns 200, can access all |
| 14 | `shouldHandleWorkspaceRoleDowngrade` | Access after role downgrade from ADMIN to MEMBER | Returns 403/500, admin ops denied |

### Coverage Areas
- User registration with validation
- JWT-based authentication
- Token validation and rejection
- Workspace-level role enforcement (OWNER, ADMIN, MEMBER)
- Role escalation prevention
- Multi-workspace support

---

## 2. Project Management Scope

**File:** `project/ProjectCrudIT.java`  
**Tags:** `@Tag("project")`, `@Tag("fast")`  
**Purpose:** Tests project CRUD operations, VCS/AI binding, and project configuration.

### Test Cases

| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 1 | `shouldCreateProject` | Create project with valid data | Returns 201, project created |
| 2 | `shouldListProjects` | List all projects in workspace | Returns 200, array of projects |
| 3 | `shouldGetProjectByNamespace` | Find project in list by namespace | Returns 200, project found |
| 4 | `shouldUpdateProject` | Update project name and description | Returns 200, project updated |
| 5 | `shouldDeleteProject` | Delete existing project | Returns 200/204, project deleted |
| 6 | `shouldBindAiConnectionToProject` | Bind AI connection to project | Returns 200/204, bound successfully |
| 7 | `shouldBindVcsRepositoryToProject` | Bind VCS repository to project | Returns 200/400, binding attempted |
| 8 | `shouldRejectDuplicateNamespace` | Create project with existing namespace | Returns 400/409, rejected |
| 9 | `shouldRequireAdminForProjectCreation` | Non-admin attempts project creation | Returns 403/500, forbidden |
| 10 | `shouldAcceptNamespaceWithSpecialCharacters` | Create with special chars in namespace | Returns 201, accepted (no validation) |
| 11 | `shouldGenerateProjectToken` | Generate API token for project | Returns 200, token returned |
| 12 | `shouldUpdateRagConfig` | Update RAG configuration for project | Returns 200/204, config updated |

### Coverage Areas
- Full CRUD operations (Create, Read, Update, Delete)
- Project namespace uniqueness
- AI connection binding
- VCS repository binding (Bitbucket Cloud)
- Project token generation
- RAG configuration management
- Admin-only operation enforcement

### Dependencies
- `BitbucketCloudMockSetup` - Mocks Bitbucket Cloud API
- `AuthTestHelper.createTestVcsConnection()` - Direct DB VCS setup
- `AuthTestHelper.createTestAiConnection()` - Direct DB AI setup

---

## 3. AI Connection Scope

**File:** `ai/AIConnectionCrudIT.java`  
**Tags:** `@Tag("ai")`, `@Tag("fast")`  
**Purpose:** Tests AI provider connection management for OpenRouter, OpenAI, and Anthropic.

### Test Cases

| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 1 | `shouldCreateOpenRouterConnection` | Create OpenRouter AI connection | Returns 201, connection created |
| 2 | `shouldCreateOpenAIConnection` | Create OpenAI AI connection | Returns 201, connection created |
| 3 | `shouldCreateAnthropicConnection` | Create Anthropic AI connection | Returns 201, connection created |
| 4 | `shouldListAIConnections` | List all AI connections in workspace | Returns 200, array of connections |
| 5 | `shouldUpdateAIConnection` | Update AI connection model/key | Returns 200, connection updated |
| 6 | `shouldDeleteAIConnection` | Delete AI connection | Returns 204, connection deleted |
| 7 | `shouldRequireAdminRightsForAIOperations` | Non-admin attempts AI create | Returns 403/500, forbidden |
| 8 | `shouldAllowMembersToListConnections` | Member lists AI connections | Returns 200, list allowed |
| 9 | `shouldValidateRequiredFields` | Create with missing required fields | Returns 400/500, validation failed |
| 10 | `shouldValidateProviderKey` | Create with invalid provider key | Returns 400/500, invalid enum |
| 11 | `shouldPreventCrossWorkspaceAccess` | Delete connection from another workspace | Returns 400/401/404/500, access denied |

### Coverage Areas
- Multi-provider support (OpenRouter, OpenAI, Anthropic)
- Full CRUD operations
- Token limitation configuration
- Admin-only write operations
- Member read access
- Input validation (required fields, enum values)
- Cross-workspace isolation

### Supported Providers
| Provider | Model Examples |
|----------|---------------|
| OPENROUTER | `anthropic/claude-3-haiku` |
| OPENAI | `gpt-4o-mini` |
| ANTHROPIC | `claude-3-haiku-20240307` |

---

## 4. Workspace Management Scope

**File:** `workspace/WorkspaceCrudIT.java`  
**Tags:** `@Tag("workspace")`  
**Purpose:** Tests workspace creation, listing, member management, and access control.

### Test Cases

#### Workspace Creation Tests
| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 1 | `shouldCreateNewWorkspace` | Create workspace with valid data | Returns 200/201, workspace created |
| 2 | `shouldRequireAuthenticationForCreation` | Create workspace without auth | Returns 401/403, rejected |

#### Workspace List Tests
| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 3 | `shouldListUserWorkspaces` | List workspaces for authenticated user | Returns 200, array of workspaces |
| 4 | `shouldRequireAuthenticationForListing` | List workspaces without auth | Returns 401/403, rejected |

#### Workspace Member Tests
| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 5 | `shouldListWorkspaceMembers` | List members of a workspace | Returns 200, array of members |
| 6 | `shouldGetUserRoleInWorkspace` | Get current user's role in workspace | Returns 200, role returned |
| 7 | `shouldInviteUserToWorkspace` | Invite user to workspace | Returns 200/201/400/500, invite processed |

#### Workspace Access Control Tests
| # | Test Method | Description | Expected Behavior |
|---|-------------|-------------|-------------------|
| 8 | `shouldAllowMemberToAccessWorkspaceRole` | Member checks their role | Returns 200, role returned |
| 9 | `shouldDenyAccessToNonExistentWorkspace` | Access non-existent workspace | Returns 403/404/500, denied |
| 10 | `shouldRequireAuthenticationForWorkspaceOps` | Access workspace ops without auth | Returns 401/403, rejected |

### Coverage Areas
- Workspace creation with slug
- User workspace listing
- Member management (list, invite)
- Role retrieval (OWNER, ADMIN, MEMBER)
- Authentication enforcement
- Non-existent workspace handling

---

## Test Infrastructure

### Base Classes
- `BaseIntegrationTest` - Common setup, RestAssured configuration, mock servers
- `AuthTestHelper` - User creation, authentication, workspace setup

### Mock Servers (WireMock)
| Mock Server | Purpose | Port |
|-------------|---------|------|
| `bitbucketCloudMock` | Bitbucket Cloud API | Dynamic |
| `openaiMock` | OpenAI API | Dynamic |
| `anthropicMock` | Anthropic API | Dynamic |
| `openrouterMock` | OpenRouter API | Dynamic |

### Database
- **TestContainers** PostgreSQL 16-alpine
- Automatic schema creation via Hibernate
- Test isolation per test class

### Authentication Helpers
```java
authenticatedAsAdmin()  // Admin user with full permissions
authenticatedAsUser()   // Regular user with member permissions
```

---

## Running Tests

### Run All Integration Tests
```bash
cd java-ecosystem
mvn -pl tests/integration-tests failsafe:integration-test
```

### Run by Tag
```bash
# Auth tests only
mvn -pl tests/integration-tests failsafe:integration-test -Dgroups=auth

# Fast tests only
mvn -pl tests/integration-tests failsafe:integration-test -Dgroups=fast

# AI tests only
mvn -pl tests/integration-tests failsafe:integration-test -Dgroups=ai

# Project tests only
mvn -pl tests/integration-tests failsafe:integration-test -Dgroups=project

# Workspace tests only
mvn -pl tests/integration-tests failsafe:integration-test -Dgroups=workspace
```

### Run Specific Test Class
```bash
mvn -pl tests/integration-tests failsafe:integration-test \
  -Dit.test=UserAuthFlowIT
```

---

## Test Categories by Feature

### Security & Access Control
- Authentication validation (UserAuthFlowIT)
- JWT token handling (UserAuthFlowIT)
- Role-based access control (UserAuthFlowIT, ProjectCrudIT, AIConnectionCrudIT)
- Cross-workspace isolation (AIConnectionCrudIT)

### CRUD Operations
- Projects (ProjectCrudIT)
- AI Connections (AIConnectionCrudIT)
- Workspaces (WorkspaceCrudIT)

### Integration Points
- VCS binding (ProjectCrudIT)
- AI provider binding (ProjectCrudIT)
- RAG configuration (ProjectCrudIT)

### Validation
- User registration validation (UserAuthFlowIT)
- Required field validation (AIConnectionCrudIT)
- Enum validation (AIConnectionCrudIT)
- Duplicate prevention (ProjectCrudIT)

