/**
 * User Controller - REST API endpoints for user management.
 * 
 * TypeScript implementation demonstrating cross-language retrieval.
 * 
 * Dependencies (via API):
 * - UserService (Python backend)
 * - AuthService (Python backend)
 */

import { Request, Response, Router } from 'express';

// Type definitions matching Python models
interface User {
  id: number;
  email: string;
  username: string;
  role: 'guest' | 'user' | 'admin' | 'superadmin';
  status: 'pending' | 'active' | 'suspended' | 'deleted';
  preferences: UserPreferences;
  created_at: string;
  last_login: string | null;
}

interface UserPreferences {
  email_notifications: boolean;
  push_notifications: boolean;
  theme: 'light' | 'dark';
  language: string;
}

interface CreateUserRequest {
  email: string;
  username: string;
  password: string;
  role?: 'user' | 'admin';
}

interface UpdateUserRequest {
  username?: string;
  email?: string;
  preferences?: Partial<UserPreferences>;
}

interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

// API base URL for Python backend
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';

/**
 * User API Controller
 * 
 * Handles HTTP requests for user management and delegates to Python services.
 */
export class UserController {
  private router: Router;

  constructor() {
    this.router = Router();
    this.initializeRoutes();
  }

  /**
   * Initialize API routes
   */
  private initializeRoutes(): void {
    // User CRUD
    this.router.get('/users', this.listUsers.bind(this));
    this.router.get('/users/:id', this.getUser.bind(this));
    this.router.post('/users', this.createUser.bind(this));
    this.router.put('/users/:id', this.updateUser.bind(this));
    this.router.delete('/users/:id', this.deleteUser.bind(this));
    
    // User actions
    this.router.post('/users/:id/activate', this.activateUser.bind(this));
    this.router.post('/users/:id/suspend', this.suspendUser.bind(this));
    this.router.put('/users/:id/password', this.changePassword.bind(this));
    this.router.put('/users/:id/role', this.assignRole.bind(this));
  }

  /**
   * Get router instance
   */
  public getRouter(): Router {
    return this.router;
  }

