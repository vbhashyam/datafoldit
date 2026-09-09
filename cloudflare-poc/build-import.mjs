import {build} from 'esbuild';
import {mkdir,copyFile,readdir} from 'node:fs/promises';
await mkdir('public/static/import/core',{recursive:true});await mkdir('public/static/import/lang',{recursive:true});
await build({entryPoints:['import-ui.mjs'],bundle:true,platform:'browser',format:'esm',minify:true,outfile:'public/static/import-ui.js'});
await build({entryPoints:['export-ui.mjs'],bundle:true,platform:'browser',format:'esm',minify:true,outfile:'public/static/export-ui.js'});
await copyFile('node_modules/pdfjs-dist/build/pdf.worker.min.mjs','public/static/import/pdf.worker.min.mjs');
await copyFile('node_modules/tesseract.js/dist/worker.min.js','public/static/import/worker.min.js');
for(const file of await readdir('node_modules/tesseract.js-core'))if(file.endsWith('.wasm.js'))await copyFile('node_modules/tesseract.js-core/'+file,'public/static/import/core/'+file);
for(const folder of ['standard_fonts','wasm']){await mkdir('public/static/import/'+folder,{recursive:true});for(const file of await readdir('node_modules/pdfjs-dist/'+folder))await copyFile('node_modules/pdfjs-dist/'+folder+'/'+file,'public/static/import/'+folder+'/'+file);}
