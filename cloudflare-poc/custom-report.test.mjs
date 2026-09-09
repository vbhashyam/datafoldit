import assert from 'node:assert/strict';
import ExcelJS from 'exceljs';
import {exportWorkbook} from './export-ui.mjs';
import {fields} from './dashboard.mjs';
let blob;const original=URL.createObjectURL;URL.createObjectURL=b=>(blob=b,'blob:test');globalThis.document={createElement:()=>({click(){}})};
const payroll=(month,start,end)=>({kind:'payroll',data:{month,effective_start:start,effective_end:end,credit_date:'2026-09-01',gross:100},updated_by:'Synthetic'});
const rows=[...['2026-06-30','2026-07-01','2026-07-31','2026-08-01'].map(date=>({kind:'expenses',data:{date,amount:10},updated_by:'Synthetic'})),payroll('2026-09','2026-07-01','2026-07-31'),payroll('2026-07','',''),payroll('2026-07','2026-08-01','2026-08-31'),payroll('2026-06','2026-06-15','2026-07-01')];
try{
 await exportWorkbook(rows,fields,{period:'custom',from:'2026-07-01',to:'2026-07-31'});const book=new ExcelJS.Workbook();await book.xlsx.load(await blob.arrayBuffer());assert.equal(book.getWorksheet('Expenses').rowCount,3,'includes both date boundaries only');assert.equal(book.getWorksheet('Payroll').rowCount,4,'effective overlap and month fallback, not credit date');
 assert.ok(book.getWorksheet('Summary').getColumn(2).values.includes('2026-07-01 to 2026-07-31'));
 for(const range of [{from:'',to:'2026-07-31'},{from:'2026-08-01',to:'2026-07-31'},{from:'2026-02-30',to:'2026-07-31'}])await assert.rejects(exportWorkbook(rows,fields,{period:'custom',...range}),/From and To/);
 console.log('PASS: inclusive custom dates, effective payroll overlap, legacy month fallback, invalid dates rejected, summary date label');
}finally{URL.createObjectURL=original;delete globalThis.document;}
