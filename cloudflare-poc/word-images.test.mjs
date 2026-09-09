import assert from 'node:assert/strict';
import JSZip from 'jszip';
import {JSDOM} from 'jsdom';
import {createCanvas} from '@napi-rs/canvas';
import {createWorker} from 'tesseract.js';
import {readWordTransactions} from './word-reader.mjs';
import {fields} from './dashboard.mjs';
globalThis.DOMParser=new JSDOM('').window.DOMParser;
const zip=new JSZip();
zip.file('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>');
zip.file('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>');
let body='',rels='';
for(let i=0;i<10;i++){
 const canvas=createCanvas(1100,350),ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,1100,350);ctx.fillStyle='black';ctx.font='36px sans-serif';
 ['Invoice # INV-'+(200+i),'Invoice Date : 08 Sep 2026','Total $100.00'].forEach((t,n)=>ctx.fillText(t,40,65+n*75));
 zip.file('word/media/image'+i+'.png',canvas.toBuffer('image/png'));
 rels+='<Relationship Id="img'+i+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image'+i+'.png"/>';
 body+='<w:p><w:r><w:drawing><wp:inline><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill><a:blip r:embed="img'+i+'"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>';
}
zip.file('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels+'</Relationships>');
zip.file('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'+body+'</w:body></w:document>');
const file=new File([await zip.generateAsync({type:'uint8array'})],'ten-images.docx');
const worker=await createWorker('eng',1,{langPath:new URL('./public/static/import/lang',import.meta.url).pathname,cacheMethod:'none'});
try{
 const rows=await readWordTransactions(file,'invoices',fields,async blob=>(await worker.recognize(Buffer.from(await blob.arrayBuffer()))).data.text,()=>{});
 assert.equal(rows.length,10);assert.equal(new Set(rows.map(r=>r.data.invoice_number)).size,10);assert.ok(rows.every(r=>r.data.amount===100&&r.data.date==='2026-09-08'));
 console.log('PASS: actual DOCX with ten embedded PNG invoices yields ten distinct complete entries through the production Word reader and OCR');
}finally{await worker.terminate();}
