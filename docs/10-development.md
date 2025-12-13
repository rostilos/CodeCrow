# Development Guide

Guide for developing and contributing to CodeCrow.

## Development Environment Setup

### Prerequisites

- Java 17 JDK
- Maven 3.8+
- Node.js 18+ (with npm or bun)
- Python 3.10+
- Docker & Docker Compose
- Git
- IDE (IntelliJ IDEA recommended)

### Initial Setup

#### 1. Clone Repository

```bash
git clone <repository-url>
cd codecrow
```

#### 2. Setup Java Development

**IntelliJ IDEA**:
1. Open project from `java-ecosystem/pom.xml`
2. Install Lombok plugin
3. Enable annotation processing (Preferences → Build → Compiler → Annotation Processors)
4. Configure Java 17 SDK
5. Import Maven dependencies

**Eclipse**:
1. Install Lombok
2. Import as Maven project
3. Configure Java 17

#### 3. Setup Python Development

```bash
# MCP Client
cd python-ecosystem/mcp-client
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# RAG Pipeline
cd ../rag-pipeline
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 4. Setup Frontend Development

```bash
cd frontend
npm install
# Or with bun
bun install
```

#### 5. Start Infrastructure Services

```bash
cd deployment
docker compose up -d postgres redis qdrant
```

This starts only the infrastructure services, allowing you to run application services locally.

### Running Services Locally

#### Web Server

```bash
cd java-ecosystem/services/web-server
mvn spring-boot:run
```

Access: `http://localhost:8081`  
Swagger UI: `http://localhost:8081/swagger-ui-custom.html`

#### Pipeline Agent

```bash
cd java-ecosystem/services/pipeline-agent
mvn spring-boot:run
```

Access: `http://localhost:8082`

#### MCP Client

```bash
cd python-ecosystem/mcp-client
source venv/bin/activate
cp .env.sample .env
# Edit .env with configuration
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Access: `http://localhost:8000`

#### RAG Pipeline

```bash
cd python-ecosystem/rag-pipeline
source venv/bin/activate
cp .env.sample .env
# Edit .env with Qdrant URL and OpenRouter key
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Access: `http://localhost:8001`

#### Frontend

```bash
cd frontend
npm run dev
# Or
bun run dev
```

Access: `http://localhost:5173`

## Development Workflow

### Branch Strategy

- `main`: Production-ready code
- `develop`: Integration branch
- `feature/*`: Feature development
- `bugfix/*`: Bug fixes
- `hotfix/*`: Production hotfixes

### Commit Convention

Use conventional commits:

```
feat: Add user authentication
fix: Resolve database connection issue
docs: Update API documentation
refactor: Simplify analysis service
test: Add unit tests for project service
chore: Update dependencies
```

### Pull Request Process

1. Create feature branch from `develop`
2. Implement changes with tests
3. Update documentation
4. Create pull request
5. Code review
6. Merge after approval

## Code Standards

### Java

**Code Style**:
- Google Java Style Guide
- Use Lombok for boilerplate reduction
- Follow Spring Boot best practices

**Example**:
```java
@Service
@RequiredArgsConstructor
@Slf4j
public class ProjectService {
    
    private final ProjectRepository projectRepository;
    
    @Transactional(readOnly = true)
    public Project findById(UUID id) {
        return projectRepository.findById(id)
            .orElseThrow(() -> new ResourceNotFoundException("Project not found"));
    }
    
    @Transactional
    public Project create(ProjectCreateRequest request) {
        log.info("Creating project: {}", request.getName());
        
        Project project = Project.builder()
            .name(request.getName())
            .description(request.getDescription())
            .build();
            
        return projectRepository.save(project);
    }
}
```

**Testing**:
```java
@SpringBootTest
@Transactional
class ProjectServiceTest {
    
    @Autowired
    private ProjectService projectService;
    
    @Test
    void shouldCreateProject() {
        ProjectCreateRequest request = new ProjectCreateRequest();
        request.setName("Test Project");
        
        Project project = projectService.create(request);
        
        assertNotNull(project.getId());
        assertEquals("Test Project", project.getName());
    }
}
```

### Python

**Code Style**:
- PEP 8
- Use type hints
- Docstrings for functions/classes

