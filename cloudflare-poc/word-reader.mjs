import mammoth from 'mammoth/mammoth.browser.js';
import {tableCandidates,textCandidates} from './import-parser.mjs';

export async function readWordTransactions(file,kind,fields,recognize,progress){
 const candidates=[],images=[];
 const result=await mammoth.convertToHtml({arrayBuffer:await file.arrayBuffer()},{convertImage:mammoth.images.imgElement(async image=>{
  if(images.length>=100)throw Error('Maximum 100 embedded images.');
  images.push(new Blob([await image.readAsArrayBuffer()],{type:image.contentType}));return {src:''};
 })});
 const doc=new DOMParser().parseFromString(result.value,'text/html');
 for(const [i,table] of [...doc.querySelectorAll('table')].entries()){
  const rows=tableCandidates([...table.rows].map(row=>[...row.cells].map(c=>c.textContent)),kind,fields,'Word table '+(i+1));
  if(rows){candidates.push(...rows);table.remove();}
 }
 const text=[...doc.querySelectorAll('p')].map(p=>p.textContent).join('\n');
 if(text.trim())candidates.push(...textCandidates(text,kind,fields,'Word text'));
 for(const [i,image] of images.entries()){
  progress('Reading Word image '+(i+1)+' of '+images.length);
  candidates.push(...textCandidates(await recognize(image),kind,fields,'Word image '+(i+1)));
 }
 return candidates;
}
