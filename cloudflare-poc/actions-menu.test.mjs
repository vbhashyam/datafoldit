import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {dashboardPage,dashboardJs} from './dashboard.mjs';
const script=process.env.HOSTED_CHECK==='1'?await(await fetch('https://datafoldit-test-poc.vamsibh07.workers.dev/dashboard.js')).text():dashboardJs;
for(const kind of ['bank','expenses','payroll','invoices']){
 const dom=new JSDOM(dashboardPage({email:'sample@example.test',role:'write',csrf:'test'}),{url:'https://test/#'+kind,runScripts:'outside-only'}),w=dom.window;let writes=0,confirmed=false;
 w.scrollTo=()=>{};w.confirm=()=>{confirmed=true;return false;};w.fetch=async(path,options)=>{if(options?.method==='POST')writes++;return {ok:true,json:async()=>path==='/api/records'?['a','b'].map(id=>({id,kind,data:{date:'2026-09-09',month:'2026-09',amount:100},updated_by:'sample@example.test'})):[]};};
 try{w.eval(script);await new Promise(r=>setTimeout(r,30));const menus=[...w.document.querySelectorAll('.transaction-actions')];assert.equal(menus.length,2,w.document.querySelector('#message').textContent);
 const triggers=menus.map(m=>m.querySelector('.actions-trigger')),options=menus.map(m=>m.querySelector('.transaction-action-options'));
 assert.ok(options.every(o=>o.hidden));assert.equal(options[0].querySelectorAll('button').length,kind==='invoices'?1:2);
 for(const button of options[0].querySelectorAll('button')){assert.equal(button.textContent,'');assert.ok(button.querySelector('svg'));assert.ok(button.title);assert.ok(button.getAttribute('aria-label'));}
 triggers[0].getBoundingClientRect=()=>({top:200,bottom:232,right:500});options[0].getBoundingClientRect=()=>({width:100,height:60});
 triggers[0].click();assert.equal(options[0].parentElement,w.document.body,'popup is outside table layout');assert.equal(options[0].hidden,false);assert.equal(options[0].style.top,'132px');assert.equal(options[0].style.left,'400px');assert.equal(triggers[0].getAttribute('aria-expanded'),'true');
 triggers[1].click();assert.equal(options[0].hidden,true);assert.equal(options[1].hidden,false);
 w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(options[1].hidden,true);assert.equal(w.document.activeElement,triggers[1]);
 triggers[0].click();w.document.body.dispatchEvent(new w.Event('pointerdown',{bubbles:true}));assert.equal(options[0].hidden,true);
 triggers[0].click();w.dispatchEvent(new w.Event('resize'));assert.equal(options[0].hidden,true);
 triggers[0].click();options[0].querySelector('.delete-action').click();assert.equal(confirmed,true);assert.equal(writes,0);assert.equal(options[0].hidden,true);

 }finally{w.close();}
}
console.log('PASS: collapsed actions menus, invoice delete-only, single open menu, Escape dismissal, deletion confirmation preserved');