**Example**:
```python
from typing import List
from pydantic import BaseModel

class AnalysisRequest(BaseModel):
    """Request model for code analysis."""
    
    project_id: str
    files: List[str]
    
    class Config:
        schema_extra = {
            "example": {
                "project_id": "proj-123",
                "files": ["src/main.py"]
            }
        }

async def analyze_code(request: AnalysisRequest) -> AnalysisResponse:
    """
    Analyze code for issues.
    
    Args:
        request: Analysis request with project and files
        
    Returns:
        Analysis response with issues found
        
    Raises:
        ValidationError: If request is invalid
    """
    # Implementation
    pass
```

**Testing**:
```python
import pytest
from fastapi.testclient import TestClient

def test_analyze_endpoint(client: TestClient):
    """Test analysis endpoint."""
    response = client.post(
        "/analyze",
        json={"project_id": "test", "files": ["test.py"]}
    )
    assert response.status_code == 200
    assert "issues" in response.json()
```

### TypeScript/React

**Code Style**:
- ESLint configuration
- Prettier formatting
- Functional components with hooks

**Example**:
```typescript
interface ProjectCardProps {
  project: Project;
  onSelect: (id: string) => void;
}

export const ProjectCard: React.FC<ProjectCardProps> = ({
  project,
  onSelect
}) => {
  const handleClick = () => {
    onSelect(project.id);
  };
  
  return (
    <Card onClick={handleClick}>
      <CardHeader>
        <CardTitle>{project.name}</CardTitle>
      </CardHeader>
      <CardContent>
        <p>{project.description}</p>
      </CardContent>
    </Card>
  );
};
```

## Testing (TODO)

### Java Unit Tests

```bash
# Run all tests
cd java-ecosystem
mvn test

# Run specific module
cd services/web-server
mvn test

# Run specific test class
mvn test -Dtest=ProjectServiceTest

# Skip tests during build
mvn clean package -DskipTests
```

### Python Tests

```bash
# MCP Client tests
cd python-ecosystem/mcp-client
pytest

# RAG Pipeline tests
cd ../rag-pipeline
pytest

# With coverage
pytest --cov=src --cov-report=html
```

### Frontend Tests

```bash
cd frontend
npm test
# Or
bun test
```

### Integration Tests

```bash
# Start all services with Docker Compose
cd deployment
docker compose up -d

# Run integration tests
cd ../
./scripts/integration-tests.sh
```

## Debugging

### Java Services

**IntelliJ IDEA Remote Debug**:
1. Services expose debug ports (5005, 5006)
2. Create Remote JVM Debug configuration
3. Set host: localhost, port: 5005 or 5006
4. Set breakpoints
5. Run debug configuration

**Docker Container Debug**:
```yaml
web-server:
  environment:
    JAVA_OPTS: "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005"
  ports:
    - "5005:5005"
```

### Python Services

**VSCode Debug Configuration**:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "MCP Client",
      "type": "python",
      "request": "launch",
      "module": "uvicorn",
      "args": ["main:app", "--reload"],
      "cwd": "${workspaceFolder}/python-ecosystem/mcp-client"
    }
  ]
}
```

**Print Debugging**:
```python
import logging
logger = logging.getLogger(__name__)
logger.debug("Variable value: %s", variable)
```

### Frontend

**React DevTools**:
- Install browser extension
- Inspect component tree
- View props and state

**Network Tab**:
- Monitor API requests
- Check request/response payloads
- Verify authentication headers

## Database Development

### Schema Changes

1. Update JPA entity
2. Hibernate auto-generates schema changes (development)
3. For production, create manual migration

**Example Migration** (Flyway):
```sql
-- V1.1__add_analysis_duration.sql
ALTER TABLE code_analysis 
ADD COLUMN duration_ms INTEGER;
```

### Test Data

Create test data script:
```sql
-- test-data.sql
INSERT INTO "user" (id, username, email, password, roles)
VALUES (
  gen_random_uuid(),
  'testuser',
  'test@example.com',
  '$2a$10$...',
  ARRAY['USER']
);
```

Load test data:
```bash
cat test-data.sql | docker exec -i codecrow-postgres \
  psql -U codecrow_user -d codecrow_ai
```

### Database Console

```bash
# Connect to database
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai

