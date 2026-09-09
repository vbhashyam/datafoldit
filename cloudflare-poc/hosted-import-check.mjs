// Test-only synthetic verification. Always remove the exact generated fixtures.
import {randomToken,passwordHash,digest} from './auth.mjs';
import {mkdtemp,writeFile,unlink,rmdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';import {execFileSync} from 'node:child_process';
import ExcelJS from 'exceljs';import assert from 'node:assert/strict';
const origin='https://datafoldit-test-poc.vamsibh07.workers.dev',id=crypto.randomUUID(),email='import-check-'+id+'@example.test',salt=randomToken(),password=randomToken(),hash=await passwordHash(password,salt);
async function sql(command){const dir=await mkdtemp(join(tmpdir(),'dfit-import-check-')),path=join(dir,'query.sql');try{await writeFile(path,command,{mode:0o600});execFileSync(process.execPath,['node_modules/wrangler/bin/wrangler.js','d1','execute','datafoldit-test-poc','--remote','--file',path],{stdio:'pipe',timeout:60000});}finally{await unlink(path).catch(()=>{});await rmdir(dir);}}
try{
 await sql(`INSERT INTO poc_users(id,email,role,salt,password_hash,mfa_hex) VALUES('${id}','${email}','write','${salt}','${hash}','');`);
 const login=await fetch(origin+'/api/login',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify({email,password,code:''})});assert.equal(login.status,200);const cookie=login.headers.get('set-cookie').split(';')[0],page=await(await fetch(origin+'/',{headers:{Cookie:cookie}})).text(),csrf=page.match(/id="csrf"[^>]*value="([^"]+)"/)[1];
 const book=new ExcelJS.Workbook(),sheet=book.addWorksheet('Test');sheet.addRow(['date','invoice_number','amount','customer']);const rows=Array.from({length:10},(_,i)=>({data:{date:'2026-09-08',invoice_number:'VERIFY-'+id+'-'+i,customer:'Synthetic verification',amount:100,status:'Open',balance_due:100},source:'Sheet Test row '+(i+2)}));rows.forEach(r=>sheet.addRow([r.data.date,r.data.invoice_number,100,r.data.customer]));const bytes=await book.xlsx.writeBuffer();
 const send=async()=>{const form=new FormData();form.append('kind','invoices');form.append('file',new Blob([bytes]),'synthetic-check.xlsx');form.append('rows',JSON.stringify(rows));return fetch(origin+'/api/import',{method:'POST',headers:{Origin:origin,Cookie:cookie,'X-CSRF-Token':csrf},body:form});};
 const response=await send(),result=await response.json();assert.equal(response.status,200,result.error);assert.equal(result.count,10);
 const records=await(await fetch(origin+'/api/records',{headers:{Cookie:cookie}})).json();assert.equal(records.filter(r=>r.updated_by===email).length,10);
 const links=await(await fetch(origin+'/api/attachment-links',{headers:{Cookie:cookie}})).json();assert.equal(links.filter(l=>l.id===result.fileId).length,10);
 const download=await fetch(origin+'/api/import-files/'+result.fileId,{headers:{Cookie:cookie}});assert.equal(download.status,200);assert.deepEqual(Buffer.from(await download.arrayBuffer()),Buffer.from(bytes));assert.equal((await send()).status,400);
 assert.equal((await fetch(origin+'/api/import-files/'+result.fileId)).status,401);
 await sql(`UPDATE poc_users SET role='read' WHERE id='${id}';`);assert.equal((await send()).status,403);
 for(const path of ['/static/import-ui.js','/static/import/pdf.worker.min.mjs','/static/import/worker.min.js','/static/import/core/tesseract-core-lstm.wasm.js','/static/import/lang/eng.traineddata.gz'])assert.equal((await fetch(origin+path,{method:'GET'})).status,200);
 console.log('PASS: hosted ten-record import, ten source links, exact attachment download, duplicate prevention, anonymous denial, read-only denial, and reader assets.');
}finally{
 await sql(`DELETE FROM record_attachments WHERE record_id IN(SELECT id FROM dashboard_records WHERE updated_by='${email}'); DELETE FROM dashboard_records WHERE updated_by='${email}'; DELETE FROM import_chunks WHERE file_id IN(SELECT id FROM import_files WHERE uploaded_by='${email}'); DELETE FROM import_files WHERE uploaded_by='${email}'; DELETE FROM poc_sessions WHERE user_id='${id}'; DELETE FROM poc_users WHERE id='${id}'; DELETE FROM poc_login_limits WHERE bucket='${await digest(email)}';`);
 console.log('Removed only the disposable verification account, ten synthetic records, and its attachment. Employee data preserved.');
}
