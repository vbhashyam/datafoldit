import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {recordApi} from './dashboard.mjs';
import {commitImport} from './bulk-import.mjs';
const mf=new Miniflare({modules:true,compatibilityDate:'2026-08-06',d1Databases:['DB'],script:"export default {fetch(){return new Response('test')}}"});
try{
 const db=await mf.getD1Database('DB');await db.exec('CREATE TABLE test_files(size INTEGER);');
 for(const file of ['0004_dashboard.sql','0005_import_attachments.sql','0006_unique_invoice_number.sql'])await db.exec((await readFile('migrations/'+file,'utf8')).replaceAll('\n',' '));
 const user={email:'synthetic@example.invalid',role:'write',csrf:'test'},data={date:'2026-09-09',invoice_number:'INV-100',customer:'A',amount:100,status:'Open',balance_due:100};
 const call=body=>recordApi(new Request('https://test/api/records',{method:'POST',headers:{'X-CSRF-Token':'test'}}),db,user,body);
 const first=await call({action:'create',kind:'invoices',data});assert.equal(first.status,200);const {id}=await first.json();
 for(const number of ['INV-100','inv-100',' INV-100 ']){const result=await call({action:'create',kind:'invoices',data:{...data,invoice_number:number,customer:'Different customer',status:'VOID'}});assert.equal(result.status,409);assert.match((await result.json()).error,/Duplicate invoice number/);}
 assert.equal((await call({action:'update',id,version:1,data:{...data,amount:200}})).status,200,'own number allowed');
 const second=await (await call({action:'create',kind:'invoices',data:{...data,invoice_number:'INV-101'}})).json();
 assert.equal((await call({action:'update',id:second.id,version:1,data})).status,409,'edit cannot collide');
 const bytes=new TextEncoder().encode('%PDF-1.7 synthetic');
 await assert.rejects(commitImport(db,user,'invoices','test.pdf',bytes,[{data:{...data,customer:'B'},source:'page 1'}]),/Duplicate invoice number/);
 await assert.rejects(commitImport(db,user,'invoices','test.pdf',bytes,[{data:{...data,invoice_number:'NEW-1'},source:'page 1'},{data:{...data,invoice_number:'new-1',customer:'B'},source:'page 2'}]),/Duplicate invoice/);
 const results=await Promise.all(['A','B'].map(customer=>call({action:'create',kind:'invoices',data:{...data,invoice_number:'RACE-1',customer}})));assert.deepEqual(results.map(r=>r.status).sort(),[200,409]);
 assert.equal((await db.prepare('SELECT COUNT(*) n FROM dashboard_records').first()).n,3);
 console.log('PASS: global invoice uniqueness on create, edit, bulk upload and concurrent saves; case/space variants and VOID duplicates rejected; self-edit permitted');
}finally{await mf.dispose();}