# Common queries
\dt                        -- List tables
\d table_name             -- Describe table
SELECT * FROM "user";     -- Query
```

## API Development

### Adding New Endpoint

1. **Define DTO**:
```java
@Data
public class ProjectCreateRequest {
    @NotNull
    private String name;
    private String description;
}
```

2. **Update Service**:
```java
@Service
public class ProjectService {
    public Project create(ProjectCreateRequest request) {
        // Implementation
    }
}
```

3. **Add Controller Endpoint**:
```java
@RestController
@RequestMapping("/api/projects")
public class ProjectController {
    
    @PostMapping
    public ResponseEntity<Project> create(@Valid @RequestBody ProjectCreateRequest request) {
        Project project = projectService.create(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(project);
    }
}
```

4. **Add Tests**:
```java
@Test
void shouldCreateProject() {
    // Test implementation
}
```

5. **Update API Documentation**:
- Swagger annotations automatically generate docs
- Update API reference docs

### API Versioning

Current version: `/api/v1`

For breaking changes, create new version:
```java
@RequestMapping("/api/v2/projects")
```

## Performance Optimization

### Database Query Optimization

**Use Projections**:
```java
public interface ProjectSummary {
    String getId();
    String getName();
}

List<ProjectSummary> findAllProjectedBy();
```

**Batch Fetching**:
```java
@Entity
public class Project {
    @OneToMany(fetch = FetchType.LAZY)
    @BatchSize(size = 10)
    private List<Branch> branches;
}
```

**Query Optimization**:
```java
@Query("SELECT p FROM Project p LEFT JOIN FETCH p.branches WHERE p.id = :id")
Project findByIdWithBranches(@Param("id") UUID id);
```

### Caching

**Spring Cache**:
```java
@Cacheable(value = "projects", key = "#id")
public Project findById(UUID id) {
    return projectRepository.findById(id).orElseThrow();
}

@CacheEvict(value = "projects", key = "#project.id")
public Project update(Project project) {
    return projectRepository.save(project);
}
```

### Async Processing

```java
@Async
public CompletableFuture<AnalysisResult> analyzeAsync(AnalysisRequest request) {
    AnalysisResult result = performAnalysis(request);
    return CompletableFuture.completedFuture(result);
}
```

## Common Development Tasks

### Add New Entity

1. Create entity class with JPA annotations
2. Create repository interface
3. Create service class
4. Create DTOs
5. Create controller endpoints
6. Add tests
7. Update documentation

### Add New Analysis Feature

1. Update analysis request/response models
2. Modify prompt generation in MCP client
3. Update result processing in pipeline agent
4. Add database fields if needed
5. Update frontend display
6. Test end-to-end

### Update Dependencies

**Java**:
```bash
cd java-ecosystem
mvn versions:display-dependency-updates
mvn versions:use-latest-versions
```

**Python**:
```bash
pip list --outdated
pip install --upgrade <package>
pip freeze > requirements.txt
```

**Frontend**:
```bash
npm outdated
npm update
# Or
bun update
```

## Troubleshooting Development Issues

### Port Already in Use

```bash
# Find process using port
lsof -i :8081
# Kill process
kill -9 <PID>
```

### Maven Build Fails

```bash
# Clean and rebuild
mvn clean install -U

# Clear local repository
rm -rf ~/.m2/repository
```

### Python Import Errors

```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Docker Issues

```bash
# Rebuild images
docker compose build --no-cache

# Clean system
docker system prune -a
```

## Contributing Guidelines

### Before Submitting PR

- [ ] Code follows style guidelines
- [ ] All tests pass
- [ ] New tests added for new features
- [ ] Documentation updated
- [ ] Commit messages follow convention
- [ ] No commented-out code
- [ ] No debug statements
- [ ] Secrets not committed

### Code Review Checklist

- Logic correctness
- Error handling
- Security considerations
- Performance implications
- Test coverage
- Documentation completeness
- Code style adherence

## Resources

### Documentation
- Spring Boot: https://spring.io/projects/spring-boot
- FastAPI: https://fastapi.tiangolo.com/
- React: https://react.dev/
- shadcn/ui: https://ui.shadcn.com/

### Tools
- Postman: API testing
- DBeaver: Database management
- Docker Desktop: Container management
- GitHub Copilot: AI code assistant

### Community
- GitHub Issues: Bug reports and feature requests
- GitHub Discussions: Q&A and ideas
- Slack/Discord: Real-time chat (if available)

