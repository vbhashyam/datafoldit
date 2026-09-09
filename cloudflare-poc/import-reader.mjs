import * as pdfjs from 'pdfjs-dist/build/pdf.mjs';
import ExcelJS from 'exceljs';
import {readWordTransactions} from './word-reader.mjs';
import {createWorker} from 'tesseract.js';
import JSZip from 'jszip';
import {tableCandidates,textCandidates,prepareCandidates} from './import-parser.mjs';
pdfjs.GlobalWorkerOptions.workerSrc='/static/import/pdf.worker.min.mjs';
export async function readTransactions(file,kind,fields,progress){
 if(file.size>10485760)throw Error('Maximum test file size is 10 MB.');
 const ext=file.name.toLowerCase().split('.').at(-1);let candidates=[],ocr;
 if(['docx','xlsx'].includes(ext)){const zip=await JSZip.loadAsync(await file.arrayBuffer());const entries=Object.values(zip.files);if(entries.length>10000||entries.reduce((n,f)=>n+(f._data?.uncompressedSize||0),0)>50*1024*1024)throw Error('Expanded document is too large. Split it into smaller files.');}
 async function recognize(image){if(!ocr)ocr=await createWorker('eng',1,{workerPath:'/static/import/worker.min.js',corePath:'/static/import/core',langPath:'/static/import/lang',workerBlobURL:false,logger:m=>progress('Reading image text: '+Math.round((m.progress||0)*100)+'%')});return (await ocr.recognize(image)).data.text;}
 try{
 if(ext==='xlsx'){
  const book=new ExcelJS.Workbook();await book.xlsx.load(await file.arrayBuffer());
  for(const sheet of book.worksheets){progress('Reading worksheet '+sheet.name);if(sheet.rowCount>5000||sheet.columnCount>100)throw Error('Worksheet too large. Split into smaller files.');const matrix=[];sheet.eachRow({includeEmpty:true},row=>matrix.push(row.values.slice(1).map(v=>v&&typeof v==='object'&&!(v instanceof Date)?v.result??v.text??v.richText?.map(t=>t.text).join('')??'':v)));const rows=tableCandidates(matrix,kind,fields,'Sheet '+sheet.name);if(rows)candidates.push(...rows);else candidates.push({data:{},source:'Sheet '+sheet.name,warning:'No recognized headers. Map this sheet manually or export with dashboard field names.'});}
 }else if(ext==='pdf'){
  const task=pdfjs.getDocument({data:new Uint8Array(await file.arrayBuffer()),isEvalSupported:false,standardFontDataUrl:'/static/import/standard_fonts/',wasmUrl:'/static/import/wasm/'}),pdf=await task.promise;
  try{if(pdf.numPages>100)throw Error('Maximum 100 pages per test import.');for(let n=1;n<=pdf.numPages;n++){progress('Reading PDF page '+n+' of '+pdf.numPages);const page=await pdf.getPage(n),content=await page.getTextContent();const lines=new Map();for(const item of content.items){if(!('str' in item))continue;const y=Math.round(item.transform[5]/3)*3;const line=lines.get(y)||[];line.push({x:item.transform[4],text:item.str});lines.set(y,line);}let text=[...lines].sort((a,b)=>b[0]-a[0]).map(([,items])=>items.sort((a,b)=>a.x-b.x).map(i=>i.text).join('  ')).join('\n');if(text.replace(/\W/g,'').length<30||(kind==='invoices'&&textCandidates(text,kind,fields,'Page '+n).some(r=>!r.data.invoice_number||!r.data.date||r.data.amount==null||r.data.amount===''))){const viewport=page.getViewport({scale:1.6});if(viewport.width*viewport.height>16000000)throw Error('PDF page too large for OCR.');const canvas=document.createElement('canvas');canvas.width=viewport.width;canvas.height=viewport.height;await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;text=await recognize(canvas);canvas.width=canvas.height=0;}candidates.push(...textCandidates(text,kind,fields,'Page '+n));page.cleanup();}}finally{await task.destroy();}
 }else if(ext==='docx'){
  candidates=await readWordTransactions(file,kind,fields,async image=>{const bitmap=await createImageBitmap(image);try{if(bitmap.width*bitmap.height>16000000)throw Error('Embedded image too large. Use images under 16 megapixels.');}finally{bitmap.close();}return recognize(image);},progress);
 }else if(['png','jpg','jpeg'].includes(ext)){const bitmap=await createImageBitmap(file);try{if(bitmap.width*bitmap.height>16000000)throw Error('Image too large. Use an image under 16 megapixels.');}finally{bitmap.close();}candidates=textCandidates(await recognize(file),kind,fields,'Image');}
 else throw Error('Use PDF, Word DOCX, PNG/JPG or Excel XLSX. Convert older DOC/XLS files first.');
 if(candidates.length>100)throw Error('More than 100 candidate transactions. Split the file; nothing was saved.');
 if(!candidates.length)throw Error('No readable transactions detected. Nothing was saved.');
 return prepareCandidates(candidates,kind,fields);
 }finally{if(ocr)await ocr.terminate();}
}
