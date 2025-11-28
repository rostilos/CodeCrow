# Frontend

React-based single-page application providing the user interface for CodeCrow.

## Technology Stack

- **Framework**: React 18
- **Build Tool**: Vite
- **Language**: TypeScript
- **UI Components**: shadcn/ui with Radix UI
- **Styling**: Tailwind CSS
- **State Management**: TanStack Query (React Query)
- **Routing**: React Router
- **HTTP Client**: Axios
- **Deployment**: Static build served with `serve`

## Project Structure

```
frontend/
├── public/                   # Static assets
│   ├── favicon.ico
│   ├── logo.png
│   └── robots.txt
├── src/
│   ├── main.tsx             # Application entry point
│   ├── App.tsx              # Root component
│   ├── api_service/         # API client layer
│   │   ├── api.ts          # Base API configuration
│   │   ├── api.interface.ts # TypeScript interfaces
│   │   ├── ai/             # AI connection APIs
│   │   ├── analysis/       # Analysis APIs
│   │   ├── auth/           # Authentication APIs
│   │   ├── codeHosting/    # VCS integration APIs
│   │   ├── project/        # Project APIs
│   │   ├── user/           # User APIs
│   │   └── workspace/      # Workspace APIs
│   ├── components/          # React components
│   │   ├── ui/             # shadcn/ui base components
│   │   ├── AppSidebar.tsx
│   │   ├── DashboardLayout.tsx
│   │   ├── ProjectStats.tsx
│   │   ├── IssuesByFileDisplay.tsx
│   │   └── ...
│   ├── pages/               # Page components
│   │   ├── auth/           # Login, Register
│   │   ├── dashboard/      # Main dashboard
│   │   ├── workspace/      # Workspace management
│   │   ├── project/        # Project views
│   │   └── analysis/       # Analysis results
│   ├── context/             # React contexts
│   ├── hooks/               # Custom hooks
│   ├── lib/                 # Utility libraries
│   └── config/              # Configuration
├── package.json             # Dependencies
├── vite.config.ts          # Vite configuration
├── tsconfig.json           # TypeScript config
├── tailwind.config.ts      # Tailwind config
└── Dockerfile              # Container build
```

## Key Features

### Authentication
- JWT-based authentication
- Login/Register forms
- Protected routes
- Session persistence
- Auto-logout on token expiration

### Workspace Management
- Create and manage workspaces
- Invite and manage members
- Role assignment (Owner, Admin, Member, Viewer)
- Workspace switching

### Project Management
- Create projects linked to Bitbucket repositories
- Configure AI connections
- Generate webhook tokens
- View project statistics
- Default branch selection

### Analysis Results
- View pull request analyses
- Browse branch issues
- Filter issues by severity, category, file
- Issue detail view with code snippets
- Analysis history and trends

### Issue Tracking
- Active issues dashboard
- Resolved issues tracking
- Group issues by file
- Severity-based filtering
- Search and filter capabilities

### VCS Integration
- Connect Bitbucket accounts
- Browse accessible repositories
- Verify webhook configuration
- Repository metadata display

### Statistics & Insights
- Project health metrics
- Issue trends over time
- PR/Branch analysis history
- Severity distribution charts
- File-level issue hotspots

## API Integration

### Base Configuration

**src/config/api.ts**:
```typescript
export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8081/api';
export const WEBHOOK_URL = import.meta.env.VITE_WEBHOOK_URL || 'http://localhost:8082';
```

### API Service Layer

**src/api_service/api.ts**:
- Axios instance with interceptors
- Request/response transformations
- Error handling
- Token injection
- Base CRUD operations

**Authentication Interceptor**:
```typescript
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
```

**Error Interceptor**:
```typescript
api.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      // Redirect to login
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);
```

### API Modules

Each API module provides typed functions for specific domain:

**auth/auth.api.ts**:
```typescript
export const authApi = {
  login: (credentials: LoginRequest) => api.post('/auth/login', credentials),
  register: (data: RegisterRequest) => api.post('/auth/register', data),
  logout: () => api.post('/auth/logout'),
  getCurrentUser: () => api.get('/auth/me')
};
```

**workspace/workspace.api.ts**:
```typescript
export const workspaceApi = {
  list: () => api.get('/workspaces'),
  create: (data: WorkspaceCreate) => api.post('/workspaces', data),
  get: (id: string) => api.get(`/workspaces/${id}`),
  update: (id: string, data: WorkspaceUpdate) => api.put(`/workspaces/${id}`, data),
  delete: (id: string) => api.delete(`/workspaces/${id}`),
  addMember: (id: string, data: MemberAdd) => api.post(`/workspaces/${id}/members`, data)
};
```

## Key Components

### DashboardLayout
Main layout wrapper with sidebar navigation.

**Features**:
- Workspace switcher
- Navigation menu
- User profile menu
- Responsive design

### ProjectStats
Displays project statistics and health metrics.

**Metrics**:
- Total analyses
- Active issues count
- Resolved issues count
- Analysis trends chart
- Severity distribution

### IssuesByFileDisplay
Groups and displays issues by file path.

**Features**:
- Collapsible file groups
- Severity badges
- Line number links
- Code snippets
- Filter by severity

### BranchPRHierarchy
Visualizes branch and PR relationships.

**Features**:
- Branch tree view
- PR status indicators
- Analysis status
- Drill-down to details

