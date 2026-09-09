import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import ExcelJS from 'exceljs';
import {dashboardPage,dashboardJs,fields,validate} from './dashboard.mjs';
import {exportWorkbook} from './export-ui.mjs';
import {tableCandidates} from './import-parser.mjs';
const hidden=['pct','commission','tax_breakdown','job_start','job_end'];
const data={month:'2026-07',effective_start:'2026-07-01',effective_end:'2026-07-31',credit_date:'2026-09-01',paystub_sent:'N',first_name:'Synthetic',gross:1000,tax:100,commission:50,pct:5,employee_pay:850,tax_breakdown:'Preserved',job_start:'2025-01-01',job_end:'2027-01-01'};
const clean=validate('payroll',data);assert.equal(clean.effective_start,'2026-07-01');assert.equal(clean.credit_date,'2026-09-01');assert.equal(clean.commission,50);
for(const changes of [{effective_end:''},{effective_end:'2026-06-30'},{effective_start:'2026-02-30'}])assert.throws(()=>validate('payroll',{...data,...changes}),/effective|Effective/);
const mapped=tableCandidates([['month','effective date range','gross'],['2026-07','2026-07-01 to 2026-07-31',1000]],'payroll',fields,'Excel');assert.equal(mapped[0].data.effective_start,'2026-07-01');assert.equal(mapped[0].data.effective_end,'2026-07-31');
const record={id:'synthetic',kind:'payroll',tracking_id:'PAY-000001',version:1,updated_by:'Sample',data};let payload;
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'write',csrf:'test'}),{url:'https://test/#payroll',runScripts:'outside-only'}),w=dom.window;w.scrollTo=()=>{};w.fetch=async(path,options)=>{if(options?.method==='POST')payload=JSON.parse(options.body);return {ok:true,json:async()=>path==='/api/records'&&!options?[record]:[]};};
try{w.eval(script);await new Promise(r=>setTimeout(r,30));const headers=[...w.document.querySelectorAll('.sort-column')].map(b=>b.textContent.toLowerCase());for(const key of hidden)assert.ok(!headers.some(h=>h.startsWith(key.replaceAll('_',' '))));assert.equal(headers.filter(h=>h.startsWith('effective date range')).length,1);
 const cell=[...w.document.querySelectorAll('td')].find(td=>td.textContent==='2026-07-01 to 2026-07-31');assert.ok(cell);cell.click();cell.querySelector('[name=effective_end]').value='2026-07-30';await [...cell.querySelectorAll('button')].find(b=>b.textContent==='Apply').onclick();assert.equal(payload.data.effective_end,'2026-07-30');assert.equal(payload.data.credit_date,'2026-09-01');for(const key of hidden)assert.equal(payload.data[key],data[key]);
 w.document.querySelector('.ledger-heading button').click();const draft=w.document.querySelector('.invoice-draft');for(const key of hidden)assert.equal(draft.querySelector('[name="'+key+'"]'),null);assert.ok(draft.querySelector('[name=effective_start]'));assert.ok(draft.querySelector('[name=effective_end]'));assert.equal(draft.querySelector('[name=effective_start]').closest('td'),draft.querySelector('[name=effective_end]').closest('td'));
}finally{w.close();}
let blob;const original=URL.createObjectURL;URL.createObjectURL=b=>(blob=b,'blob:test');globalThis.document={createElement:()=>({click(){}})};
try{await exportWorkbook([record],fields,{tab:'payroll'});const book=new ExcelJS.Workbook();await book.xlsx.load(await blob.arrayBuffer());const sheet=book.getWorksheet('Payroll'),headers=sheet.getRow(1).values;for(const key of hidden)assert.ok(!headers.includes(key.replaceAll('_',' ')));assert.equal(sheet.getRow(2).getCell(headers.indexOf('effective date range')).value,'2026-07-01 to 2026-07-31');assert.equal(sheet.getRow(2).getCell(headers.indexOf('credit date')).value,'2026-09-01');}finally{URL.createObjectURL=original;delete globalThis.document;}
console.log('PASS: one editable payroll coverage range, separate credit date, removed columns, preserved legacy values, range validation, spreadsheet import/export and aligned draft');
