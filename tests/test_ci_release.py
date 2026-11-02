import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.ci import artifacts as a
from scripts.ci.github import GitHub
from scripts.ci.release import publish

SHA='a'*40


class GitHubDouble:
    def __init__(self):
        self.current=None
        self.files={}
        self.uploads=[]
        self.patches=[]
        self.latest=None
        self.fail_upload=None
        self.fail_api=False
        self.commit=SHA

    def tag_sha(self,tag):
        return self.commit

    def release(self,tag):
        if self.fail_api:
            raise RuntimeError('synthetic API failure')
        result=copy.deepcopy(self.current)
        if result is not None:
            result['assets']=[{'id':name,'name':name} for name in self.files]
        return result

    def repo(self,path,method='GET',data=None,missing=False):
        if self.fail_api:
            raise RuntimeError('synthetic API failure')
        if path=='releases/generate-notes':
            return {'body':'### Changes\n- Synthetic release notes'}
        if path=='releases' and method=='POST':
            self.current={'id':1,**data}
            return self.release(data['tag_name'])
        if path=='releases/latest':
            return self.latest
        if path=='releases/1' and method=='PATCH':
            self.patches.append(copy.deepcopy(data))
            self.current.update(data)
            return self.release(self.current['tag_name'])
        raise AssertionError((path,method))

    def asset(self,asset_id):
        return self.files[asset_id]

    def upload(self,tag,path):
        if path.name==self.fail_upload:
            raise RuntimeError('synthetic interrupted upload')
        self.uploads.append(path.name)
        self.files[path.name]=path.read_bytes()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def assets(self,version='1.9.0'):
        binaries,digests=self.root/'binaries',self.root/'digests'
        binaries.mkdir();digests.mkdir()
        for system,(name,_) in a.BINARIES.items():
            (binaries/name).write_bytes(('binary:'+system).encode())
            a.binary_metadata(binaries,system,SHA,version,check_host=False)
        for variant in a.PLATFORMS:
            a.write_json(digests/f'digest-{variant}.json',a.digest_metadata(variant,'sha256:'+'b'*64,SHA,version))
        target=self.root/'assets'
        a.prepare(binaries,digests,target,SHA,version,'refs/tags/v'+version,'https://github.com/run/1')
        return target

    def test_partial_upload_resume_and_published_noop(self):
        target=self.assets();github=GitHubDouble()
        github.fail_upload='SHA256SUMS'
        with self.assertRaises(RuntimeError):
            publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertTrue(github.current['draft']);self.assertEqual(github.patches,[])
        github.fail_upload=None
        published=publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertFalse(published['draft'])
        self.assertEqual(github.patches[-1]['make_latest'],'true')
        uploads,patches=list(github.uploads),list(github.patches)
        publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertEqual(github.uploads,uploads);self.assertEqual(github.patches,patches)
        github.files['SHA256SUMS']=b'corrupt'
        with self.assertRaises(ValueError):
            publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertEqual(github.uploads,uploads)

    def test_prerelease_never_latest(self):
        target=self.assets('1.10.0-rc.1');github=GitHubDouble()
        publish(target,'v1.10.0-rc.1',SHA,'1.10.0-rc.1',github,lambda _:None)
        self.assertEqual(github.patches[-1],{'draft':False,'prerelease':True,'make_latest':'false'})

    def test_old_stable_never_downgrades_latest(self):
        target=self.assets();github=GitHubDouble()
        github.latest={'tag_name':'v1.10.0','draft':False,'prerelease':False}
        publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertEqual(github.patches[-1]['make_latest'],'false')

    def test_wrong_tag_sha_or_api_failure_prevents_writes(self):
        target=self.assets()
        for fail in ('sha','api'):
            github=GitHubDouble()
            if fail=='sha':github.commit='c'*40
            else:github.fail_api=True
            with self.subTest(fail=fail),self.assertRaises((ValueError,RuntimeError)):
                publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
            self.assertEqual(github.uploads,[]);self.assertIsNone(github.current)

    def test_unrelated_draft_and_mixed_sha_rejected(self):
        target=self.assets();github=GitHubDouble();github.fail_upload='SHA256SUMS'
        with self.assertRaises(RuntimeError):publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        github.files['build-info.json']=json.dumps({'sha':'c'*40,'version':'1.9.0'}).encode()
        with self.assertRaisesRegex(ValueError,'SHA/version conflict'):
            publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        github.current['target_commitish']='c'*40
        with self.assertRaisesRegex(ValueError,'unrelated'):
            publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertEqual(github.patches,[])

    def test_corrupt_readback_leaves_draft(self):
        target=self.assets();github=GitHubDouble()
        original=github.asset
        github.asset=lambda asset_id:b'corrupt' if asset_id=='SHA256SUMS' else original(asset_id)
        with self.assertRaises(ValueError):
            publish(target,'v1.9.0',SHA,'1.9.0',github,lambda _:None)
        self.assertTrue(github.current['draft']);self.assertEqual(github.patches,[])

    def test_adapter_finds_drafts_and_resolves_annotated_tag(self):
        github=GitHub()
        draft={'tag_name':'v1.9.0','draft':True}
        with patch.object(github,'repo',side_effect=[None,[draft]]):
            self.assertEqual(github.release('v1.9.0'),draft)
        with patch.object(github,'repo',side_effect=[{'object':{'type':'tag','sha':'b'*40}}, {'object':{'type':'commit','sha':SHA}}]):
            self.assertEqual(github.tag_sha('v1.9.0'),SHA)
