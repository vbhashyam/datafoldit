import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {provisionUser,totp} from './auth.mjs';
const origin='https://www.accounts.datafoldit.com';
const runtime=new Miniflare({modules:true,scriptPath:new URL('./production-worker.mjs',import.meta.url).pathname,compatibilityDate:'2026-08-06',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],bindings:{DEPLOYMENT_ENV:'production',PUBLIC_ORIGIN:origin,RELEASE_PHASE:'live'}});
try{
 const db=await runtime.getD1Database('DB');for(const file of (await readdir('migrations')).filter(f=>f.endsWith('.sql')).sort())await db.exec((await readFile('migrations/'+file,'utf8')).replace(/^--.*$/gm,'').replaceAll('\n',' '));
 const call=(path,init={})=>runtime.dispatchFetch(origin+path,init);
 const post=(path,data,session={},requestOrigin=origin)=>call(path,{method:'POST',headers:{Origin:requestOrigin,'Content-Type':'application/json',Cookie:session.cookie||'','X-CSRF-Token':session.csrf||''},body:JSON.stringify(data)});
 const password='Synthetic-live-password',secret='3132333435363738393031323334353637383930',sessions={};
 for(const role of ['admin','read','write']){
  const email=role+'@example.test';await provisionUser(db,email,password,role,role==='admin'?secret:'');
  const response=await post('/api/login',{email,password,code:role==='admin'?await totp(secret,Math.floor(Date.now()/30000)):''});assert.equal(response.status,200);
  const cookie=response.headers.get('set-cookie').split(';')[0];const markup=await(await call('/',{headers:{Cookie:cookie}})).text();
  assert.match(markup,/Live production workspace/);assert.match(markup,new RegExp('id="role" value="'+role+'"'));assert.equal(markup.includes('Manage users'),role==='admin');sessions[role]={cookie,csrf:markup.match(/id="csrf" value="([^"]+)"/)[1]};
 }
 assert.equal((await(await call('/healthz')).json()).businessWritesEnabled,true);
 const create={action:'create',kind:'expenses',data:{date:'2026-09-09',amount:100}};
 assert.equal((await post('/api/records',create,sessions.read)).status,403);
 assert.equal((await post('/api/records',create,{...sessions.write,csrf:'bad'})).status,403);
 assert.equal((await post('/api/records',create,sessions.write,'https://evil.test')).status,403);
 const created=await post('/api/records',create,sessions.write);assert.equal(created.status,200);const id=(await created.json()).id;
 const record=await db.prepare('SELECT * FROM dashboard_records WHERE id=?').bind(id).first();assert.equal(record.tracking_id,'EXP-000001');assert.equal(record.updated_by,'write@example.test');
 assert.equal((await post('/api/records',{action:'update',id,version:1,data:{...create.data,amount:110,date:'2020-01-01'}},sessions.write)).status,200);
 assert.equal((await db.prepare('SELECT tracking_id FROM dashboard_records WHERE id=?').bind(id).first()).tracking_id,record.tracking_id);
 assert.equal((await post('/api/records',{action:'delete',id,version:1},sessions.write)).status,409);
 const invoice={action:'create',kind:'invoices',data:{date:'2026-09-09',invoice_number:'SYNTHETIC-1',amount:50,balance_due:50,status:'Open'}};
 assert.equal((await post('/api/records',invoice,sessions.admin)).status,200);assert.equal((await post('/api/records',invoice,sessions.write)).status,409);
 const form=new FormData();form.set('kind','expenses');form.set('targetId',id);form.set('rows','[]');form.set('file',new File(['%PDF-1.7 synthetic attachment'],'synthetic.pdf',{type:'application/pdf'}));
 const encoded=new Request(origin+'/api/import',{method:'POST',body:form});
 const upload=await call('/api/import',{method:'POST',headers:{Origin:origin,Cookie:sessions.write.cookie,'X-CSRF-Token':sessions.write.csrf,'Content-Type':encoded.headers.get('Content-Type')},body:await encoded.arrayBuffer()});assert.equal(upload.status,200,await upload.clone().text());const fileId=(await upload.json()).fileId;
 assert.equal((await call('/api/import-files/'+fileId)).status,401);const download=await call('/api/import-files/'+fileId,{headers:{Cookie:sessions.read.cookie}});assert.equal(await download.text(),'%PDF-1.7 synthetic attachment');
 assert.equal((await call('/api/operational-backup',{headers:{Cookie:sessions.read.cookie}})).status,403);const backup=await call('/api/operational-backup',{headers:{Cookie:sessions.admin.cookie}});assert.equal(backup.status,200);assert.match(await backup.text(),/dashboard_records/);
 assert.equal((await post('/api/records',{action:'delete',id,version:3},sessions.write)).status,200);
 console.log('PASS: live role-aware dashboard, reader denial, CSRF/origin checks, create/edit/delete, stable IDs, concurrency, invoice duplicates, attachment privacy and admin backup');
}finally{await runtime.dispose();}
