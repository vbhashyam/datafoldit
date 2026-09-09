// Creates one disposable synthetic account in the explicitly isolated test DB.
// Credentials never leave memory except a mode-0600 temporary SQL file removed
// immediately after use. Never point this script at production.
import {randomToken,passwordHash,totp,digest} from './auth.mjs';
import {mkdtemp,writeFile,unlink,rmdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
const origin='https://datafoldit-test-poc.vamsibh07.workers.dev';
const id=crypto.randomUUID(),email='synthetic-'+id+'@example.test';
const secret=randomToken().slice(0,40),password=randomToken(),salt=randomToken();
const hash=await passwordHash(password,salt);
async function sql(command){
  const dir=await mkdtemp(join(tmpdir(),'dfit-hosted-auth-')),path=join(dir,'query.sql');
  try{await writeFile(path,command,{mode:0o600});execFileSync(process.execPath,['node_modules/wrangler/bin/wrangler.js','d1','execute','datafoldit-test-poc','--remote','--file',path],{stdio:'pipe',timeout:60000});}
  finally{await unlink(path).catch(()=>{});await rmdir(dir);}
}
try{
  await sql(`INSERT INTO poc_users(id,email,role,salt,password_hash,mfa_hex) VALUES ('${id}','${email}','read','${salt}','${hash}','${secret}');`);
  const response=await fetch(origin+'/api/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({email,password,code:await totp(secret,Math.floor(Date.now()/30000))})});
  if(response.status!==200){const body=await response.json();console.log('Hosted login failure category:',body.category||'authentication_rejected');}
  assert.equal(response.status,200,'Hosted password + MFA login must succeed');
  const cookie=response.headers.get('set-cookie')?.split(';')[0];assert.ok(cookie);
  const page=await fetch(origin+'/',{headers:{Cookie:cookie}});assert.equal(page.status,200);
  const pageText=await page.text();assert.ok(pageText.includes('#invoices'));
  const csrf=pageText.match(/id="csrf"[^>]*value="([^"]+)"/)?.[1];assert.ok(csrf);
  assert.equal((await fetch(origin+'/api/records',{headers:{Cookie:cookie}})).status,200);
  assert.equal((await fetch(origin+'/static/dashboard.css')).status,200);
  assert.equal((await fetch(origin+'/api/transactions',{headers:{Cookie:cookie}})).status,200);
  assert.equal((await fetch(origin+'/api/transactions',{method:'POST',headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({description:'Must not save',amount:'1'})})).status,403);
  await sql(`DELETE FROM poc_sessions WHERE user_id='${id}'; UPDATE poc_users SET mfa_hex='',last_step=-1 WHERE id='${id}';`);
  const optional=await fetch(origin+'/api/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({email,password,code:''})});
  assert.equal(optional.status,200,'Employee without MFA may sign in with a password');
  await sql(`DELETE FROM poc_sessions WHERE user_id='${id}'; UPDATE poc_users SET role='admin' WHERE id='${id}';`);
  const admin=await fetch(origin+'/api/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({email,password,code:''})});
  assert.equal(admin.status,401,'Admin without enrolled MFA must never sign in');
  console.log('PASS: hosted password + MFA login, authenticated read, and read-only write rejection. No business data created.');
  console.log('PASS: hosted optional employee MFA and mandatory admin MFA enforcement.');
}finally{
  await sql(`DELETE FROM poc_sessions WHERE user_id='${id}'; DELETE FROM poc_users WHERE id='${id}'; DELETE FROM poc_login_limits WHERE bucket='${await digest(email)}';`);
  console.log('Disposable account and session removed.');
}
