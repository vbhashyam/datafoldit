import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {recordApi,validate,dashboardPage,dashboardJs} from './dashboard.mjs';
const mf=new Miniflare({modules:true,compatibilityDate:'2026-08-06',d1Databases:['DB'],script:"export default {fetch(){return new Response('test')}}"});
try{
 const db=await mf.getD1Database('DB');await db.exec((await readFile(new URL('./migrations/0004_dashboard.sql',import.meta.url),'utf8')).replaceAll('\n',' '));
 const user={role:'write',email:'tester@example.invalid',csrf:'test'},req=new Request('https://test/api/records',{method:'POST',headers:{'X-CSRF-Token':'test'}});
 const body={action:'create',kind:'expenses',data:{date:'2026-09-08',amount:12,vendor:'Test'}};
 assert.equal((await recordApi(req,db,{...user,role:'read'},body)).status,403);
 assert.equal((await recordApi(new Request('https://test',{method:'POST'}),db,user,body)).status,403);
 const result=await (await recordApi(req,db,user,body)).json();assert.ok(result.id);
 let rows=await (await recordApi(new Request('https://test'),db,user)).json();assert.equal(rows[0].updated_by,user.email);
 assert.equal((await recordApi(req,db,user,{action:'update',id:result.id,version:1,data:{...body.data,amount:15}})).status,200);
 assert.equal((await recordApi(req,db,user,{action:'update',id:result.id,version:1,data:body.data})).status,409);
 assert.throws(()=>validate('expenses',{...body.data,amount:-1}));
 assert.equal(validate('invoices',{date:'2026-09-08',invoice_number:'T1',status:'Paid',amount:100,balance_due:100}).balance_due,0);
 assert.ok(dashboardPage(user).includes('#invoices'));new Function(dashboardJs);
 assert.equal((await recordApi(req,db,user,{action:'delete',id:result.id,version:2})).status,200);
 assert.equal((await db.prepare('SELECT count(*) n FROM dashboard_records').first()).n,0);
 console.log('PASS dashboard CRUD, permissions, CSRF, attribution, stale-update protection, validation, JS syntax');
}finally{await mf.dispose();}
