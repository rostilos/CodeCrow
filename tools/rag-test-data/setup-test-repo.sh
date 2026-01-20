#!/bin/bash

# RAG Delta Index Test Data Setup Script
# This script helps set up a test repository with different branches for delta index testing.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}"

echo "=== RAG Delta Index Test Data Setup ==="
echo ""

# Check if target repo is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <target-repo-path>"
    echo ""
    echo "Example:"
    echo "  $0 /path/to/test-repo"
    echo ""
    echo "This script will:"
    echo "  1. Initialize a git repo at the target path (if not exists)"
    echo "  2. Create 'main' branch with base files"
    echo "  3. Create 'release/1.0' branch with performance improvements"
    echo "  4. Create 'feature/user-roles' branch with role management"
    echo "  5. Create 'feature/notifications' branch with notifications"
    exit 1
fi

TARGET_REPO="$1"

# Create target directory if it doesn't exist
mkdir -p "$TARGET_REPO"
cd "$TARGET_REPO"

# Initialize git repo if needed
if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
    git config user.email "test@codecrow.dev"
    git config user.name "CodeCrow Test"
fi

# Function to copy files
copy_branch_files() {
    local source_dir="$1"
    local commit_msg="$2"
    
    # Clean src and config directories
    rm -rf src config
    
    # Copy files from source
    cp -r "${DATA_DIR}/${source_dir}/." .
    
    # Add and commit
    git add -A
    git commit -m "$commit_msg" || echo "No changes to commit"
}

echo ""
echo "=== Setting up main branch ==="
git checkout -B main 2>/dev/null || git checkout main
copy_branch_files "master" "Initial commit: Base application with User, Auth, and Config"
echo "✓ main branch ready"

echo ""
echo "=== Setting up release/1.0 branch ==="
git checkout -B release/1.0
copy_branch_files "release-1.0" "Release 1.0: Added caching and rate limiting"
echo "✓ release/1.0 branch ready"

echo ""
echo "=== Setting up feature/user-roles branch ==="
git checkout main
git checkout -B feature/user-roles
copy_branch_files "feature-1.0" "Feature: User Roles - RBAC implementation"
echo "✓ feature/user-roles branch ready"

echo ""
echo "=== Setting up feature/notifications branch ==="
git checkout main
git checkout -B feature/notifications
copy_branch_files "feature-1.1" "Feature: Notifications - User notification system"
echo "✓ feature/notifications branch ready"

# Return to main branch
git checkout main

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Repository structure:"
echo ""
git branch -a
echo ""
echo "Branch comparison (files changed from main):"
echo ""
echo "release/1.0:"
git diff --stat main..release/1.0 --name-only 2>/dev/null | sed 's/^/  /'
echo ""
echo "feature/user-roles:"
git diff --stat main..feature/user-roles --name-only 2>/dev/null | sed 's/^/  /'
echo ""
echo "feature/notifications:"
git diff --stat main..feature/notifications --name-only 2>/dev/null | sed 's/^/  /'
echo ""
echo "Next steps:"
echo "  1. Add a remote: git remote add origin <your-repo-url>"
echo "  2. Push all branches: git push -u origin --all"
echo "  3. Configure RAG delta indexing in your project settings"
echo "  4. Trigger analysis on each branch to test delta indexes"
