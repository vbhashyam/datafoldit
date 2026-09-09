import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import ExcelJS from 'exceljs';
import {dashboardPage,dashboardJs,fields} from './dashboard.mjs';
import {exportWorkbook} from './export-ui.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'write',csrf:'test'}),{url:'https://test/#reports',runScripts:'outside-only'}),w=dom.window;
w.fetch=async()=>({ok:true,json:async()=>[]});w.scrollTo=()=>{};
try{
 w.eval(script);await new Promise(r=>setTimeout(r,20));const q=name=>w.document.querySelector('[name="'+name+'"]'),period=q('period'),month=q('month'),day=q('day');
 assert.equal(w.document.querySelector('.report-filters'),null);assert.equal(w.document.querySelectorAll('.excel-reports').length,1);
 assert.deepEqual([...q('report_tab').options].map(o=>o.value),['all','bank','expenses','payroll','invoices']);
 for(const [value,m,d]of [['all',true,true],['monthly',false,true],['daily',true,false],['custom',true,true],['all',true,true]]){period.value=value;period.dispatchEvent(new w.Event('change'));assert.equal(month.disabled,m);assert.equal(day.disabled,d);assert.equal(q('from').disabled,value!=='custom');assert.equal(q('to').disabled,value!=='custom');}
 assert.equal(w.document.querySelector('#message').textContent,'');
}finally{w.close();}
let blob;const original=URL.createObjectURL;URL.createObjectURL=b=>(blob=b,'blob:test');globalThis.document={createElement:()=>({click(){}})};
const rows=Object.keys(fields).flatMap(kind=>['2026-09-09','2026-09-10','2026-08-09'].map((date,i)=>({kind,tracking_id:'TEST-'+i,data:{date:kind==='payroll'?undefined:date,month:date.slice(0,7),amount:10,gross:20,status:'Open'},updated_by:'Sample'})));
try{
 for(const tab of ['all',...Object.keys(fields)])for(const period of ['all','monthly','daily','custom']){
  await exportWorkbook(rows,fields,{tab,period,month:'2026-09',day:'2026-09-09',from:'2026-09-09',to:'2026-09-09'});const book=new ExcelJS.Workbook();await book.xlsx.load(await blob.arrayBuffer());
  assert.equal(book.worksheets.length,tab==='all'?5:2);for(const sheet of book.worksheets.slice(1)){const expected=period==='all'?3:period==='monthly'||sheet.name==='Payroll'?2:1;assert.equal(sheet.rowCount,expected+1);}
 }
 console.log('PASS: one reports panel, tab choices, all/monthly/daily disabled states, and all 20 tab-period workbook combinations');
}finally{URL.createObjectURL=original;delete globalThis.document;}
