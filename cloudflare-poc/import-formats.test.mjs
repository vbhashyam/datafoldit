import assert from 'node:assert/strict';
import ExcelJS from 'exceljs';
import JSZip from 'jszip';
import mammoth from 'mammoth';
import {createCanvas} from '@napi-rs/canvas';
import {createWorker} from 'tesseract.js';
import {tableCandidates,textCandidates} from './import-parser.mjs';
import {fields} from './dashboard.mjs';
// Entirely synthetic in-memory fixtures. No user documents are uploaded.
const book=new ExcelJS.Workbook(),sheet=book.addWorksheet('Invoices');sheet.addRow(['date','invoice_number','amount','customer']);for(let i=0;i<10;i++)sheet.addRow(['2026-09-08','TEST-'+i,100,'Synthetic']);
const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await book.xlsx.writeBuffer());const matrix=[];loaded.worksheets[0].eachRow(row=>matrix.push(row.values.slice(1)));assert.equal(tableCandidates(matrix,'invoices',fields,'Sheet').length,10);
const zip=new JSZip();zip.file('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');zip.file('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
zip.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+Array.from({length:10},(_,i)=>['Invoice # INV-'+(100+i),'Invoice Date : 08 Sep 2026','Total $100.00'].map(t=>'<w:p><w:r><w:t>'+t+'</w:t></w:r></w:p>').join('')).join('')+'</w:body></w:document>');const doc=await mammoth.extractRawText({buffer:await zip.generateAsync({type:'nodebuffer'})});assert.equal(textCandidates(doc.value,'invoices',fields,'Word').length,10);
const canvas=createCanvas(1400,2400),ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,1400,2400);ctx.fillStyle='black';ctx.font='36px sans-serif';for(let i=0;i<10;i++){ctx.fillText('Invoice # INV-'+(100+i),50,65+i*230);ctx.fillText('Invoice Date : 08 Sep 2026',50,115+i*230);ctx.fillText('Total $100.00',50,165+i*230);}
const worker=await createWorker('eng',1,{langPath:new URL('./public/static/import/lang',import.meta.url).pathname,cacheMethod:'none'});try{const result=await worker.recognize(canvas.toBuffer('image/png'));assert.equal(textCandidates(result.data.text,'invoices',fields,'PNG').length,10);}finally{await worker.terminate();}
console.log('PASS: synthetic XLSX, DOCX and PNG OCR fixtures each yield ten separate invoice candidates');
