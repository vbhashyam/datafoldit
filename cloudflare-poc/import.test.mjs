import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {commitImport,downloadImport,checkFile,importRequest} from './bulk-import.mjs';
import {tableCandidates,textCandidates,prepareCandidates} from './import-parser.mjs';
import {fields} from './dashboard.mjs';
const matrix=[['date','invoice_number','customer','amount','balance_due','status'],...Array.from({length:10},(_,i)=>['2026-09-08','TEST-'+i,'Synthetic client',100,100,'Open'])];
const parsed=prepareCandidates(tableCandidates(matrix,'invoices',fields,'Sheet 1'),'invoices',fields);assert.equal(parsed.length,10);assert.equal(parsed[9].data.invoice_number,'TEST-9');
const text=Array.from({length:10},(_,i)=>`Invoice # INV-${100+i}\nInvoice Date : 08 Sep 2026\nBill To\nSynthetic Client\nTotal $100.00\nBalance Due $100.00`).join('\n');
assert.equal(textCandidates(text,'invoices',fields,'Document').length,10);
const mf=new Miniflare({modules:true,compatibilityDate:'2026-08-06',d1Databases:['DB'],script:"export default {fetch(){return new Response('test')}}"});
try{
 const db=await mf.getD1Database('DB');await db.exec('CREATE TABLE test_files(size INTEGER);');for(const file of ['0004_dashboard.sql','0005_import_attachments.sql'])await db.exec((await readFile('migrations/'+file,'utf8')).replaceAll('\n',' '));
 const user={id:'test',email:'tester@example.invalid',role:'write',csrf:'csrf'},bytes=new TextEncoder().encode('%PDF-1.7\nSynthetic fixture');
 const result=await commitImport(db,user,'invoices','sample.pdf',bytes,parsed);assert.equal(result.count,10);assert.equal((await db.prepare('SELECT COUNT(*) n FROM dashboard_records').first()).n,10);assert.equal((await db.prepare('SELECT COUNT(*) n FROM record_attachments').first()).n,10);assert.deepEqual((await downloadImport(db,result.fileId)).bytes,bytes);
 await assert.rejects(commitImport(db,user,'invoices','sample.pdf',bytes,parsed));assert.equal((await db.prepare('SELECT COUNT(*) n FROM dashboard_records').first()).n,10);
 const remaining=await commitImport(db,user,'invoices','sample.pdf',bytes,[{data:{...parsed[0].data,invoice_number:'TEST-REMAINING'},source:'Previously missed page'}]);assert.equal(remaining.count,1);assert.equal(remaining.fileId,result.fileId);assert.equal((await db.prepare('SELECT COUNT(*) n FROM import_files').first()).n,1);
 await assert.rejects(commitImport(db,{...user,role:'read'},'expenses','sample.pdf',bytes,[{data:{date:'2026-09-08',amount:1},source:'Page 1'}]));
 await assert.rejects(commitImport(db,user,'expenses','sample.pdf',bytes,[{data:{date:'2026-09-08',amount:1},source:'Page 1'},{data:{date:'',amount:2},source:'Page 2'}]));assert.equal((await db.prepare("SELECT COUNT(*) n FROM dashboard_records WHERE kind='expenses'").first()).n,0);
 assert.equal((await importRequest(new Request('https://test/api/import',{method:'POST'}),db,user)).status,403);
 assert.throws(()=>checkFile('x.pdf',new Uint8Array([1,2,3])));assert.throws(()=>checkFile('x.doc',bytes));
 const large=new Uint8Array(6*1024*1024);large.set(bytes);const attached=await commitImport(db,user,'invoices','larger.pdf',large,[],result.ids[0]);assert.deepEqual((await downloadImport(db,attached.fileId)).bytes,large);
 console.log('PASS: ten-document extraction, ten separate atomic records and links, >5 MB attachment, duplicate rejection, permissions, validation and source downloads');
}finally{await mf.dispose();}
