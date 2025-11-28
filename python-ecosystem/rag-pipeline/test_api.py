#!/usr/bin/env python3
"""Quick test script for RAG Pipeline API"""
import requests
import json
import sys

BASE_URL = "http://localhost:8001"

def test_health():
    print("🔍 Testing health endpoint...")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"   Status: {r.status_code}")
        print(f"   Response: {r.json()}")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"
        print("   ✅ Health check passed\n")
        return True
    except Exception as e:
        print(f"   ❌ Health check failed: {e}\n")
        return False

def test_root():
    print("🔍 Testing root endpoint...")
    try:
        r = requests.get(f"{BASE_URL}/", timeout=5)
        print(f"   Status: {r.status_code}")
        print(f"   Response: {r.json()}")
        assert r.status_code == 200
        print("   ✅ Root endpoint passed\n")
        return True
    except Exception as e:
        print(f"   ❌ Root endpoint failed: {e}\n")
        return False

def test_list_indices():
    print("🔍 Testing list indices...")
    try:
        r = requests.get(f"{BASE_URL}/index/list", timeout=5)
        print(f"   Status: {r.status_code}")
        data = r.json()
        print(f"   Response: {json.dumps(data, indent=2)}")
        assert r.status_code == 200
        print(f"   📊 Found {len(data)} indices")
        print("   ✅ List indices passed\n")
        return True
    except Exception as e:
        print(f"   ❌ List indices failed: {e}\n")
        return False

def test_search_empty():
    print("🔍 Testing semantic search (may be empty)...")
    try:
        payload = {
            "query": "test query",
            "workspace": "test",
            "project": "test",
            "branch": "main",
            "top_k": 5
        }
        r = requests.post(f"{BASE_URL}/query/search", json=payload, timeout=10)
        print(f"   Status: {r.status_code}")
        data = r.json()
        print(f"   Response: {json.dumps(data, indent=2)}")
        print("   ✅ Search endpoint responded\n")
        return True
    except Exception as e:
        print(f"   ❌ Search failed: {e}\n")
        return False

def test_openapi():
    print("🔍 Testing OpenAPI schema...")
    try:
        r = requests.get(f"{BASE_URL}/openapi.json", timeout=5)
        print(f"   Status: {r.status_code}")
        data = r.json()
        paths = list(data.get("paths", {}).keys())
        print(f"   📋 Available endpoints: {len(paths)}")
        for path in sorted(paths):
            print(f"      - {path}")
        print("   ✅ OpenAPI schema retrieved\n")
        return True
    except Exception as e:
        print(f"   ❌ OpenAPI schema failed: {e}\n")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("RAG Pipeline API Test Suite")
    print("=" * 60)
    print()

    tests = [
        ("Health Check", test_health),
        ("Root Endpoint", test_root),
        ("List Indices", test_list_indices),
        ("Semantic Search", test_search_empty),
        ("OpenAPI Schema", test_openapi),
    ]

    results = []
    for name, test_func in tests:
        results.append(test_func())

    print("=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print(f"❌ {total - passed} test(s) failed")
        sys.exit(1)