### IssueFilterSidebar
Filter issues by various criteria.

**Filters**:
- Severity (High, Medium, Low)
- Category (Security, Quality, Performance, etc.)
- Status (Active, Resolved)
- File path search
- Date range

### ProtectedRoute
Route wrapper requiring authentication.

**Usage**:
```typescript
<ProtectedRoute>
  <Dashboard />
</ProtectedRoute>
```

### WorkspaceGuard
Ensures user has access to current workspace.

**Usage**:
```typescript
<WorkspaceGuard workspaceId={id}>
  <WorkspaceContent />
</WorkspaceGuard>
```

## State Management

### TanStack Query

Used for server state management.

**Example**:
```typescript
const { data, isLoading, error } = useQuery({
  queryKey: ['projects', workspaceId],
  queryFn: () => projectApi.list(workspaceId)
});
```

**Mutations**:
```typescript
const createProject = useMutation({
  mutationFn: projectApi.create,
  onSuccess: () => {
    queryClient.invalidateQueries(['projects']);
  }
});
```

### React Context

**AuthContext**:
- Current user state
- Login/logout functions
- Authentication status

**ThemeContext**:
- Dark/light mode toggle
- Theme persistence

## Routing

**Main Routes**:
- `/` - Welcome page or redirect to dashboard
- `/login` - Login page
- `/register` - Registration page
- `/dashboard` - Main dashboard (protected)
- `/workspaces` - Workspace list (protected)
- `/workspaces/:id` - Workspace detail (protected)
- `/projects/:id` - Project detail (protected)
- `/projects/:id/analysis` - Analysis results (protected)
- `/analysis/:id` - Single analysis view (protected)

## Styling

### Tailwind CSS

Utility-first CSS framework with custom configuration.

**tailwind.config.ts**:
```typescript
export default {
  darkMode: ["class"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        // ... custom colors
      }
    }
  }
}
```

### shadcn/ui Components

Pre-built accessible components:
- Button, Input, Select, Checkbox
- Dialog, Dropdown, Popover
- Table, Card, Badge
- Tabs, Accordion, Collapsible
- Toast notifications

**Installation**:
```bash
npx shadcn-ui@latest add button
npx shadcn-ui@latest add dialog
```

## Configuration

**.env**:
```bash
VITE_API_URL=http://localhost:8081/api
VITE_WEBHOOK_URL=http://localhost:8082
SERVER_PORT=8080
```

**Modes**:
- `.env.development` - Development mode
- `.env.production` - Production build

## Build & Deploy

### Development

```bash
npm install
npm run dev
```

Application runs on `http://localhost:5173` (Vite default).

### Production Build

```bash
npm run build
```

Outputs to `dist/` directory.

### Serve Static Build

```bash
npm install -g serve
serve -s dist -l 8080
```

### Docker Build

**Dockerfile**:
```dockerfile
FROM node:18-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:18-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
CMD ["serve", "-s", "dist", "-l", "8080"]
```

**Build and Run**:
```bash
cd frontend
docker build -t codecrow-frontend .
docker run -p 8080:8080 codecrow-frontend
```

## Environment Variables

Accessed via `import.meta.env`:

- `VITE_API_URL` - Backend API base URL
- `VITE_WEBHOOK_URL` - Webhook URL for display
- `MODE` - `development` or `production`
- `DEV` - Boolean, true in dev mode
- `PROD` - Boolean, true in prod mode

## TypeScript Interfaces

**src/api_service/api.interface.ts**:
```typescript
export interface User {
  id: string;
  username: string;
  email: string;
  roles: string[];
}

export interface Workspace {
  id: string;
  name: string;
  description?: string;
  createdAt: string;
  members: WorkspaceMember[];
}

export interface Project {
  id: string;
  name: string;
  workspaceId: string;
  repositoryUrl: string;
  defaultBranch: string;
  createdAt: string;
}

export interface CodeAnalysis {
  id: string;
  projectId: string;
  pullRequestId?: string;
  branchId?: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
  createdAt: string;
  issues: Issue[];
}

export interface Issue {
  id: string;
  file: string;
  line: number;
  severity: 'HIGH' | 'MEDIUM' | 'LOW';
  category: string;
  description: string;
  suggestion?: string;
  resolved: boolean;
}
```

## Testing

```bash
# Unit tests
npm run test

# E2E tests (if configured)
npm run test:e2e
```

## Code Quality

### ESLint

```bash
npm run lint
```

**eslint.config.js**:
- React hooks rules
- TypeScript rules
- Accessibility rules

### Prettier

```bash
npm run format
```

## Common Issues

### API Connection Failed
Check `VITE_API_URL` in `.env` matches backend URL.

### CORS Errors
Backend must allow frontend origin in CORS configuration.

### Build Fails
Clear `node_modules` and reinstall: `rm -rf node_modules && npm install`

### Hot Reload Not Working
Restart dev server. Check Vite config.

### Type Errors
Run `npm run typecheck` to see TypeScript errors.

## Development Tips

- Use React DevTools for component debugging
- Use TanStack Query DevTools for state inspection
- Enable source maps in development
- Use ESLint and Prettier VSCode extensions
- Leverage shadcn/ui component customization
- Keep API interfaces in sync with backend
- Use React.memo for expensive components
- Implement error boundaries
- Add loading states for all async operations
- Test responsive design on multiple devices

