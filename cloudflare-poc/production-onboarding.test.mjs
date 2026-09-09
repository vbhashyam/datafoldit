import assert from 'node:assert/strict';
import {Miniflare} from 'miniflare';
import {initializeAuth,provisionUser,totp} from './auth.mjs';
import {initializeInvitations,bootstrapAdmin} from './invitations.mjs';
const origin='https://www.accounts.datafoldit.com';
for(const phase of ['onboarding','review']){
const runtime=new Miniflare({modules:true,scriptPath:new URL('./production-worker.mjs',import.meta.url).pathname,compatibilityDate:'2026-08-06',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],bindings:{DEPLOYMENT_ENV:'production',PUBLIC_ORIGIN:origin,RELEASE_PHASE:phase}});
try{
 const db=await runtime.getD1Database('DB');await initializeAuth(db);await initializeInvitations(db);
 const call=(path,init)=>runtime.dispatchFetch(origin+path,init);
 assert.equal((await call('/healthz')).status,200);
 assert.equal((await runtime.dispatchFetch('https://accounts.datafoldit.com/healthz')).status,403);
 assert.equal((await runtime.dispatchFetch('https://datafoldit-production.vamsibh07.workers.dev/healthz')).status,200);
 assert.match(await(await call('/login')).text(),/Production/);
 for(const path of ['/api/import','/api/transactions'])assert.equal((await call(path)).status,503);
 assert.equal((await call('/api/admin/invitations')).status,phase==='review'?401:503);
 assert.equal((await call('/api/records')).status,phase==='review'?401:503);
 assert.equal((await call('/api/records',{method:'POST'})).status,phase==='review'?403:503);
 const invitation=await bootstrapAdmin(db);
 const response=await call('/api/enrollment/begin',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({token:invitation.token})});
 const setup=await response.json();assert.equal(setup.mfaRequired,true);assert.match(setup.otpauth,/DataFoldIT%20Production/);assert.ok(setup.qr);
 const secret='3132333435363738393031323334353637383930',password='Synthetic-production-test-password';
 await provisionUser(db,'vamsi@datafoldit.com',password,'admin',secret);
 const login=await call('/api/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({email:'vamsi@datafoldit.com',password,code:await totp(secret,Math.floor(Date.now()/30000))})});
 assert.equal(login.status,200);
 const page=await call('/',{headers:{Cookie:login.headers.get('set-cookie').split(';')[0]}});const markup=await page.text();assert.match(markup,phase==='review'?/PRODUCTION REVIEW/:/administrator login is working/);
 if(phase==='review'){assert.match(markup,/id="role" value="read"/);assert.ok(!markup.includes('Use synthetic data only'));assert.equal((await call('/api/records',{method:'POST',headers:{Cookie:login.headers.get('set-cookie').split(';')[0]}})).status,403);}
 console.log('PASS: production onboarding, distinct MFA issuer, admin login, business API and employee invitation gates');
}finally{await runtime.dispose();}
}
