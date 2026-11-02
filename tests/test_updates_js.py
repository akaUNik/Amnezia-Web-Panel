from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js required')
class UpdateJavaScriptTests(unittest.TestCase):
    def node(self, source):
        result = subprocess.run(['node', '-e', source, str(ROOT/'static/js/updates.js')],
                                capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_semantic_versions(self):
        self.node(r'''
const assert=require('assert/strict');
const {compareVersions:c}=require(process.argv[1]);
assert.equal(c('1.9.0','1.10.0'),-1);
assert.equal(c('v1.9.0+build.1','1.9.0+build.2'),0);
assert.equal(c('1.9.0-rc.1','1.9.0'),-1);
assert.equal(c('1.9.0-rc.2','1.9.0-rc.11'),-1);
assert.equal(c('1.9.0-alpha.1','1.9.0-alpha.beta'),-1);
assert.equal(c('999999999999999999999.0.0','999999999999999999998.0.0'),1);
for (const v of ['vnext','1.2','01.2.3','1.2.3-01','1.2.3-','1.2.3+','1.2.3\n',null]) {
    assert.equal(c(v,'1.2.3'),null);
}
''')

    def test_release_outcomes_and_button_recovery(self):
        self.node(r'''
const assert=require('assert/strict');
const api=require(process.argv[1]);
const source={api_url:'https://api.github.com/repos/akaUNik/Amnezia-Web-Panel/releases/latest',releases_url:'https://github.com/akaUNik/Amnezia-Web-Panel/releases'};
const body=(tag='v1.10.0')=>({tag_name:tag,draft:false,prerelease:false,html_url:'https://evil.invalid'});
const response=(status,data)=>({status,ok:status===200,json:async()=>data});
const messages=Object.fromEntries(['available','no_update','no_release','rate_limit','timeout','uncomparable','error','check','checking'].map(k=>[k,k]));
function elements() {
    return Object.fromEntries(['checkUpdateBtn','checkUpdateBtnText','checkUpdateSpinner','updateStatus','downloadUpdateBtn'].map(id=>[id,{
        disabled:false,style:{},dataset:{},textContent:'',href:'',
        set innerHTML(_) {throw new Error('HTML interpolation forbidden');},
        classList:{values:new Set(['hidden']),remove(k){this.values.delete(k);},add(k){this.values.add(k);}}
    }]));
}
(async()=>{
const cases=[
 [200,body(),'v1.9.0','available'],[200,body('1.9.0+build'),'v1.9.0','no_update'],
 [200,body('v1.8.4'),'1.9.0','no_update'],[200,body('1.9.0'),'1.9.0-rc.1','available'],
 [404,null,'1.9.0','no_release'],[403,null,'1.9.0','rate_limit'],[429,null,'1.9.0','rate_limit'],
 [500,null,'1.9.0','error'],[503,null,'1.9.0','error'],[200,{},'1.9.0','error'],
 [200,null,'1.9.0','error'],[200,{...body(),draft:true},'1.9.0','error'],
 [200,{...body(),prerelease:true},'1.9.0','error'],[200,body('1.10.0-rc.1'),'1.9.0','error'],
 [200,body('<img src=x onerror=alert(1)>'),'1.9.0','uncomparable'],
 [200,body(),'development','uncomparable']
];
for(const [status,data,current,state] of cases) {
    const nodes=elements();
    const fetchImpl=async(url,opts)=>{
        assert.equal(url,source.api_url);assert.equal(opts.credentials,'omit');
        assert.equal(opts.headers.Authorization,undefined);
        assert.equal(nodes.checkUpdateBtn.disabled,true);
        return response(status,data);
    };
    const result=await api.check({document:{getElementById:id=>nodes[id]},source,current,messages,fetchImpl});
    assert.equal(result.state,state); assert.equal(nodes.updateStatus.dataset.state,state);
    assert.equal(nodes.checkUpdateBtn.disabled,false);
    assert.equal(nodes.checkUpdateSpinner.classList.values.has('hidden'),true);
    assert.equal(nodes.downloadUpdateBtn.href,source.releases_url);
    assert.ok(nodes.updateStatus.textContent.startsWith(messages[state]));
}
for (const fetchImpl of [async()=>{throw new Error('network');},async()=>({status:200,ok:true,json:async()=>{throw new Error('malformed JSON');}})]) {
    assert.equal((await api.lookupRelease(source,'1.9.0',fetchImpl)).state,'error');
}
let signal;
const nodes=elements();
assert.equal((await api.check({document:{getElementById:id=>nodes[id]},source,current:'1.9.0',messages,timeoutMs:1,
    fetchImpl:async(_,opts)=>{signal=opts.signal;return new Promise(()=>{});}})).state,'timeout');
assert.equal(signal.aborted,true);assert.equal(nodes.checkUpdateBtn.disabled,false);
assert.equal((await api.lookupRelease(source,'1.9.0',async()=>({status:200,ok:true,json:()=>new Promise(()=>{})}),1)).state,'timeout');
})().catch(e=>{console.error(e);process.exitCode=1;});
''')
