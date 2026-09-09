import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {runInNewContext} from 'node:vm';
// Reproduce Wrangler's keepNames transform, then EXECUTE the emitted client
// calculations. Parsing alone cannot detect missing runtime helper bindings.
const bundle=await build({entryPoints:['financial-views.mjs'],bundle:true,format:'esm',platform:'browser',keepNames:true,write:false});
const {financialViewsJs}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const code=process.env.HOSTED_CHECK==='1'?await (await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():financialViewsJs;
// The full dashboard script initializes DOM at this marker; helpers precede it.
const helpers=code.split("let records=[],tab=")[0];
const result=runInNewContext(helpers+`\nJSON.stringify({summary:financialSummary([{kind:'bank',data:{type:'Opening',amount:50}}]),filtered:filterReport([],'bank'),groups:reportGroups([],'bank'),csv:makeCsv([{kind:'expenses',data:{amount:10},updated_by:'Sample'}],['amount'])})`);
const data=JSON.parse(result);assert.equal(data.summary.bank.balance,50);assert.deepEqual(data.filtered,[]);assert.deepEqual(data.groups,[]);assert.ok(data.csv.includes('10'));
console.log('PASS: '+(process.env.HOSTED_CHECK==='1'?'hosted':'bundled')+' dashboard calculations and report export execute without missing helpers');
