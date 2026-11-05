"""Composer-owned input selection and isolated CLI adapter; no package installation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import tempfile
from urllib.parse import urlsplit

PACKAGE = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
MAX_OUTPUT = 2 * 1024 * 1024
PHAR = "/opt/composer/composer.phar"


def document(raw, *, optional=False):
    if raw is None and optional:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def packages(lock):
    result = {}
    for group in ("packages", "packages-dev"):
        entries = lock.get(group, [])
        if not isinstance(entries, list):
            raise ValueError("Invalid lock packages")
        for package in entries:
            if (not isinstance(package, dict) or not isinstance(package.get("name"), str)
                    or not PACKAGE.fullmatch(package["name"])
                    or not isinstance(package.get("version"), str)):
                raise ValueError("Invalid locked package")
            if package["name"] in result:
                raise ValueError("Ambiguous locked package")
            result[package["name"]] = package
    return result


def requirements(manifest):
    result = {}
    for group in ("require", "require-dev"):
        entries = manifest.get(group, {})
        if not isinstance(entries, dict) or any(not isinstance(v, str) for v in entries.values()):
            raise ValueError("Invalid requirements")
        for name, version in entries.items():
            if PACKAGE.fullmatch(name):
                result.setdefault(name, {})[group] = version
    return result


def identity(package):
    return (package.get("version"), (package.get("source") or {}).get("reference"),
            (package.get("dist") or {}).get("reference"))


def changed_packages(base_manifest, head_manifest, base_lock, head_lock, has_base_lock):
    before, after = packages(base_lock), packages(head_lock)
    old_require, new_require = requirements(base_manifest), requirements(head_manifest)
    changed = {name for name in new_require if new_require[name] != old_require.get(name)}
    if has_base_lock or not base_manifest:
        changed.update(name for name, package in after.items()
                       if name not in before or identity(package) != identity(before[name]))
    return {name: after[name] for name in sorted(changed & after.keys())}, changed - after.keys()


def repositories(manifest, diagnostics):
    entries = manifest.get("repositories", [])
    if isinstance(entries, dict):
        entries = [{name: entry} if name in ("packagist", "packagist.org") and entry is False else entry
                   for name, entry in entries.items()]
    if not isinstance(entries, list):
        raise ValueError("Invalid repositories")
    result = []
    for entry in entries:
        if entry in ({"packagist": False}, {"packagist.org": False}):
            result.append(entry)
            continue
        if not isinstance(entry, dict) or entry.get("type") != "composer":
            diagnostics.append("repository-omitted: only HTTPS Composer advisory repositories are supported")
            continue
        url = entry.get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (not parsed or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            diagnostics.append("repository-omitted: repository URL is not a credential-free HTTPS endpoint")
            continue
        result.append({"type": "composer", "url": url})
    return result


def auth_config(raw, diagnostics):
    if raw is None:
        diagnostics.append("auth-missing: auditing without private repository credentials")
        return None
    try:
        auth = document(raw)
        token_types = {"bearer", "github-oauth", "gitlab-oauth"}
        object_types = {"http-basic": {"username", "password"},
                        "bitbucket-oauth": {"consumer-key", "consumer-secret"},
                        "forgejo-token": {"username", "token"}}
        for kind, hosts in auth.items():
            if kind not in token_types | object_types.keys() | {"gitlab-token", "custom-headers"}:
                raise ValueError("Unsupported authentication")
            if not isinstance(hosts, dict):
                raise ValueError("Invalid hosts")
            for host, credential in hosts.items():
                if not re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", host):
                    raise ValueError("Invalid credential host")
                if kind in token_types:
                    valid = isinstance(credential, str) and bool(credential)
                elif kind in object_types:
                    valid = (isinstance(credential, dict) and object_types[kind].issubset(credential)
                             and all(isinstance(credential[key], str) for key in object_types[kind])
                             and all(isinstance(value, int) if key == "access-token-expiration" else isinstance(value, str)
                                     for key, value in credential.items()))
                elif kind == "gitlab-token":
                    valid = ((isinstance(credential, str) and bool(credential)) or
                             (isinstance(credential, dict) and set(credential) == {"username", "token"}
                              and all(isinstance(v, str) for v in credential.values())))
                else:
                    valid = (isinstance(credential, list) and all(isinstance(v, str) and ":" in v
                             and "\r" not in v and "\n" not in v for v in credential))
                if not valid:
                    raise ValueError("Invalid credential")
        return auth
    except (ValueError, TypeError):
        diagnostics.append("auth-invalid: ignoring malformed auth.json and continuing without credentials")
        return None


def cli_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def run_composer(directory):
    argv = ["php", "-d", "memory_limit=256M", "-d", "phar.readonly=1",
            "-d", f"open_basedir={directory}:{PHAR}:/etc/ssl/certs:/dev/null",
            "-d", "disable_functions=exec,system,passthru,shell_exec,proc_open,popen,pcntl_exec,mail,dl",
            PHAR, "audit", "--locked", "--format=json", "--no-interaction", "--no-plugins", "--no-scripts",
            "--ignore-unreachable", "--abandoned=ignore", "--no-ansi"]
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory),
                   "COMPOSER_HOME": str(directory / "home"), "COMPOSER_CACHE_DIR": str(directory / "cache"),
                   "COMPOSER_ROOT_VERSION": "1.0.0", "COMPOSER_NO_INTERACTION": "1",
                   "COMPOSER_PROCESS_TIMEOUT": "60", "TMPDIR": str(directory)}
    # File-backed output is bounded by RLIMIT_FSIZE, including a malicious remote response.
    with (directory / "stdout").open("w+b") as stdout, (directory / "stderr").open("w+b") as stderr:
        process = subprocess.Popen(argv, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True,
                                   close_fds=True, preexec_fn=cli_limits)
        try:
            process.wait(timeout=75)
        finally:
            # Also reap descendants if a compromised dependency ever bypasses --no-plugins.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        stdout.seek(0)
        stderr.seek(0)
        return process.returncode, stdout.read(MAX_OUTPUT), stderr.read(MAX_OUTPUT)


def findings_from_report(report, selected, path, raw_lock):
    advisories = report.get("advisories")
    if not isinstance(advisories, (dict, list)) or (isinstance(advisories, list) and advisories):
        raise ValueError("Invalid advisory response")
    advisories = dict(advisories) if isinstance(advisories, dict) else {}
    for name, entries in (report.get("filter") or {}).items():
        if name not in selected:
            continue
        for entry in entries:
            if entry.get("listName") == "malware":
                existing = advisories.get(name, [])
                existing = list(existing.values()) if isinstance(existing, dict) else list(existing)
                existing.append({"advisoryId": entry.get("id") or "Composer malware report",
                                 "title": "Reported as malware: " + str(entry.get("reason") or "listed by repository"),
                                 "severity": "high", "affectedVersions": entry.get("constraint"),
                                 "link": entry.get("url")})
                advisories[name] = existing
    findings, seen = [], set()
    for name, entries in (advisories.items() if isinstance(advisories, dict) else []):
        if name not in selected:
            continue
        if isinstance(entries, dict):
            entries = entries.values()
        elif not isinstance(entries, list):
            raise ValueError("Invalid package advisories")
        for advisory in entries:
            identifier = advisory.get("advisoryId") or advisory.get("cve")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Missing advisory identity")
            if (name, identifier) in seen:
                continue
            seen.add((name, identifier))
            title = str(advisory.get("title") or "Known dependency vulnerability")[:500]
            severity = str(advisory.get("severity") or "medium").lower()
            severity = {"critical": "HIGH", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}.get(severity, "MEDIUM")
            package_line = re.search(r'"name"\s*:\s*' + re.escape(json.dumps(name)), raw_lock)
            line = raw_lock.count("\n", 0, package_line.start()) + 1 if package_line else 1
            snippet = package_line.group(0) if package_line else ""
            version = selected[name]["version"]
            reason = f"Composer audit: {name} {version} is affected by {identifier}. {title}"
            affected = advisory.get("affectedVersions")
            if isinstance(affected, str):
                reason += f" Affected versions: {affected[:1000]}."
            link = advisory.get("link")
            if isinstance(link, str) and urlsplit(link).scheme == "https":
                reason += f" Advisory: {link[:2000]}"
            findings.append({"file": path + "composer.lock", "line": line, "severity": severity,
                             "title": f"{name}: {identifier}", "reason": reason,
                             "suggestedFix": "Update this dependency to a release outside the advisory's affected range and regenerate composer.lock.",
                             "codeSnippet": snippet})
    return findings


def audit(payload, runner=run_composer, work_root="/work"):
    diagnostics = []
    path = payload.get("path", "")
    if not isinstance(path, str) or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("Invalid Composer root")
    if payload.get("headManifest") is None or payload.get("headLock") is None:
        return {"findings": [], "diagnostics": ["inputs-missing: audit requires composer.json and composer.lock at the PR head"]}
    try:
        head_manifest = document(payload["headManifest"])
        head_lock = document(payload["headLock"])
        base_manifest = document(payload.get("baseManifest"), optional=True)
        base_lock = document(payload.get("baseLock"), optional=True)
        selected, unresolved = changed_packages(base_manifest, head_manifest, base_lock, head_lock,
                                               payload.get("baseLock") is not None)
        if unresolved:
            diagnostics.append("unlocked-dependencies: changed manifest requirements missing from the lockfile were not audited")
        if payload.get("baseLock") is None and base_manifest:
            diagnostics.append("baseline-unlocked: only changed direct requirements can be compared without a base lockfile")
        if not selected:
            return {"findings": [], "diagnostics": diagnostics + ["no-changed-dependencies: no changed locked packages to audit"]}
        repos = repositories(head_manifest, diagnostics)
        if repos and all(entry in ({"packagist": False}, {"packagist.org": False}) for entry in repos):
            diagnostics.append("advisory-sources-empty: Packagist is disabled and no supported advisory repository remains")
        auth = auth_config(payload.get("auth"), diagnostics)
        manifest = {"name": "codecrow/dependency-audit", "repositories": repos,
                    "config": {"allow-plugins": False, "secure-http": True}}
        lock = {"content-hash": "", "packages": [{"name": name, "version": package["version"]}
                for name, package in selected.items()], "packages-dev": []}
        with tempfile.TemporaryDirectory(prefix="audit-", dir=work_root) as temporary:
            directory = Path(temporary)
            (directory / "composer.json").write_text(json.dumps(manifest))
            (directory / "composer.lock").write_text(json.dumps(lock))
            if auth is not None:
                (directory / "auth.json").write_text(json.dumps(auth))
                (directory / "auth.json").chmod(0o600)
            status, stdout, _stderr = runner(directory)
            report = json.loads(stdout)
            if status not in (0, 1) or not isinstance(report, dict):
                raise ValueError("Composer audit failed")
            findings = findings_from_report(report, selected, path, payload["headLock"])
            if report.get("unreachable-repositories"):
                diagnostics.append("repositories-unreachable: advisory coverage is incomplete; available findings are retained")
            diagnostics.append(f"audited: {len(selected)} changed locked dependencies; {len(findings)} security advisories")
            return {"findings": findings, "diagnostics": diagnostics}
    except subprocess.TimeoutExpired:
        return {"findings": [], "diagnostics": diagnostics + ["audit-timeout: Composer exceeded its time limit"]}
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return {"findings": [], "diagnostics": diagnostics + ["audit-failed: invalid inputs or unavailable audit output; core review continues"]}