  /**
   * GET /users - List all users
   * 
   * Query params:
   * - status: Filter by user status
   * - role: Filter by user role
   * - limit: Max results (default: 100)
   * - offset: Pagination offset
   */
  private async listUsers(req: Request, res: Response): Promise<void> {
    try {
      const { status, role, limit = '100', offset = '0' } = req.query;

      const params = new URLSearchParams();
      if (status) params.append('status', status as string);
      if (role) params.append('role', role as string);
      params.append('limit', limit as string);
      params.append('offset', offset as string);

      const response = await fetch(`${BACKEND_URL}/api/users?${params}`);
      const users: User[] = await response.json();

      res.json({
        success: true,
        data: users,
        meta: {
          total: users.length,
          limit: parseInt(limit as string),
          offset: parseInt(offset as string),
        },
      } as ApiResponse<User[]>);

    } catch (error) {
      console.error('Error listing users:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to list users',
      } as ApiResponse<null>);
    }
  }

  /**
   * GET /users/:id - Get user by ID
   */
  private async getUser(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;

      const response = await fetch(`${BACKEND_URL}/api/users/${id}`);
      
      if (!response.ok) {
        if (response.status === 404) {
          res.status(404).json({
            success: false,
            error: 'User not found',
          } as ApiResponse<null>);
          return;
        }
        throw new Error(`Backend error: ${response.status}`);
      }

      const user: User = await response.json();

      res.json({
        success: true,
        data: user,
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error getting user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to get user',
      } as ApiResponse<null>);
    }
  }

  /**
   * POST /users - Create new user
   */
  private async createUser(req: Request, res: Response): Promise<void> {
    try {
      const { email, username, password, role }: CreateUserRequest = req.body;

      // Basic validation
      if (!email || !username || !password) {
        res.status(400).json({
          success: false,
          error: 'Missing required fields: email, username, password',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, username, password, role: role || 'user' }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        res.status(response.status).json({
          success: false,
          error: errorData.message || 'Failed to create user',
        } as ApiResponse<null>);
        return;
      }

      const user: User = await response.json();

      res.status(201).json({
        success: true,
        data: user,
        message: 'User created successfully',
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error creating user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to create user',
      } as ApiResponse<null>);
    }
  }

  /**
   * PUT /users/:id - Update user profile
   */
  private async updateUser(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;
      const updates: UpdateUserRequest = req.body;

      // Verify authenticated user can update this profile
      const currentUserId = this.getCurrentUserId(req);
      if (currentUserId !== parseInt(id) && !this.isAdmin(req)) {
        res.status(403).json({
          success: false,
          error: 'Permission denied',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });

      if (!response.ok) {
        const errorData = await response.json();
        res.status(response.status).json({
          success: false,
          error: errorData.message || 'Failed to update user',
        } as ApiResponse<null>);
        return;
      }

      const user: User = await response.json();

      res.json({
        success: true,
        data: user,
        message: 'User updated successfully',
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error updating user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to update user',
      } as ApiResponse<null>);
    }
  }

  /**
   * DELETE /users/:id - Delete user (admin only)
   */
  private async deleteUser(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;

      if (!this.isAdmin(req)) {
        res.status(403).json({
          success: false,
          error: 'Admin access required',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const errorData = await response.json();
        res.status(response.status).json({
          success: false,
          error: errorData.message || 'Failed to delete user',
        } as ApiResponse<null>);
        return;
      }

      res.json({
        success: true,
        message: 'User deleted successfully',
      } as ApiResponse<null>);

    } catch (error) {
      console.error('Error deleting user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to delete user',
      } as ApiResponse<null>);
    }
  }

  /**
   * POST /users/:id/activate - Activate user account
   */
  private async activateUser(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;

      if (!this.isAdmin(req)) {
        res.status(403).json({
          success: false,
          error: 'Admin access required',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}/activate`, {
        method: 'POST',
      });

      const user: User = await response.json();

      res.json({
        success: true,
        data: user,
        message: 'User activated successfully',
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error activating user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to activate user',
      } as ApiResponse<null>);
    }
  }

  /**
   * POST /users/:id/suspend - Suspend user account
   */
  private async suspendUser(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;
      const { reason } = req.body;

      if (!this.isAdmin(req)) {
        res.status(403).json({
          success: false,
          error: 'Admin access required',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}/suspend`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });

      const user: User = await response.json();

      res.json({
        success: true,
        data: user,
        message: 'User suspended',
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error suspending user:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to suspend user',
      } as ApiResponse<null>);
    }
  }

  /**
   * PUT /users/:id/password - Change password
   */
  private async changePassword(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;
      const { old_password, new_password } = req.body;

      // Only user themselves can change their password
      const currentUserId = this.getCurrentUserId(req);
      if (currentUserId !== parseInt(id)) {
        res.status(403).json({
          success: false,
          error: 'Permission denied',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}/password`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_password, new_password }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        res.status(response.status).json({
          success: false,
          error: errorData.message || 'Failed to change password',
        } as ApiResponse<null>);
        return;
      }

      res.json({
        success: true,
        message: 'Password changed successfully',
      } as ApiResponse<null>);

    } catch (error) {
      console.error('Error changing password:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to change password',
      } as ApiResponse<null>);
    }
  }

  /**
   * PUT /users/:id/role - Assign role (admin only)
   */
  private async assignRole(req: Request, res: Response): Promise<void> {
    try {
      const { id } = req.params;
      const { role } = req.body;

      if (!this.isAdmin(req)) {
        res.status(403).json({
          success: false,
          error: 'Admin access required',
        } as ApiResponse<null>);
        return;
      }

      const response = await fetch(`${BACKEND_URL}/api/users/${id}/role`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      });

      const user: User = await response.json();

      res.json({
        success: true,
        data: user,
        message: `Role '${role}' assigned successfully`,
      } as ApiResponse<User>);

    } catch (error) {
      console.error('Error assigning role:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to assign role',
      } as ApiResponse<null>);
    }
  }

  // Helper methods
  private getCurrentUserId(req: Request): number {
    // In production, extract from JWT token
    return (req as any).user?.id || 0;
  }

  private isAdmin(req: Request): boolean {
    // In production, check JWT token claims
    const role = (req as any).user?.role;
    return role === 'admin' || role === 'superadmin';
  }
}

export default new UserController().getRouter();
