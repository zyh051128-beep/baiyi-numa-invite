"""Actual ephemeral Mac CLI installation of the complete authenticated package."""
import hashlib, json, os, pathlib, platform, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode=True
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'ci-reports'
OUT.mkdir(exist_ok=True)
report={'ok':False,'system':platform.system(),'machine':platform.machine(),'macos':platform.mac_ver()[0],
        'gui_permissions_tested':False,'paid_services_called':False,'full_installations':[]}

def run(command,env=None,accepted=(0,)):
    result=subprocess.run(command,cwd=ROOT,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=600)
    if result.returncode not in accepted:
        # Installers deliberately suppress secrets. Never emit the inherited environment.
        print(result.stdout[-6000:]); print(result.stderr[-6000:],file=sys.stderr)
        raise RuntimeError('Validation subprocess failed with exit '+str(result.returncode)+': '+pathlib.Path(command[0]).name)
    return result

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

try:
    assert platform.system()=='Darwin'
    code=os.environ.pop('BAIYI_INVITE_CODE','')
    if not code: raise RuntimeError('Invitation CI secret missing')
    env=os.environ.copy()
    env['BAIYI_PYTHON']=sys.executable
    env['PYTHONDONTWRITEBYTECODE']='1'
    codex=shutil.which('codex')
    if not codex: raise RuntimeError('Official Codex CLI missing')
    report['codex_version']=run([codex,'--version'],env).stdout.strip()
    profile=pathlib.Path.home()/'.codex'
    owner_before={name:digest(profile/name) for name in ('config.toml','auth.json')}
    base=pathlib.Path(tempfile.mkdtemp(prefix='白衣怒马 Mac 完整验证 ')).resolve()
    home=base/'隔离 Codex 用户'
    secret=base/'invite-code.txt'
    secret.write_text(code,encoding='utf-8'); secret.chmod(0o600)
    del code
    try:
        for label in ('首次 安装','再次 更新'):
            install_root=base/label
            result=run(['/bin/sh',str(ROOT/'install.sh'),'--invite-code-file',str(secret),
                        '--crypto-backend','openssl','--install-root',str(install_root),
                        '--codex-home',str(home),'--codex-path',codex],env)
            receipt=json.loads((install_root/'installation-state.json').read_text())
            assert all(receipt.get(x) is True for x in ('plugin_installed','extraction_verified','cache_verified'))
            cache=pathlib.Path(receipt['installed_cache_path'])
            release=json.loads((install_root/'recipient-manifest.json').read_text())
            expected={item['path'][len('plugins/nuphus/'):]:item for item in release['files'] if item['path'].startswith('plugins/nuphus/')}
            actual={p.relative_to(cache).as_posix():p for p in cache.rglob('*') if p.is_file()}
            assert set(expected)==set(actual),'Independent file inventory differs'
            for name,item in expected.items():
                assert actual[name].stat().st_size==item['size'] and digest(actual[name])==item['sha256'],'Independent cache hash differs'
            skills=list((cache/'skills').glob('*/SKILL.md')); assert len(skills)==129
            execs=[p for p in release['executables'] if p.startswith('plugins/nuphus/')]
            assert all(os.access(cache/p[len('plugins/nuphus/'):],os.X_OK) for p in execs)
            assert json.loads((cache/'.mcp.json').read_text())['mcpServers']['白衣怒马']['command']=='/bin/sh'
            report['full_installations'].append({'label':label,'exit_code':result.returncode,'plugin_files':len(actual),
                'skills':len(skills),'executables_checked':len(execs),'independent_cache_verified':True,'runtime':receipt['runtime']})
        # Only public test code and this synthetic image enter the public repository.
        if not receipt['runtime']['coreRuntimeUnavailable']:
            result=run([sys.executable,'-B',str(cache/'scripts/verify_mcp_macos.py'),'--plugin-root',str(cache),
                '--skip-screen-check','--ocr-test-image',str(ROOT/'ci/ocr-synthetic-test.png'),'--timeout','90'],env)
            mcp=json.loads(result.stdout)
            assert mcp['ok'] and mcp['tool_count']==45
            # Never publish private file paths or raw image contents as CI artifacts.
            report['mcp']={k:v for k,v in mcp.items() if k not in ('plugin_root','launch_command')}
        else:
            raise RuntimeError('The final macOS release must include a working runtime for both architectures')
        fixture=ROOT/'ci/test_macos_installer.py'
        if fixture.exists(): run([sys.executable,'-B',str(fixture),'--require-openssl','-v'],env)
        run([sys.executable,'-B','ci/test_macos_verifier.py','--scripts',str(cache/'scripts'),'-v'],env)
        report['fixture_tests_passed']=True
        assert owner_before=={name:digest(profile/name) for name in owner_before},'Default Codex profile changed'
        report['default_profile_unchanged']=True
        report['ok']=True
    finally:
        secret.unlink(missing_ok=True)
except Exception as exc:
    report['error']=str(exc)
finally:
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['ok'] else 1)
