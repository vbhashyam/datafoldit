import assert from 'node:assert/strict';
import {Miniflare} from 'miniflare';
import {initializeAuth,provisionUser,totp} from './auth.mjs';
import {initializeInvitations} from './invitations.mjs';
import {pendingSchema} from './mfa-settings.mjs';
const origin='https://www.accounts.datafoldit.com',mail=[];
const runtime=new Miniflare({modules:true,scriptPath:new URL('./production-worker.mjs',import.meta.url).pathname,compatibilityDate:'2026-08-06',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],bindings:{DEPLOYMENT_ENV:'production',PUBLIC_ORIGIN:origin,RELEASE_PHASE:'review'},serviceBindings:{MAILER:async request=>{mail.push(await request.json());return new Response('accepted');}}});
try{
 const db=await runtime.getD1Database('DB');await initializeAuth(db);await initializeInvitations(db);await db.prepare(pendingSchema).run();
 await db.prepare('CREATE TABLE dashboard_records(id TEXT,kind TEXT,data TEXT,updated_by TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)').run();
 await db.prepare('INSERT INTO dashboard_records(id,kind,data,updated_by) VALUES(?,?,?,?)').bind('synthetic','expenses',JSON.stringify({amount:100,date:'2026-09-01'}),'Synthetic').run();
 const call=(path,init={})=>runtime.dispatchFetch(origin+path,init);
 const post=(path,data,cookie='',csrf='',requestOrigin=origin)=>call(path,{method:'POST',headers:{Origin:requestOrigin,'Content-Type':'application/json',Cookie:cookie,'X-CSRF-Token':csrf},body:JSON.stringify(data)});
 const password='Synthetic-review-password',secret='3132333435363738393031323334353637383930';
 await provisionUser(db,'vamsi@datafoldit.com',password,'admin',secret);
 const login=await post('/api/login',{email:'vamsi@datafoldit.com',password,code:await totp(secret,Math.floor(Date.now()/30000))});assert.equal(login.status,200);
 const adminCookie=login.headers.get('set-cookie').split(';')[0];
 const root=await(await call('/',{headers:{Cookie:adminCookie}})).text();assert.match(root,/>Manage users</);assert.match(root,/id="role" value="read"/);
 const adminPage=await(await call('/admin',{headers:{Cookie:adminCookie}})).text();assert.match(adminPage,/Read-only review/);
 const csrf=adminPage.match(/id="csrf"[^>]*value="([^"]+)"/)[1];
 assert.equal((await post('/api/admin/invitations',{email:'blocked@example.test',role:'read'},adminCookie,'wrong')).status,403);
 assert.equal((await post('/api/admin/invitations',{email:'blocked@example.test',role:'read'},adminCookie,csrf,'https://evil.test')).status,403);
 for(const role of ['read','write']){
  const email=role+'@example.test';
  assert.equal((await post('/api/admin/invitations',{email,role},adminCookie,csrf)).status,201);
  const url=new URL(mail.at(-1).link);assert.equal(url.origin,origin);const token=new URLSearchParams(url.hash.slice(1)).get('token');
  const setup=await(await post('/api/enrollment/begin',{token})).json();assert.equal(setup.mfaRequired,false);
  assert.equal((await post('/api/enrollment/accept',{token,password,skipMfa:true})).status,200);
  const result=await post('/api/login',{email,password});assert.equal(result.status,200);const cookie=result.headers.get('set-cookie').split(';')[0];
  const page=await(await call('/',{headers:{Cookie:cookie}})).text();assert.match(page,/PRODUCTION REVIEW/);assert.ok(!page.includes('Manage users'));assert.match(page,/id="role" value="read"/);
  assert.equal((await call('/api/records',{headers:{Cookie:cookie}})).status,200);
  for(const path of ['/admin','/api/admin/users'])assert.equal((await call(path,{headers:{Cookie:cookie}})).status,403);
  assert.equal((await post('/api/admin/invitations',{email:'other@example.test',role:'read'},cookie,csrf)).status,403);
  assert.equal((await post('/api/records',{action:'delete',id:'synthetic'},cookie,csrf)).status,403);
  assert.equal((await post('/api/import',{},cookie,csrf)).status,503);
  const user=await db.prepare('SELECT id FROM poc_users WHERE email=?').bind(email).first();
  assert.equal((await post('/api/admin/user-access',{id:user.id,action:'update',role,active:false},adminCookie,csrf)).status,200);
  assert.equal((await call('/api/records',{headers:{Cookie:cookie}})).status,401);
 }
 assert.equal((await db.prepare('SELECT count(*) AS n FROM dashboard_records').first()).n,1);
 console.log('PASS: admin invites, production email links, employee enrollment/login, review access, admin-only management, CSRF/origin checks, reader/writer mutation blocking and revocation. No real email sent.');
}finally{await runtime.dispose();}
