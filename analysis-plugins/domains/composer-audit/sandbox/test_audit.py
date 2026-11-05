import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from audit import audit, auth_config, changed_packages, findings_from_report, repositories


def package(name, version='1.0.0', reference=None):
    return {'name': name, 'version': version, 'source': {'reference': reference}}


def payload(before, after, **extra):
    return {'path': 'app/', 'baseManifest': '{}', 'headManifest': '{}',
            'baseLock': json.dumps({'packages': before}),
            'headLock': json.dumps({'packages': after}, indent=2), **extra}


def advisory(identifier='TEST-001', severity='critical'):
    return {'advisoryId': identifier, 'title': 'Test advisory', 'severity': severity,
            'affectedVersions': '<2.0.0', 'link': 'https://example.com/advisory'}


class AuditTest(unittest.TestCase):
    def run_audit(self, data, runner):
        with tempfile.TemporaryDirectory() as directory:
            result = audit(data, runner=runner, work_root=directory)
            self.assertEqual([], list(Path(directory).iterdir()))
            return result

    def test_only_added_and_changed_versions_references_and_manifest_requirements(self):
        base = {'packages': [package('a/same'), package('a/updated'), package('a/ref'), package('a/deleted')]}
        head = {'packages': [package('a/same'), package('a/updated','2.0.0'), package('a/ref',reference='new')],
                'packages-dev': [package('a/new'), package('a/constraint')]}
        selected, missing = changed_packages({'require': {'a/constraint': '^1'}},
             {'require': {'a/constraint': '^2', 'a/unlocked': '^1', 'php': '^8'}}, base, head, True)
        self.assertEqual({'a/updated','a/ref','a/new','a/constraint'}, set(selected))
        self.assertEqual({'a/unlocked'}, missing)

    def test_no_baseline_does_not_assume_existing_dependencies_are_new(self):
        selected, _ = changed_packages({'require': {'a/same':'^1'}},
              {'require': {'a/same':'^1','a/new':'^1'}}, {}, {'packages':[package('a/same'),package('a/new')]}, False)
        self.assertEqual({'a/new'}, set(selected))

    def test_manifest_only_change_audits_locked_version_and_omits_unchanged(self):
        calls = []
        def runner(directory):
            calls.append(1)
            written = json.loads((directory / 'composer.lock').read_text())
            self.assertEqual([{'name': 'a/change', 'version': '1.0.0'}], written['packages'])
            return 1, json.dumps({'advisories':{'a/change':[advisory()], 'a/same':[advisory()]}}).encode(), b''
        deps = [package('a/change'),package('a/same')]
        data = payload(deps, deps, baseManifest='{"require":{"a/change":"^1"}}',
                       headManifest='{"require":{"a/change":"^1.1"}}')
        result = self.run_audit(data,runner)
        self.assertEqual([1], calls)
        self.assertEqual(1,len(result['findings']))
        self.assertEqual('app/composer.lock', result['findings'][0]['file'])
        self.assertEqual('HIGH',result['findings'][0]['severity'])
        self.assertGreater(result['findings'][0]['line'],1)

    def test_safe_manifest_auth_and_unreachable_repositories_preserve_findings(self):
        def runner(directory):
            manifest = json.loads((directory/'composer.json').read_text())
            self.assertNotIn('scripts',manifest)
            self.assertNotIn('autoload',manifest)
            self.assertNotIn('ignore',json.dumps(manifest))
            self.assertEqual(False, manifest['config']['allow-plugins'])
            self.assertEqual({'http-basic': {'repo.example': {'username':'user','password':'secret'}}},
                             json.loads((directory/'auth.json').read_text()))
            return 1, json.dumps({'advisories':{'a/new':[advisory()]}, 'unreachable-repositories':['private']}).encode(), b'secret'
        data=payload([], [package('a/new')], auth='{"http-basic":{"repo.example":{"username":"user","password":"secret"}}}',
             headManifest='{"scripts":{"post-install-cmd":"touch /escape"},"autoload":{"files":["evil.php"]},"config":{"audit":{"ignore":["TEST-001"]}}}')
        result=self.run_audit(data,runner)
        self.assertEqual(1,len(result['findings']))
        self.assertNotIn('secret',json.dumps(result))
        self.assertTrue(any('unreachable' in d for d in result['diagnostics']))

    def test_explicit_packagist_disable_is_preserved(self):
        for repos in ({'packagist.org': False}, [{'packagist.org': False}]):
            self.assertEqual([{'packagist.org': False}], repositories({'repositories':repos}, []))

    def test_missing_invalid_and_valid_auth(self):
        for raw in (None,'bad','[]','{"http-basic":{"x":{"username":"only"}}}', '{"bearer":{"x":["bad"]}}'):
            diagnostics=[]
            self.assertIsNone(auth_config(raw,diagnostics))
            self.assertTrue(diagnostics)
        self.assertEqual({'bearer':{'repo.example':'token'}},auth_config('{"bearer":{"repo.example":"token"}}',[]))

    def test_composer_generated_bitbucket_auth_cache_is_valid(self):
        auth={'bitbucket-oauth':{'bitbucket.org':{'consumer-key':'key','consumer-secret':'secret',
              'access-token':'token','access-token-expiration':1799999999}}}
        self.assertEqual(auth,auth_config(json.dumps(auth),[]))

    def test_minified_lock_snippet_contains_only_package_name(self):
        raw='{"name":"a/new","dist":{"url":"https://secret@example.invalid"}}'
        finding=findings_from_report({'advisories':{'a/new':[advisory()]}},{'a/new':package('a/new')},'',raw)[0]
        self.assertEqual('"name":"a/new"',finding['codeSnippet'])
        self.assertNotIn('secret',json.dumps(finding))

    def test_repository_options_and_local_paths_cannot_enter_composer(self):
        diagnostics=[]
        entries=repositories({'repositories':[{'type':'path','url':'/app'},
             {'type':'composer','url':'http://localhost'}, {'type':'composer','url':'https://user:secret@example.com'},
             {'type':'composer','url':'https://repo.example','options':{'ssl':{'verify_peer':False}}}]},diagnostics)
        self.assertEqual([{'type':'composer','url':'https://user:secret@example.com'},
                          {'type':'composer','url':'https://repo.example'}],entries)
        self.assertEqual(2,len(diagnostics))
        self.assertNotIn('secret',json.dumps(diagnostics))

    def test_native_https_auth_forms_and_project_auth_reach_composer(self):
        urls=['https://fixture%40example.invalid:fixture%3Asecret@repo.example.invalid',
              'https?://repo.example.invalid/path']
        auth={'http-basic':{'repo.example.invalid':{'username':'fixture','password':'fixture-secret'}}}
        def runner(directory):
            manifest=json.loads((directory/'composer.json').read_text())
            self.assertEqual([{'type':'composer','url':urls[0]},
                              {'type':'composer','url':'https://repo.example.invalid/path'}],manifest['repositories'])
            self.assertEqual(auth,json.loads((directory/'auth.json').read_text()))
            return 1, json.dumps({'advisories':{'a/new':[advisory()]}}).encode(), b''
        result=self.run_audit(payload([], [package('a/new')], auth=json.dumps(auth),
            headManifest=json.dumps({'repositories':[{'type':'composer','url':url} for url in urls]})),runner)
        self.assertEqual(1,len(result['findings']))
        self.assertFalse(any('omitted' in item for item in result['diagnostics']))
        self.assertNotIn('fixture',json.dumps(result))

    def test_malformed_repository_is_omitted_without_losing_other_sources(self):
        diagnostics=[]
        bad=['https://fixture:secret@host:bad', 'https://[broken', 'http://host',
             'https://host:8443', 'host/path']
        entries=repositories({'repositories':[{'type':'composer','url':url} for url in bad]
                             +[{'type':'composer','url':'https://good.example'}]},diagnostics)
        self.assertEqual([{'type':'composer','url':'https://good.example'}],entries)
        self.assertEqual(len(bad),len(diagnostics))
        self.assertIn('entry 1: malformed',diagnostics[0])
        self.assertIn('HTTPS is required',diagnostics[2])
        self.assertIn('port 443 only',diagnostics[3])
        self.assertNotIn('secret',json.dumps(diagnostics))

    def test_non_advisory_repositories_have_one_informational_diagnostic(self):
        diagnostics=[]
        entries=repositories({'repositories':[{'type':kind,'url':'secret'}
                             for kind in ('vcs','git','path','package','artifact')]},diagnostics)
        self.assertEqual([],entries)
        self.assertEqual(1,len(diagnostics))
        self.assertIn('repository-non-advisory: 5',diagnostics[0])
        self.assertIn('changed locked packages are still checked',diagnostics[0])
        self.assertNotIn('secret',json.dumps(diagnostics))

    def test_no_changes_and_missing_lock_do_not_invoke_cli(self):
        def forbidden(_): self.fail('CLI must not run')
        for data in (payload([package('a/same')],[package('a/same')]), payload([],[],headLock=None)):
            result=self.run_audit(data,forbidden)
            self.assertEqual([],result['findings'])
            self.assertTrue(result['diagnostics'])

    def test_malformed_base_never_becomes_all_new(self):
        def forbidden(_): self.fail('CLI must not run')
        result=self.run_audit(payload([], [package('a/new')],baseLock='invalid'),forbidden)
        self.assertEqual([],result['findings'])
        self.assertTrue(any('failed' in d for d in result['diagnostics']))

    def test_timeout_non_json_and_cli_failure_have_no_retry_and_clean_inputs(self):
        for failure in ('timeout','invalid','failed'):
            calls=[]
            def runner(_):
                calls.append(1)
                if failure=='timeout': raise subprocess.TimeoutExpired('php',75)
                return (255 if failure=='failed' else 0), b'secret invalid output', b'secret'
            result=self.run_audit(payload([], [package('a/new')]),runner)
            self.assertEqual([1],calls)
            self.assertEqual([],result['findings'])
            self.assertNotIn('secret',json.dumps(result))

    def test_malware_is_security_but_unrelated_policy_and_abandonment_are_not(self):
        report={'advisories':[], 'abandoned': {'a/new':None}, 'filter': {'a/new':[
            {'listName':'malware','id':'malware-id','reason':'malicious package','constraint':'*'},
            {'listName':'license','id':'policy-only','reason':'license policy','constraint':'*'}]}}
        findings=findings_from_report(report,{'a/new':package('a/new')},'', '{"name":"a/new"}')
        self.assertEqual(1,len(findings))
        self.assertEqual('HIGH',findings[0]['severity'])
        self.assertIn('malware-id',findings[0]['title'])

    def test_sparse_advisories_deduplicate_and_ignore_old_packages(self):
        report={'advisories':{'a/new':{'0':advisory(),'2':advisory()}, 'a/old':[advisory()]}}
        self.assertEqual(1,len(findings_from_report(report,{'a/new':package('a/new')},'', '{"name":"a/new"}')))


if __name__=='__main__': unittest.main()
