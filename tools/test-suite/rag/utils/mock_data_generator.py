#!/usr/bin/env python3
"""
Mock Data Generator - Generate mock PR payloads and test data.
"""
import json
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional


class MockDataGenerator:
    """Generate mock data for RAG testing."""
    
    # Sample file patterns
    FILE_PATTERNS = {
        "python": {
            "extensions": [".py"],
            "paths": [
                "src/services/{name}_service.py",
                "src/models/{name}.py",
                "src/utils/{name}_utils.py",
                "src/api/{name}_api.py",
                "tests/test_{name}.py"
            ]
        },
        "typescript": {
            "extensions": [".ts", ".tsx"],
            "paths": [
                "src/components/{Name}Component.tsx",
                "src/services/{name}Service.ts",
                "src/hooks/use{Name}.ts",
                "src/utils/{name}Utils.ts"
            ]
        },
        "java": {
            "extensions": [".java"],
            "paths": [
                "src/main/java/com/example/service/{Name}Service.java",
                "src/main/java/com/example/model/{Name}.java",
                "src/main/java/com/example/controller/{Name}Controller.java",
                "src/main/java/com/example/repository/{Name}Repository.java"
            ]
        }
    }
    
    # Sample domain concepts
    DOMAIN_CONCEPTS = [
        "user", "order", "product", "payment", "notification",
        "auth", "session", "analytics", "report", "config",
        "cache", "queue", "worker", "scheduler", "migration"
    ]
    
    # Sample PR title patterns
    PR_TITLE_PATTERNS = [
        "feat: Add {concept} functionality",
        "fix: Fix {concept} validation bug",
        "refactor: Improve {concept} performance",
        "chore: Update {concept} dependencies",
        "docs: Update {concept} documentation",
        "test: Add tests for {concept} service"
    ]
    
    def __init__(self, seed: Optional[int] = None):
        if seed:
            random.seed(seed)
    
    def generate_pr_payload(
        self,
        workspace: str = "test-workspace",
        project: str = "test-project",
        branch: str = "feature/test",
        num_files: int = 3,
        language: str = "python"
    ) -> Dict[str, Any]:
        """
        Generate a mock PR payload.
        
        Args:
            workspace: Workspace name
            project: Project name
            branch: Branch name
            num_files: Number of changed files
            language: Primary language
            
        Returns:
            Mock PR payload dict
        """
        concept = random.choice(self.DOMAIN_CONCEPTS)
        
        # Generate changed files
        changed_files = self._generate_changed_files(concept, language, num_files)
        
        # Generate diff snippets
        diff_snippets = self._generate_diff_snippets(changed_files, concept, language)
        
        # Generate title and description
        title = random.choice(self.PR_TITLE_PATTERNS).format(concept=concept)
        description = self._generate_description(concept, changed_files)
        
        return {
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "base_branch": "main",
            "pr_title": title,
            "pr_description": description,
            "changed_files": changed_files,
            "diff_snippets": diff_snippets,
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "concept": concept,
                "language": language
            }
        }
    
    def generate_pr_scenario(
        self,
        name: str,
        description: str,
        num_files: int = 2,
        language: str = "python",
        workspace: str = "test-workspace",
        project: str = "test-project"
    ) -> Dict[str, Any]:
        """
        Generate a complete PR test scenario.
        
        Returns:
            PR scenario with expected results
        """
        payload = self.generate_pr_payload(
            workspace=workspace,
            project=project,
            num_files=num_files,
            language=language
        )
        
        concept = payload["metadata"]["concept"]
        
        return {
            "name": name,
            "description": description,
            "workspace": payload["workspace"],
            "project": payload["project"],
            "branch": payload["branch"],
            "base_branch": payload["base_branch"],
            "pr_title": payload["pr_title"],
            "pr_description": payload["pr_description"],
            "changed_files": payload["changed_files"],
            "diff_snippets": payload["diff_snippets"],
            "expected_results": {
                "should_find": [concept],
                "should_not_find": [],
                "min_results": 1,
                "min_relevance_score": 0.5
            }
        }
    
    def generate_search_queries(
        self,
        num_queries: int = 10,
        language: str = "python"
    ) -> List[Dict[str, Any]]:
        """
        Generate search test queries.
        
        Returns:
            List of query definitions
        """
        queries = []
        
        for _ in range(num_queries):
            concept = random.choice(self.DOMAIN_CONCEPTS)
            
            query_types = [
                {
                    "type": "keyword",
                    "query": f"{concept} service implementation",
                    "expected_patterns": [concept]
                },
                {
                    "type": "natural_language",
                    "query": f"How does the {concept} functionality work?",
                    "expected_patterns": [concept]
                },
                {
                    "type": "function_search",
                    "query": f"get_{concept}_by_id function",
                    "expected_patterns": ["get", concept, "id"]
                },
                {
                    "type": "class_search",
                    "query": f"{concept.capitalize()}Service class methods",
                    "expected_patterns": [concept.capitalize()]
                }
            ]
            
            queries.append(random.choice(query_types))
        
        return queries
    
    def generate_diff_content(
        self,
        file_path: str,
        language: str = "python"
    ) -> str:
        """Generate mock diff content for a file."""
        if language == "python":
            return self._generate_python_diff(file_path)
        elif language == "typescript":
            return self._generate_typescript_diff(file_path)
        elif language == "java":
            return self._generate_java_diff(file_path)
        return ""
    
    def _generate_changed_files(
        self,
        concept: str,
        language: str,
        num_files: int
    ) -> List[str]:
        """Generate list of changed file paths."""
        patterns = self.FILE_PATTERNS.get(language, self.FILE_PATTERNS["python"])
        
        files = []
        for pattern in random.sample(patterns["paths"], min(num_files, len(patterns["paths"]))):
            file_path = pattern.format(
                name=concept,
                Name=concept.capitalize()
            )
            files.append(file_path)
        
        return files
    
    def _generate_diff_snippets(
        self,
        changed_files: List[str],
        concept: str,
        language: str
    ) -> List[str]:
        """Generate diff snippets for changed files."""
        snippets = []
        
        for file_path in changed_files[:2]:  # Limit to 2 snippets
            snippet = self.generate_diff_content(file_path, language)
            if snippet:
                snippets.append(snippet)
        
        return snippets
    
    def _generate_description(self, concept: str, changed_files: List[str]) -> str:
        """Generate PR description."""
        descriptions = [
            f"This PR adds new {concept} functionality.\n\n## Changes\n- Updated {concept} service\n- Added new methods\n- Fixed validation",
            f"Improvements to {concept} module.\n\n## Summary\nRefactored the {concept} implementation for better performance.",
            f"Bug fix for {concept} handling.\n\n## Issue\nFixed edge case in {concept} validation logic."
        ]
        
        return random.choice(descriptions)
    
    def _generate_python_diff(self, file_path: str) -> str:
        """Generate Python diff content."""
        return f"""@@ -10,6 +10,15 @@
 class Service:
     def __init__(self):
         self.initialized = True
+
+    def process_data(self, data: dict) -> dict:
+        \"\"\"Process incoming data.\"\"\"
+        result = {{}}
+        for key, value in data.items():
+            result[key] = self._transform(value)
+        return result
+
+    def _transform(self, value):
+        return str(value).strip()
"""
    
    def _generate_typescript_diff(self, file_path: str) -> str:
        """Generate TypeScript diff content."""
        return f"""@@ -15,4 +15,12 @@
 export class Service {{
   private initialized: boolean = false;
+
+  async processData(data: Record<string, any>): Promise<Record<string, any>> {{
+    const result: Record<string, any> = {{}};
+    for (const [key, value] of Object.entries(data)) {{
+      result[key] = this.transform(value);
+    }}
+    return result;
+  }}
 }}
"""
    
    def _generate_java_diff(self, file_path: str) -> str:
        """Generate Java diff content."""
        return f"""@@ -20,4 +20,12 @@
 public class Service {{
     private boolean initialized = false;
+
+    public Map<String, Object> processData(Map<String, Object> data) {{
+        Map<String, Object> result = new HashMap<>();
+        for (Map.Entry<String, Object> entry : data.entrySet()) {{
+            result.put(entry.getKey(), transform(entry.getValue()));
+        }}
+        return result;
+    }}
 }}
"""
    
    def save_scenario(self, scenario: Dict[str, Any], output_dir: Path, name: str):
        """Save scenario to JSON file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"{name}.json"
        
        with open(filepath, "w") as f:
            json.dump(scenario, f, indent=2)
        
        return filepath


def main():
    """CLI for mock data generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate mock test data")
    parser.add_argument("--type", choices=["pr", "scenario", "queries"], default="pr")
    parser.add_argument("--output", type=str, help="Output file/directory")
    parser.add_argument("--num-files", type=int, default=3, help="Number of changed files")
    parser.add_argument("--language", choices=["python", "typescript", "java"], default="python")
    parser.add_argument("--num-queries", type=int, default=10, help="Number of queries")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    args = parser.parse_args()
    
    generator = MockDataGenerator(seed=args.seed)
    
    if args.type == "pr":
        payload = generator.generate_pr_payload(
            num_files=args.num_files,
            language=args.language
        )
        print(json.dumps(payload, indent=2))
        
    elif args.type == "scenario":
        scenario = generator.generate_pr_scenario(
            name="generated_scenario",
            description="Auto-generated test scenario",
            num_files=args.num_files,
            language=args.language
        )
        
        if args.output:
            output_dir = Path(args.output)
            filepath = generator.save_scenario(scenario, output_dir, "generated")
            print(f"Saved to: {filepath}")
        else:
            print(json.dumps(scenario, indent=2))
            
    elif args.type == "queries":
        queries = generator.generate_search_queries(
            num_queries=args.num_queries,
            language=args.language
        )
        print(json.dumps(queries, indent=2))


if __name__ == "__main__":
    main()
