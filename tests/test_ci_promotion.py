import copy
import unittest

from scripts.ci.artifacts import PLATFORMS, digest_metadata
from scripts.ci.promote import IMAGE, promote

SHA='a'*40


class RegistryDouble:
    def __init__(self,version='1.9.0'):
        self.digests={v:digest_metadata(v,'sha256:'+c*64,SHA,version)
                      for v,c in (('default','b'),('warp','c'))}
        self.entries={IMAGE+'@'+d['digest']:{k:d[k] for k in ('digest','sha','version','platforms')}
                      for d in self.digests.values()}
        self.writes=[]
        self.fail=None

    def inspect(self,ref):
        return copy.deepcopy(self.entries.get(ref))

    def promote(self,tag,digest):
        if tag==self.fail:
            raise RuntimeError('synthetic registry interruption')
        self.writes.append(tag)
        self.entries[IMAGE+':'+tag]=copy.deepcopy(self.entries[IMAGE+'@'+digest])


class PromotionTests(unittest.TestCase):
    def run_promotion(self,registry,ref='refs/tags/v1.9.0',head=SHA,emit=lambda message:None):
        version=registry.digests['default']['version']
        return promote(registry.digests,version,SHA,ref,head,registry,emit)

    def test_main_and_stale_main(self):
        current=RegistryDouble()
        self.run_promotion(current,ref='refs/heads/main')
        self.assertEqual(set(current.writes),{'sha-'+SHA,'sha-'+SHA+'-warp','main','main-warp','latest','latest-warp'})
        stale=RegistryDouble()
        self.run_promotion(stale,ref='refs/heads/main',head='d'*40)
        self.assertEqual(set(stale.writes),{'sha-'+SHA,'sha-'+SHA+'-warp'})

    def test_stable_and_prerelease(self):
        registry=RegistryDouble()
        self.run_promotion(registry)
        self.assertEqual(set(registry.writes),{'1.9.0','1.9.0-warp','1','1-warp','1.9','1.9-warp','latest','latest-warp'})
        pre=RegistryDouble('1.10.0-rc.1')
        self.run_promotion(pre,ref='refs/tags/v1.10.0-rc.1')
        self.assertEqual(pre.writes,['1.10.0-rc.1','1.10.0-rc.1-warp'])

    def test_old_release_preserves_newer_aliases(self):
        registry=RegistryDouble()
        for tag in ('1','1-warp','latest','latest-warp'):
            registry.entries[IMAGE+':'+tag]={'digest':'sha256:'+'f'*64,'version':'1.10.0','sha':'d'*40,'platforms':['linux/amd64']}
        self.run_promotion(registry)
        self.assertEqual(set(registry.writes),{'1.9.0','1.9.0-warp','1.9','1.9-warp'})

    def test_incomplete_or_wrong_image_does_not_promote(self):
        for corruption in ('missing','sha','platform'):
            registry=RegistryDouble()
            if corruption=='missing':
                registry.digests.pop('warp')
            else:
                image=registry.entries[IMAGE+'@'+registry.digests['warp']['digest']]
                image['sha' if corruption=='sha' else 'platforms']='bad'
            with self.subTest(corruption=corruption),self.assertRaises(ValueError):
                self.run_promotion(registry)
            self.assertEqual(registry.writes,[])

    def test_immutable_conflict_preflights_before_writes(self):
        registry=RegistryDouble()
        registry.entries[IMAGE+':1.9.0-warp']={'digest':'sha256:'+'f'*64}
        with self.assertRaisesRegex(ValueError,'Immutable'):
            self.run_promotion(registry)
        self.assertEqual(registry.writes,[])

    def test_partial_promotion_summary_and_retry(self):
        registry=RegistryDouble()
        registry.fail='latest-warp'
        messages=[]
        with self.assertRaises(RuntimeError):
            self.run_promotion(registry,emit=messages.append)
        self.assertTrue(any('`latest-warp`: `absent`' in message for message in messages))
        registry.fail=None
        self.run_promotion(registry)
        self.assertEqual(registry.writes[-1],'latest-warp')
