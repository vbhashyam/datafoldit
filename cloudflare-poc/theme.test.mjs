import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs} from './dashboard.mjs';
const origin='https://datafoldit-test-poc.vamsibh07.workers.dev';
const themeScript=process.env.HOSTED_CHECK==='1'?await(await fetch(origin+'/static/theme.js')).text():await readFile('public/static/theme.js','utf8');
for(const stored of [null,'light','dark','invalid','blocked']){
 const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'write',csrf:'test'}),{url:origin+'/#payroll',runScripts:'outside-only'}),w=dom.window;
 try{
  if(stored==='blocked')Object.defineProperty(w,'localStorage',{get(){throw Error('Storage blocked');}});else if(stored)w.localStorage.setItem('datafoldit-test-theme',stored);
  w.eval(themeScript);w.document.dispatchEvent(new w.Event('DOMContentLoaded'));const button=w.document.querySelector('#theme-toggle');const initial=stored==='light'?'light':'dark';assert.equal(w.document.documentElement.dataset.theme,initial);assert.ok(button.getAttribute('aria-label'));
  w.fetch=async()=>({ok:true,json:async()=>[]});w.scrollTo=()=>{};w.eval(dashboardJs);await new Promise(r=>setTimeout(r,20));w.document.querySelector('.ledger-heading button').click();const draft=w.document.querySelector('.invoice-draft');draft.querySelector('[name=first_name]').value='Unsaved example';
  button.click();const chosen=initial==='dark'?'light':'dark';assert.equal(w.document.documentElement.dataset.theme,chosen);assert.equal(draft.querySelector('[name=first_name]').value,'Unsaved example');assert.equal(w.document.querySelector('.invoice-draft'),draft,'theme switch does not redraw or discard edits');
  if(stored!=='blocked'){assert.equal(w.localStorage.getItem('datafoldit-test-theme'),chosen);w.eval(themeScript);assert.equal(w.document.documentElement.dataset.theme,chosen);}
  w.location.hash='#expenses';await new Promise(r=>setTimeout(r,20));assert.equal(w.document.documentElement.dataset.theme,chosen);assert.equal(w.document.querySelector('#message').textContent,'');
 }finally{w.close();}
}
const css=await readFile('public/static/theme.css','utf8');assert.ok(css.includes('color-scheme:light'));assert.ok(css.includes('--money-in:#146b49'));assert.ok(css.includes('--money-out:#b13b49'));
console.log('PASS: dark default, saved light/dark preference, blocked storage fallback, accessible toggle, tab persistence and unsaved edits preserved');
