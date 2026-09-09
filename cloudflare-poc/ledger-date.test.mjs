import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
for(const kind of Object.keys(fields)){
 const rows=['Paid','Open','VOID'].map((status,i)=>({id:String(i),kind,updated_by:'Test',data:{date:'2026-09-0'+(i+1),month:'2026-09',effective_start:'2026-07-01',effective_end:'2026-07-31',credit_date:'2026-09-01',status,amount:100,commission_amount:30,balance_due:status==='Open'?100:0}}));
 const dom=new JSDOM(dashboardPage({email:'test@example.test',role:'read',csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),w=dom.window;
 w.scrollTo=()=>{};w.fetch=async p=>({ok:true,json:async()=>p==='/api/records'?rows:[]});
 try{w.eval(script);await new Promise(r=>setTimeout(r,25));const control=name=>w.document.querySelector('#page-filters [aria-label="'+name+'"]'),period=control('Period'),apply=()=>[...w.document.querySelectorAll('#page-filters button')].find(b=>b.textContent==='Apply').click(),count=()=>w.document.querySelector('.summary-cards').textContent;
 assert.ok(!count().includes('Period'));
 if(kind==='invoices'){assert.match(count(),/Received\$100.00/);assert.match(count(),/Commission received\$30.00/);}
 for(const mode of ['all','monthly','daily','custom']){period.value=mode;period.onchange();assert.equal(control('Month').disabled,mode!=='monthly');assert.equal(control('Day').disabled,mode!=='daily');assert.equal(control('From').disabled,mode!=='custom');assert.equal(control('To').disabled,mode!=='custom');}
 control('From').value='2026-07-15';control('To').value='2026-07-20';apply();assert.ok(count().includes((kind==='payroll'?3:0)+' matching'));
 control('From').value='2026-09-01';control('To').value='2026-09-02';apply();assert.ok(count().includes((kind==='payroll'?0:2)+' matching'));
 period.value='daily';period.onchange();control('Day').value='2026-09-01';apply();assert.ok(count().includes((kind==='payroll'?3:1)+' matching'));
 period.value='monthly';period.onchange();control('Month').value='2026-09';apply();assert.ok(count().includes('3 matching'));
 period.value='all';period.onchange();apply();assert.ok(count().includes('3 matching'));
 }finally{w.close();}
}
console.log('PASS: all ledger date modes, disabled controls, inclusive ranges, payroll effective dates, paid-only invoice cards');
